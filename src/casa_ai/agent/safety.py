"""Capa de seguridad: clasificacion de riesgo, confirmacion y auditoria.

Este es el punto por el que pasa TODA ejecucion de herramienta. Hace cuatro
cosas, en este orden:

1. Si la herramienta no existe, lo dice sin ejecutar nada.
2. Si es de riesgo alto y la confirmacion esta activada, NO la ejecuta: crea
   una accion pendiente con un token y devuelve al modelo la instruccion de
   pedir confirmacion al usuario. La accion solo se ejecuta cuando el usuario
   confirma en un mensaje posterior y el modelo llama a
   `ejecutar_accion_pendiente` con ese token.
3. Ejecuta el handler y captura los fallos como resultado de error, no como
   excepcion: el agente tiene que poder leer el error y reaccionar.
4. Deja constancia en la auditoria de todo lo que se ejecuta.

El paso 2 es lo que hace que sea seguro dar a un modelo la llave de la casa.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..adapters.base import AdapterError
from .registry import Contexto, Herramienta, Registro, Riesgo

log = logging.getLogger(__name__)

# Nombre reservado: es la herramienta que consuma una accion pendiente.
TOOL_CONFIRMAR = "ejecutar_accion_pendiente"


class Ejecutor:
    def __init__(self, registro: Registro, ctx: Contexto) -> None:
        self.registro = registro
        self.ctx = ctx

    async def ejecutar(self, nombre: str, argumentos: dict[str, Any]) -> tuple[Any, bool]:
        """Ejecuta una herramienta. Devuelve (contenido_para_el_modelo, es_error)."""
        herramienta = self.registro.disponible(nombre, self.ctx)
        if herramienta is None:
            disponibles = ", ".join(h.nombre for h in self.registro.disponibles(self.ctx))
            return (
                f"No existe la herramienta '{nombre}' o no esta disponible para quien "
                f"habla. Disponibles: {disponibles}.",
                True,
            )

        if nombre == TOOL_CONFIRMAR:
            return await self._confirmar(argumentos)

        necesita_confirmacion = (
            herramienta.riesgo is Riesgo.ALTO and self.ctx.settings.exigir_confirmacion
        )
        if necesita_confirmacion:
            return self._pedir_confirmacion(herramienta, argumentos)

        return await self._ejecutar_ya(herramienta, argumentos)

    # --- Confirmacion ----------------------------------------------------
    def _pedir_confirmacion(
        self, herramienta: Herramienta, argumentos: dict[str, Any]
    ) -> tuple[str, bool]:
        resumen = herramienta.resumir(argumentos)

        if self.ctx.confirmacion == "imposible":
            # No hay nadie al otro lado (una rutina programada). No se crea
            # pendiente ni se emite token: emitirlo seria dejar vivo 15 minutos
            # el permiso para una accion fisica que nadie va a autorizar, y
            # ademas ese texto sale por Telegram en el aviso de la rutina.
            self.ctx.store.registrar(
                canal=self.ctx.canal,
                usuario=self.ctx.usuario,
                herramienta=herramienta.nombre,
                argumentos=argumentos,
                riesgo=herramienta.riesgo.value,
                resultado="no ejecutada: sin nadie que la confirme",
            )
            return (
                "ACCION NO EJECUTADA - NO HAY NADIE QUE PUEDA CONFIRMARLA.\n"
                f"Accion propuesta: {resumen}\n\n"
                "Este turno no lo ha pedido una persona, asi que no hay a quien "
                "preguntar. NO vuelvas a intentarlo. Menciona en tu respuesta que "
                "esto haria falta y que alguien tendra que pedirlo, y sigue.",
                False,
            )

        token = self.ctx.store.crear_pendiente(
            canal=self.ctx.canal,
            usuario=self.ctx.usuario,
            conversacion=self.ctx.conversacion,
            herramienta=herramienta.nombre,
            argumentos=argumentos,
            resumen=resumen,
        )
        self.ctx.store.registrar(
            canal=self.ctx.canal,
            usuario=self.ctx.usuario,
            herramienta=herramienta.nombre,
            argumentos=argumentos,
            riesgo=herramienta.riesgo.value,
            # El token NO se registra. La auditoria la puede leer una
            # herramienta de solo lectura y un endpoint HTTP, y el token es la
            # credencial que autoriza una accion fisica: escribirlo aqui seria
            # publicarla durante sus 15 minutos de validez.
            resultado="propuesta, pendiente de confirmacion del usuario",
        )
        # El texto va dirigido al modelo, no al usuario: le dice exactamente
        # que hacer a continuacion para no quedarse colgado ni reintentar.
        if self.ctx.confirmacion_fuera_de_banda:
            # El token no entra en el contexto del modelo. El canal se encarga:
            # ofrece un boton y, al pulsarlo, llama al codigo de confirmacion
            # directamente. Asi una inyeccion de prompt no tiene con que
            # trabajar, ni siquiera un token que reutilizar.
            return (
                "ACCION NO EJECUTADA - REQUIERE CONFIRMACION DEL USUARIO.\n"
                f"Accion propuesta: {resumen}\n\n"
                "Al usuario le va a aparecer un boton para confirmarla o "
                "cancelarla, asi que TU no tienes que hacer nada mas: explicale "
                "en una frase que vas a hacer y que consecuencia tiene, y para.\n"
                "NO vuelvas a llamar a esta herramienta: repetirla solo crea "
                "propuestas duplicadas y al usuario le saldria un boton por cada "
                "una.",
                False,
            )

        return (
            "ACCION NO EJECUTADA - REQUIERE CONFIRMACION DEL USUARIO.\n"
            f"Accion propuesta: {resumen}\n"
            f"token: {token}\n\n"
            "Explica al usuario en una frase que vas a hacer y pidele que lo "
            "confirme. NO vuelvas a llamar a esta herramienta. Cuando el usuario "
            f"confirme, llama a {TOOL_CONFIRMAR} con este token. Si dice que no, "
            "no llames a nada y confirmale que lo has cancelado.",
            False,
        )

    async def _tomar_y_ejecutar(
        self, token: str, *, exige_turno_nuevo: bool, via: str, si_no_vale: str
    ) -> tuple[Any, bool]:
        """El cuerpo comun de las dos confirmaciones.

        `exige_turno_nuevo` es el unico bit que de verdad cambia entre ellas, y
        las dos entradas publicas siguen existiendo porque son dos llamantes
        distintos: el modelo y el canal.
        """
        pendiente, motivo = self.ctx.store.tomar_pendiente(
            token,
            canal=self.ctx.canal,
            usuario=self.ctx.usuario,
            conversacion=self.ctx.conversacion,
            exige_turno_nuevo=exige_turno_nuevo,
        )
        if pendiente is None:
            return si_no_vale.format(motivo=motivo), True
        herramienta = self.registro.get(pendiente["herramienta"])
        if herramienta is None:  # pragma: no cover - solo si cambia el codigo
            return (f"La herramienta '{pendiente['herramienta']}' ya no existe.", True)
        log.info("Confirmada %s: %s (%s)", via, pendiente["resumen"], self.ctx.usuario)
        return await self._ejecutar_ya(
            herramienta, pendiente["argumentos"], confirmada=True
        )

    async def _confirmar(self, argumentos: dict[str, Any]) -> tuple[Any, bool]:
        """La confirmacion que pide el modelo, con el token que se le dio."""
        return await self._tomar_y_ejecutar(
            str(argumentos.get("token", "")).strip(),
            exige_turno_nuevo=True,
            via="por el modelo",
            si_no_vale=(
                "No se puede ejecutar esa accion pendiente: {motivo}. "
                "No reintentes con el mismo token. Si el usuario sigue queriendo "
                "la accion, esperale y vuelve a proponerla desde el principio."
            ),
        )

    async def confirmar_fuera_de_banda(self, token: str) -> tuple[Any, bool]:
        """Ejecuta una accion pendiente porque un humano pulso un boton.

        Este camino no pasa por el modelo: lo llama el canal. Por eso no exige
        un turno nuevo, la pulsacion es en si misma la prueba de que hablo un
        humano, que es lo que el guardia de turno intenta establecer.
        """
        return await self._tomar_y_ejecutar(
            token,
            exige_turno_nuevo=False,
            via="con boton",
            si_no_vale="No se puede ejecutar esa accion: {motivo}.",
        )

    # --- Ejecucion real --------------------------------------------------
    async def _ejecutar_ya(
        self, herramienta: Herramienta, argumentos: dict[str, Any], *, confirmada: bool = False
    ) -> tuple[Any, bool]:
        try:
            resultado = await herramienta.handler(self.ctx, **argumentos)
        except AdapterError as e:
            # Fallo esperable del hardware o de la configuracion: el mensaje ya
            # esta redactado para que el agente pueda explicarlo o corregir.
            self._auditar(herramienta, argumentos, f"ERROR: {e}", error=True, confirmada=confirmada)
            return (str(e), True)
        except TypeError as e:
            self._auditar(
                herramienta, argumentos, f"ARGUMENTOS: {e}", error=True, confirmada=confirmada
            )
            return (f"Argumentos incorrectos para {herramienta.nombre}: {e}", True)
        except Exception as e:  # noqa: BLE001 - frontera con el hardware
            log.exception("Fallo inesperado en %s", herramienta.nombre)
            self._auditar(
                herramienta, argumentos, f"EXCEPCION: {e}", error=True, confirmada=confirmada
            )
            return (
                f"Fallo inesperado ejecutando {herramienta.nombre}: {type(e).__name__}: {e}",
                True,
            )

        self._auditar(herramienta, argumentos, _resumir(resultado), confirmada=confirmada)
        return (resultado, False)

    def _auditar(
        self,
        herramienta: Herramienta,
        argumentos: dict[str, Any],
        resultado: str,
        *,
        error: bool = False,
        confirmada: bool = False,
    ) -> None:
        if herramienta.riesgo is Riesgo.LECTURA and not error:
            # Las lecturas no se auditan: son inocuas y llenarian la tabla.
            return
        self.ctx.store.registrar(
            canal=self.ctx.canal,
            usuario=self.ctx.usuario,
            herramienta=herramienta.nombre,
            argumentos=argumentos,
            riesgo=herramienta.riesgo.value + (" (confirmada)" if confirmada else ""),
            resultado=resultado,
            error=error,
        )


def _resumir(resultado: Any) -> str:
    if isinstance(resultado, str):
        return resultado
    if isinstance(resultado, list) and any(
        isinstance(b, dict) and b.get("type") == "image" for b in resultado
    ):
        return "[imagen devuelta al modelo]"
    try:
        return json.dumps(resultado, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(resultado)


def contenido_para_api(resultado: Any) -> Any:
    """Convierte el resultado de un handler en contenido de tool_result.

    Los dicts y listas se serializan a JSON, que es lo que mejor lee el modelo.
    Una lista de bloques de contenido (imagenes de camara) se pasa tal cual.
    """
    if isinstance(resultado, str):
        return resultado
    if isinstance(resultado, list) and resultado and all(
        isinstance(b, dict) and "type" in b for b in resultado
    ):
        return resultado
    return json.dumps(resultado, ensure_ascii=False, indent=2, default=str)
