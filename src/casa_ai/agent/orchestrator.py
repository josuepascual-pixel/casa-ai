"""Bucle del agente contra la Messages API de Claude.

Se usa un bucle manual en vez del tool runner del SDK por una razon concreta:
la confirmacion de acciones de riesgo cruza turnos. El usuario dice "fuerza
carga de bateria", el sistema responde pidiendo confirmacion, y el "si" llega
en un mensaje POSTERIOR, minutos despues, desde Telegram. El tool runner cierra
el bucle cuando no hay mas llamadas a herramientas, asi que ese "si" tiene que
entrar como un turno nuevo con el historial completo detras. El bucle manual
nos deja controlar eso, ademas de la persistencia del historial y la auditoria.

Detalles de la llamada:

* `thinking` adaptativo: el modelo decide cuanto razonar. Un "pon musica" no
  gasta razonamiento; un "optimiza la bateria para manana" si.
* Cache de prompt: el prompt del sistema mas el inventario de la casa van en un
  bloque cacheado con TTL de 1 hora. Las herramientas se renderizan antes del
  system, asi que el registro las entrega SIEMPRE en el mismo orden; si el
  orden variara, la cache se invalidaria en cada mensaje.
* La hora actual se inyecta en el turno del usuario, nunca en el system: si
  estuviera en el prefijo cacheado, cada mensaje seria un fallo de cache.
* `fallbacks` del servidor: si un clasificador de seguridad rechaza la
  peticion, la API la reencamina en vez de devolver nada.
* Busqueda web: es una herramienta del SERVIDOR. Va al final de la lista de
  `tools` (orden estable, por la cache) y la ejecuta la propia API: el bucle
  no ve `tool_use` de ella, solo bloques `server_tool_use` y
  `web_search_tool_result`, que se devuelven en el historial tal cual.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

import anthropic

from ..settings import Settings
from ..tiempo import ahora
from .prompts import construir_system
from .registry import Contexto, Registro
from .safety import Ejecutor, contenido_para_api

log = logging.getLogger(__name__)

BETA_FALLBACKS = "server-side-fallback-2026-07-01"
BUSQUEDA_WEB = "web_search_20260209"
MAX_VUELTAS = 12
MAX_REINICIOS_PAUSA = 3

OnTexto = Callable[[str], Awaitable[None]]


def crear_cliente(settings: Settings) -> anthropic.AsyncAnthropic:
    """Un cliente de la API.

    Construirlo cuesta ~66 ms de CPU (dos contextos SSL) y estrena el pool
    TLS, asi que se hace una vez por proceso y no una por mensaje: quien lleva
    el `ctx` de cada turno es el `Agente`, no el cliente.
    """
    return anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key or None,
        max_retries=3,
    )


class Agente:
    # Se apaga para todo el proceso cuando la API dice que la organizacion no
    # tiene la busqueda web habilitada: es un ajuste de la consola de
    # Anthropic, no cambia entre turnos, y asi no se paga una peticion fallida
    # por cada mensaje.
    busqueda_web_disponible: ClassVar[bool] = True

    def __init__(
        self,
        settings: Settings,
        registro: Registro,
        ctx: Contexto,
        cliente: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self._s = settings
        self._registro = registro
        self._ctx = ctx
        self._ejecutor = Ejecutor(registro, ctx)
        self._system = construir_system(settings, ctx.inventario)
        self._propio = cliente is None
        self._cliente = cliente if cliente is not None else crear_cliente(settings)
        # Se apaga solo si la cuenta no tiene el beta de fallbacks habilitado.
        self._usar_fallbacks = settings.fallbacks_servidor

    async def cerrar(self) -> None:
        """Cierra el cliente solo si es suyo: el prestado lo cierra su dueno."""
        if self._propio:
            await self._cliente.close()

    # --- Turno completo ---------------------------------------------------
    async def responder(
        self,
        conversacion: str,
        entrada: str | list[dict[str, Any]],
        *,
        on_texto: OnTexto | None = None,
    ) -> str:
        """Procesa un mensaje del usuario y devuelve la respuesta final en texto.

        `entrada` puede ser texto o una lista de bloques de contenido (por
        ejemplo una foto enviada por Telegram junto a una pregunta).
        """
        store = self._ctx.store
        # Marca que ha llegado un mensaje HUMANO. Es la referencia que impide
        # que una accion de riesgo se proponga y se confirme en la misma vuelta:
        # el token solo vale en un turno posterior a este.
        store.nuevo_turno(conversacion)
        historial = store.historial(conversacion)

        bloques: list[dict[str, Any]] = (
            [{"type": "text", "text": entrada}] if isinstance(entrada, str) else list(entrada)
        )
        momento = ahora(self._s)
        contexto = f"{momento.strftime('%A %d/%m/%Y %H:%M')} hora local"
        # Quien habla va aqui y no en el system prompt por lo mismo que la
        # hora: cambia por conversacion y romperia la cache.
        if self._ctx.persona is not None:
            contexto += f". Habla {self._ctx.persona.nombre} ({self._ctx.persona.nivel})"
        bloques.append({"type": "text", "text": f"[Contexto: {contexto}]"})

        mensajes: list[dict[str, Any]] = [*historial, {"role": "user", "content": bloques}]
        store.anadir_mensaje(conversacion, "user", bloques)

        herramientas = self._herramientas()
        texto_final = ""
        reinicios_pausa = 0

        for _vuelta in range(MAX_VUELTAS):
            respuesta = await self._llamar(mensajes, herramientas, on_texto)

            if respuesta.stop_reason == "refusal":
                motivo = getattr(respuesta, "stop_details", None)
                categoria = getattr(motivo, "category", None) if motivo else None
                log.warning("Peticion rechazada por el clasificador (%s)", categoria)
                return "No he podido procesar esa peticion. Pruebalo de otra manera."

            # Sin los None: un bloque de la API vuelve a la API tal cual, y
            # los campos opcionales vacios (citas, cache) solo estorban.
            contenido = [b.model_dump(exclude_none=True) for b in respuesta.content]
            mensajes.append({"role": "assistant", "content": contenido})
            store.anadir_mensaje(conversacion, "assistant", contenido)

            texto_turno = "".join(
                b.text for b in respuesta.content if getattr(b, "type", None) == "text"
            ).strip()
            if texto_turno:
                texto_final = texto_turno

            # Las llamadas se atienden SIEMPRE, incluso en una pausa: un
            # `tool_use` sin su `tool_result` hace que la API rechace la
            # siguiente peticion y deja la conversacion inservible. Antes la
            # rama de pausa continuaba sin mirarlas.
            llamadas = [b for b in respuesta.content if getattr(b, "type", None) == "tool_use"]
            if llamadas:
                resultados = await self._ejecutar_llamadas(llamadas)
                mensajes.append({"role": "user", "content": resultados})
                store.anadir_mensaje(conversacion, "user", resultados)

            if respuesta.stop_reason == "pause_turn":
                reinicios_pausa += 1
                if reinicios_pausa > MAX_REINICIOS_PAUSA:
                    return (
                        texto_final
                        or "La operacion se ha quedado a medias. Vuelve a pedirmelo."
                    )
                continue

            if not llamadas:
                if respuesta.stop_reason == "max_tokens":
                    texto_final += "\n\n(respuesta cortada por longitud)"
                return texto_final or "Hecho."

        log.warning(
            "Se agoto el limite de %s vueltas en la conversacion %s", MAX_VUELTAS, conversacion
        )
        return (
            texto_final
            or "He dado demasiadas vueltas sin terminar. Concretame un poco mas que necesitas."
        )

    def _herramientas(self) -> list[dict[str, Any]]:
        """Las del registro y, detras, la busqueda web del servidor.

        Detras y no delante por la cache de prompt: `tools` se renderiza
        antes que `system`, asi que el orden tiene que ser el mismo en cada
        mensaje. A un nino no se le ofrece: la lista blanca de herramientas
        es la misma para las que ejecuta la API.
        """
        herramientas = self._registro.definiciones_api(self._ctx)
        if self._s.busqueda_web and Agente.busqueda_web_disponible and not self._ctx.es_nino:
            herramientas.append({
                "type": BUSQUEDA_WEB,
                "name": "web_search",
                "max_uses": self._s.busqueda_web_max_usos,
            })
        return herramientas

    # --- Llamada a la API -------------------------------------------------
    async def _llamar(
        self,
        mensajes: list[dict[str, Any]],
        herramientas: list[dict[str, Any]],
        on_texto: OnTexto | None,
    ) -> Any:
        params: dict[str, Any] = {
            "model": self._s.modelo,
            "max_tokens": self._s.max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": self._system,
                    # TTL de 1 hora: el prompt del sistema y el inventario no
                    # cambian entre mensajes, y una conversacion por Telegram
                    # puede tener huecos largos entre turnos.
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            "messages": mensajes,
            "tools": herramientas,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self._s.esfuerzo},
            # Cachea tambien la cola del historial, que crece en cada turno.
            "cache_control": {"type": "ephemeral"},
        }

        try:
            return await self._llamar_con(params, on_texto)
        except anthropic.BadRequestError as e:
            if not _es_de_busqueda_web(e) or not any(_es_busqueda_web(h) for h in herramientas):
                raise
            log.warning(
                "La busqueda web no esta habilitada para esta organizacion (consola "
                "de Anthropic > Settings); se sigue sin ella: %s", e,
            )
            Agente.busqueda_web_disponible = False
            # In situ: la lista es la del turno entero, y `params` la comparte.
            herramientas[:] = [h for h in herramientas if not _es_busqueda_web(h)]
            return await self._llamar_con(params, on_texto)

    async def _llamar_con(self, params: dict[str, Any], on_texto: OnTexto | None) -> Any:
        if self._usar_fallbacks:
            try:
                return await self._stream(
                    self._cliente.beta.messages,
                    {**params, "betas": [BETA_FALLBACKS], "fallbacks": "default"},
                    on_texto,
                )
            except anthropic.BadRequestError as e:
                # Solo si el 400 es por el beta: otro error (la busqueda web,
                # un parametro) no es culpa de los fallbacks y se propaga.
                if "fallback" not in str(e).lower():
                    raise
                # La cuenta no tiene el beta habilitado: seguimos sin fallbacks
                # en vez de dejar al usuario sin respuesta.
                log.warning("Fallbacks de servidor no disponibles, se desactivan: %s", e)
                self._usar_fallbacks = False

        return await self._stream(self._cliente.messages, params, on_texto)

    @staticmethod
    async def _stream(
        recurso: Any, params: dict[str, Any], on_texto: OnTexto | None
    ) -> Any:
        """Siempre en streaming: evita timeouts y permite ir mostrando texto."""
        async with recurso.stream(**params) as stream:
            if on_texto is not None:
                async for fragmento in stream.text_stream:
                    await on_texto(fragmento)
            return await stream.get_final_message()

    # --- Ejecucion de herramientas ---------------------------------------
    async def _ejecutar_llamadas(self, llamadas: list[Any]) -> list[dict[str, Any]]:
        """Ejecuta las llamadas en paralelo y devuelve TODOS los tool_result juntos.

        Devolverlos en un solo mensaje de usuario es obligatorio: repartirlos en
        varios mensajes le ensena al modelo a dejar de pedir llamadas en
        paralelo, y entonces cada consulta multiple se vuelve secuencial y lenta.
        """
        async def una(llamada: Any) -> dict[str, Any]:
            if isinstance(llamada.input, str):  # defensivo: el input llega como JSON
                try:
                    argumentos = json.loads(llamada.input)
                except json.JSONDecodeError:
                    argumentos = {}
            else:
                argumentos = llamada.input if isinstance(llamada.input, dict) else {}
            log.info("herramienta %s %s", llamada.name, argumentos)
            resultado, es_error = await self._ejecutor.ejecutar(llamada.name, argumentos)
            bloque: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": llamada.id,
                "content": contenido_para_api(resultado),
            }
            if es_error:
                bloque["is_error"] = True
            return bloque

        return list(await asyncio.gather(*(una(ll) for ll in llamadas)))


def _es_busqueda_web(herramienta: dict[str, Any]) -> bool:
    return herramienta.get("type") == BUSQUEDA_WEB


def _es_de_busqueda_web(error: Exception) -> bool:
    texto = str(error).lower()
    return "web_search" in texto or "web search" in texto
