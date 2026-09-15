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
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..tiempo import zona

if TYPE_CHECKING:
    from ..app import Aplicacion
    from ..channels.telegram import BotTelegram

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


class Rutinas:
    def __init__(self, app: Aplicacion, bot: BotTelegram | None) -> None:
        self.app = app
        self.bot = bot
        # La zona resuelta, no la cadena: con un nombre mal escrito
        # AsyncIOScheduler lanza dentro del lifespan y no arranca el backend
        # entero. `tiempo.zona()` degrada a UTC, que es lo que hace el resto
        # del sistema.
        self.scheduler = AsyncIOScheduler(timezone=zona(app.settings))

    def iniciar(self, *, hora_informe: int = 8, minutos_vigilancia: int = 30) -> None:
        """Arranca el planificador. Hace falta un bucle de eventos corriendo."""
        if not self._destinos():
            log.info("Rutinas proactivas desactivadas: no hay chats de Telegram autorizados.")
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
        return self.app.settings.chats_telegram if self.bot is not None else set()

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
        if not altavoz:
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
