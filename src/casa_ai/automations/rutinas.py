"""Rutinas proactivas: el agente que habla primero.

Un asistente que solo responde cuando le preguntas se queda a medias. Estas
rutinas son turnos de agente lanzados por reloj, con las mismas herramientas y
la misma capa de seguridad, cuyo resultado se envia por Telegram.

Nota importante: las rutinas NO pueden ejecutar acciones de riesgo alto. No es
una limitacion tecnica sino de diseno: una accion de riesgo pide confirmacion
humana, y a las 8 de la manana no hay nadie al otro lado para confirmar. Si una
rutina detecta algo que requiere una accion de riesgo, avisa y propone; tu
decides desde el chat.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..tiempo import formatear, zona
from ..tools.programar import siguiente_repeticion

if TYPE_CHECKING:
    from ..app import Aplicacion
    from ..channels.telegram import BotTelegram
    from ..channels.whatsapp import CanalWhatsApp

log = logging.getLogger(__name__)

PROMPT_INFORME = """\
Genera el informe de la manana para el dueno de la casa. Consulta el estado
real de los sistemas (usa informe_casa) y resume en 6 lineas como maximo:

- Energia: carga de la bateria y si vienes de una noche con mucho consumo de red.
- Algo que este raro: un equipo de red caido, una camara desconectada, la
  bateria muy baja, temperatura de bateria anomala.
- Una sola recomendacion practica para hoy, si la hay.

Si todo esta normal, dilo en una linea y no rellenes.
"""

PROMPT_VIGILANCIA = """\
Revisa el estado de la casa buscando SOLO problemas que merezcan una
interrupcion: bateria por debajo del 15%, temperatura de bateria fuera de
0-45 C, algun equipo de red caido, o alguna camara desconectada.

Si no hay nada de eso, responde exactamente: SIN NOVEDAD

Si hay algo, describelo en una o dos frases y di que harias.
"""

SIN_NOVEDAD = "SIN NOVEDAD"

PROMPT_PROGRAMADA = """\
[Orden programada] Es la hora de algo que dejaste programado el {creado}:
«{orden}». Hazlo ahora con tus herramientas y responde en una o dos lineas
con lo que has hecho de verdad. Si es un recordatorio, escribe el aviso tal
cual, sin adornos. Si hace falta una accion de riesgo alto, no la puedes
ejecutar sin nadie delante: dilo y deja claro que hay que pedirla en el chat.
"""


class Rutinas:
    def __init__(
        self,
        app: Aplicacion,
        bot: BotTelegram | None,
        whatsapp: CanalWhatsApp | None = None,
    ) -> None:
        self.app = app
        self.bot = bot
        self.whatsapp = whatsapp
        # La zona resuelta, no la cadena: con un nombre mal escrito
        # AsyncIOScheduler lanza dentro del lifespan y no arranca el backend
        # entero. `tiempo.zona()` degrada a UTC, que es lo que hace el resto
        # del sistema.
        self.scheduler = AsyncIOScheduler(timezone=zona(app.settings))

    def iniciar(self, *, hora_informe: int = 8, minutos_vigilancia: int = 30) -> None:
        """Arranca el planificador. Hace falta un bucle de eventos corriendo."""
        if self.bot is not None or self.whatsapp is not None:
            # Las ordenes programadas por chat: un vistazo por minuto a las
            # que han vencido. Sin canal por el que avisar no tiene sentido,
            # y la herramienta tampoco se ofrece.
            self.scheduler.add_job(
                self._programadas,
                IntervalTrigger(minutes=1),
                id="programaciones",
                replace_existing=True,
                max_instances=1,
            )
        if not self._destinos():
            log.info("Rutinas proactivas desactivadas: no hay chats de Telegram autorizados.")
            if self.scheduler.get_jobs():
                self.scheduler.start()
            return
        self.scheduler.add_job(
            self._informe,
            CronTrigger(hour=hora_informe, minute=0),
            id="informe_matinal",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self._vigilancia,
            CronTrigger(minute=f"*/{minutos_vigilancia}"),
            id="vigilancia",
            replace_existing=True,
        )
        self.scheduler.start()
        log.info(
            "Rutinas activas: informe diario a las %02d:00 y vigilancia cada %s min",
            hora_informe,
            minutos_vigilancia,
        )

    def detener(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _destinos(self) -> set[int]:
        """Los chats autorizados que no son de un nino.

        El informe corre como dueno y cuenta camaras, red y quien esta en
        casa: justo lo que a un nino no se le da.
        """
        if self.bot is None:
            return set()
        inventario = self.app.inventario
        salida = set()
        for chat in self.app.settings.chats_telegram:
            persona = inventario.persona_de("telegram", str(chat))
            if persona is None or not persona.es_nino:
                salida.add(chat)
        return salida

    async def _turno(self, prompt: str, conversacion: str) -> str:
        # Conversacion propia y efimera: una rutina no debe heredar el hilo del
        # chat ni contaminarlo con sus propios turnos.
        self.app.store.limpiar_conversacion(conversacion)
        return await self.app.responder(
            canal="rutina",
            usuario="programada",
            conversacion=conversacion,
            entrada=prompt,
            # A las 8 de la manana no hay nadie para confirmar. Declararlo aqui
            # es lo que hace que el Ejecutor no emita token ni cree pendiente:
            # antes se apoyaba solo en el guardia de turno, asi que el token
            # existia y salia por Telegram dentro del texto de la rutina.
            confirmacion="imposible",
        )

    async def _informe(self) -> None:
        try:
            texto = await self._turno(PROMPT_INFORME, "rutina:informe")
            # Telegram y el altavoz no dependen uno del otro: con Telegram
            # lento, el informe hablado no tiene por que esperar.
            await asyncio.gather(
                self._difundir(f"☀️ Buenos dias\n\n{texto}"), self._leer_en_voz_alta(texto)
            )
        except Exception:  # noqa: BLE001 - una rutina que falla no tumba el proceso
            log.exception("Fallo el informe matinal")

    async def _leer_en_voz_alta(self, texto: str) -> None:
        """El informe por el altavoz de la cocina, si esta configurado.

        Solo el informe: la vigilancia avisa a cualquier hora y por el altavoz
        seria despertar a la casa.
        """
        altavoz = self.app.settings.rutinas_altavoz
        if not altavoz or self.app.store.bloqueo():
            # Bloqueada la casa, ni el altavoz: era lo unico que se saltaba
            # el Ejecutor, y el bloqueo tiene que ser total.
            return
        try:
            await self.app.ctx.ha.hablar(texto, self.app.inventario.resolver_alias(altavoz))
        except Exception:  # noqa: BLE001
            log.exception("No se pudo leer el informe por %s", altavoz)

    async def _vigilancia(self) -> None:
        try:
            texto = await self._turno(PROMPT_VIGILANCIA, "rutina:vigilancia")
            if SIN_NOVEDAD in texto.upper():
                log.debug("Vigilancia: sin novedad")
                return
            await self._difundir(f"⚠️ Aviso de la casa\n\n{texto}")
        except Exception:  # noqa: BLE001
            log.exception("Fallo la vigilancia periodica")

    async def _difundir(self, texto: str) -> None:
        if self.bot is None or self.bot.application is None:
            log.info("Sin canal para difundir: %s", texto)
            return
        for chat_id in self._destinos():
            try:
                await self.bot.application.bot.send_message(chat_id=chat_id, text=texto)
            except Exception:  # noqa: BLE001
                log.exception("No se pudo enviar el aviso al chat %s", chat_id)

    # --- Ordenes programadas por chat ------------------------------------
    async def _programadas(self, ahora: float | None = None) -> None:
        """Ejecuta las que han vencido, cada una con la identidad de quien la pidio."""
        momento = time.time() if ahora is None else ahora
        for p in self.app.store.programaciones_vencidas(momento):
            # Primero se reprograma o se apaga: si el turno fallara a medias,
            # no se repetiria cada minuto hasta el infinito.
            self.app.store.reprogramar(p["id"], self._siguiente(p, momento))
            try:
                await self._ejecutar_programada(p)
            except Exception:  # noqa: BLE001 - una orden que falla no tumba el resto
                log.exception("Fallo la orden programada %s", p["id"])

    def _siguiente(self, p: dict[str, Any], momento: float) -> float | None:
        if not p.get("hora"):
            return None
        desde = datetime.fromtimestamp(momento, zona(self.app.settings))
        return siguiente_repeticion(desde, p["hora"], p["dias"] or "diario").timestamp()

    async def _ejecutar_programada(self, p: dict[str, Any]) -> None:
        conversacion = f"programada:{p['id']}"
        self.app.store.limpiar_conversacion(conversacion)
        texto = await self.app.responder(
            # Con el canal y el usuario de quien la pidio: mismos permisos, y
            # un nino sigue siendo un nino a las 8 de la manana.
            canal=p["canal"],
            usuario=p["usuario"],
            conversacion=conversacion,
            entrada=PROMPT_PROGRAMADA.format(
                creado=formatear(p["creado"], self.app.settings, "%d/%m %H:%M"),
                orden=p["orden"],
            ),
            confirmacion="imposible",
        )
        await self._enviar(p["conversacion"], f"⏰ {texto}")

    async def _enviar(self, conversacion: str, texto: str) -> None:
        """Al chat donde se pidio: el id de conversacion lleva el canal y el destino."""
        canal, _, destino = conversacion.partition(":")
        try:
            if canal == "telegram" and self.bot is not None and self.bot.application is not None:
                await self.bot.application.bot.send_message(chat_id=int(destino), text=texto)
            elif canal == "whatsapp" and self.whatsapp is not None:
                await self.whatsapp._enviar(destino, texto)
            else:
                log.info("Sin canal para entregar la orden de %s: %s", conversacion, texto)
        except Exception:  # noqa: BLE001
            log.exception("No se pudo entregar la orden programada a %s", conversacion)
