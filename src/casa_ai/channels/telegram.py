"""Canal de Telegram: texto, notas de voz y fotos.

Es el canal principal del dia a dia: se le habla desde el movil, dentro o
fuera de casa, y las notas de voz se transcriben en local.

Autorizacion: solo responden los chat_id listados en
TELEGRAM_CHATS_AUTORIZADOS. Si la lista esta vacia, el bot no atiende a nadie.
Eso es deliberado: un bot de Telegram es alcanzable por cualquiera que
adivine su nombre, y este bot puede apagar la casa.
"""

from __future__ import annotations

import logging
import tempfile
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..app import Aplicacion
from ..bloques import imagen_jpeg
from ..stt import TranscripcionError, transcribir
from ..tiempo import formatear
from .comun import CANCELAR, CONFIRMAR, codificar, partir

log = logging.getLogger(__name__)

CANAL = "telegram"
LIMITE_MENSAJE = 4096

Handler = Callable[["BotTelegram", Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def solo_autorizados(handler: Handler) -> Handler:
    """Rechaza el update si el chat no esta en la lista.

    Es decorador y no tres lineas al principio de cada handler porque son
    siete handlers y la omision en uno nuevo no la detecta nada: en `_cmd_start`
    el `assert` ya se habia colado ANTES de la comprobacion.
    """

    @wraps(handler)
    async def envoltorio(
        self: BotTelegram, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._autorizado(update):
            await self._rechazar(update)
            return
        if update.message is None:
            return
        await handler(self, update, ctx)

    return envoltorio


class BotTelegram:
    def __init__(self, app: Aplicacion) -> None:
        self.app = app
        self.application: Application | None = None
        # Ultimo aviso por chat desconocido: uno por hora, no uno por mensaje.
        self._avisados: dict[int, float] = {}

    # --- Ciclo de vida ---------------------------------------------------
    def construir(self) -> Application:
        token = self.app.settings.telegram_token
        if not token:
            raise RuntimeError("TELEGRAM_TOKEN no configurado")

        application = ApplicationBuilder().token(token).build()
        application.add_handler(CommandHandler("start", self._cmd_start))
        application.add_handler(CommandHandler("estado", self._cmd_estado))
        application.add_handler(CommandHandler("reset", self._cmd_reset))
        application.add_handler(CommandHandler("auditoria", self._cmd_auditoria))
        application.add_handler(CommandHandler("verificar", self._cmd_verificar))
        application.add_handler(CommandHandler("descubrir", self._cmd_descubrir))
        application.add_handler(CommandHandler("sondear", self._cmd_sondear))
        application.add_handler(CommandHandler("bloquear", self._cmd_bloquear))
        application.add_handler(CommandHandler("desbloquear", self._cmd_desbloquear))
        application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self._voz))
        application.add_handler(MessageHandler(filters.PHOTO, self._foto))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._texto))
        application.add_handler(CallbackQueryHandler(self._boton))
        application.add_error_handler(self._on_error)
        self.application = application
        return application

    async def iniciar(self) -> None:
        application = self.construir()
        await application.initialize()
        await application.start()
        if application.updater is not None:
            # Polling: no hace falta abrir puertos ni certificados hacia fuera,
            # que es lo sensato para un backend que vive en la LAN de casa.
            await application.updater.start_polling(drop_pending_updates=True)
        autorizados = self.app.settings.chats_telegram
        log.info("Bot de Telegram activo. Chats autorizados: %s", autorizados or "NINGUNO")
        avisos = self.app.settings.avisos_de_seguridad()
        if avisos:
            # Que lo vea el dueno en el movil, no solo quien lea el registro.
            await self._avisar_duenos(
                "🔓 He arrancado, pero la configuracion deja la casa expuesta:\n\n"
                + "\n\n".join(f"• {aviso}" for aviso in avisos)
            )

    async def detener(self) -> None:
        if self.application is None:
            return
        if self.application.updater is not None:
            await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()

    # --- Autorizacion ----------------------------------------------------
    def _autorizado(self, update: Update) -> bool:
        chat = update.effective_chat
        if chat is None:
            return False
        permitidos = self.app.settings.chats_telegram
        if not permitidos:
            log.warning(
                "Mensaje rechazado: no hay chats autorizados. Tu chat_id es %s; "
                "anadelo a TELEGRAM_CHATS_AUTORIZADOS.",
                chat.id,
            )
            return False
        return chat.id in permitidos

    async def _rechazar(self, update: Update) -> None:
        chat = update.effective_chat
        # Con una pulsacion de boton no hay `message`: ese caso lo contesta
        # _boton editando el mensaje del teclado.
        if chat is not None and update.message is not None:
            await update.message.reply_text(
                f"No estas autorizado. Tu chat_id es {chat.id}; si eres el dueno "
                "de la casa, anadelo a TELEGRAM_CHATS_AUTORIZADOS y reinicia."
            )
        if chat is not None:
            await self._avisar_intento(chat)

    async def _avisar_intento(self, chat: Any) -> None:
        """Que el dueno se entere de que alguien ha llamado a la puerta.

        El bot es publico por construccion (cualquiera que sepa su nombre le
        puede escribir); lo que no puede ser es que eso pase en silencio.
        """
        ahora = time.monotonic()
        if ahora - self._avisados.get(chat.id, -1e9) < 3600 or self.application is None:
            return
        self._avisados[chat.id] = ahora
        quien = " ".join(
            x for x in (getattr(chat, "first_name", None), getattr(chat, "username", None)) if x
        )
        texto = (
            f"⚠️ Alguien ha intentado hablar conmigo desde un chat no autorizado: "
            f"{chat.id}{f' ({quien})' if quien else ''}. Lo he rechazado."
        )
        await self._avisar_duenos(texto)

    async def _avisar_duenos(self, texto: str) -> None:
        if self.application is None:
            return
        for dueno in self.app.chats_de_duenos():
            try:
                for trozo in partir(texto, LIMITE_MENSAJE):
                    await self.application.bot.send_message(chat_id=dueno, text=trozo)
            except Exception:  # noqa: BLE001 - avisar no puede tumbar lo que lo llamo
                log.exception("No se pudo avisar al chat %s", dueno)

    @staticmethod
    def _usuario(update: Update) -> str:
        """Quien habla. En un grupo (id negativo) es la persona, no el grupo.

        La autorizacion sigue siendo por chat, pero la persona, sus permisos y
        los botones de confirmacion van con quien escribio: si no, un invitado
        anadido al grupo heredaria el nivel del dueno y podria pulsar el boton
        de una accion que propuso otro.
        """
        chat = update.effective_chat
        assert chat is not None
        if chat.id < 0 and update.effective_user is not None:
            return str(update.effective_user.id)
        return str(chat.id)

    # --- Nucleo ----------------------------------------------------------
    async def _procesar(self, update: Update, entrada: Any) -> None:
        chat = update.effective_chat
        assert chat is not None and update.message is not None
        conversacion = f"{CANAL}:{chat.id}"

        await update.message.chat.send_action(ChatAction.TYPING)
        respuesta = await self.app.responder(
            canal=CANAL,
            usuario=self._usuario(update),
            conversacion=conversacion,
            entrada=entrada,
            # Este canal tiene botones, asi que el token de confirmacion no
            # necesita pasar por el contexto del modelo.
            confirmacion="boton",
        )
        for trozo in partir(respuesta, LIMITE_MENSAJE):
            await update.message.reply_text(trozo)

        await self._ofrecer_botones(update, conversacion)

    async def _ofrecer_botones(self, update: Update, conversacion: str) -> None:
        """Manda un boton por cada accion de riesgo que quedo propuesta.

        Es lo que cierra el circulo: el modelo explica lo que quiere hacer,
        pero la orden la da el usuario pulsando, y el token va en el
        callback_data, no en nada que el modelo lea.
        """
        assert update.message is not None
        for pendiente in self.app.pendientes_para_ofrecer(conversacion):
            teclado = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✅ Confirmar", callback_data=codificar(CONFIRMAR, pendiente["token"])
                ),
                InlineKeyboardButton(
                    "✖️ Cancelar", callback_data=codificar(CANCELAR, pendiente["token"])
                ),
            ]])
            await update.message.reply_text(
                f"⚠️ {pendiente['resumen']}", reply_markup=teclado
            )

    async def _boton(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Pulsacion de un boton de confirmacion.

        Este camino NO pasa por el modelo: la pulsacion es la prueba de que un
        humano ha dicho si, que es justo lo que la comprobacion de turno
        intenta establecer cuando la confirmacion va por conversacion.
        """
        consulta = update.callback_query
        chat = update.effective_chat
        if consulta is None or chat is None:
            return

        await consulta.answer()

        if not self._autorizado(update):
            await consulta.edit_message_text("No estas autorizado.")
            return

        texto = await self.app.resolver_pulsacion(
            consulta.data or "",
            canal=CANAL,
            usuario=self._usuario(update),
            conversacion=f"{CANAL}:{chat.id}",
        )
        if texto is None:
            log.warning("Pulsacion de Telegram no reconocida")
            return
        texto = texto[:LIMITE_MENSAJE]
        try:
            await consulta.edit_message_text(texto)
        except Exception:  # noqa: BLE001 - la accion YA se ejecuto: hay que contarlo
            log.warning("No se pudo editar el mensaje del boton; se responde aparte")
            if update.effective_message is not None:
                await update.effective_message.reply_text(texto)

    @solo_autorizados
    async def _texto(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        assert update.message is not None
        await self._procesar(update, update.message.text or "")

    @solo_autorizados
    async def _voz(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        assert update.message is not None
        audio = update.message.voice or update.message.audio
        if audio is None:
            return
        await update.message.chat.send_action(ChatAction.TYPING)
        fichero = await audio.get_file()
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "nota.ogg"
            await fichero.download_to_drive(destino)
            try:
                texto = await transcribir(destino, self.app.settings)
            except TranscripcionError as e:
                await update.message.reply_text(str(e))
                return
        log.debug("Nota de voz transcrita: %s", texto)
        # Se muestra la transcripcion para que el usuario vea que se entendio
        # antes de que el agente actue sobre ello.
        await update.message.reply_text(f"🎙 «{texto}»")
        await self._procesar(update, texto)

    @solo_autorizados
    async def _foto(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        assert update.message is not None
        foto = update.message.photo[-1]  # la de mayor resolucion
        fichero = await foto.get_file()
        datos = bytes(await fichero.download_as_bytearray())
        await self._procesar(
            update,
            imagen_jpeg(datos, update.message.caption or "Que ves en esta foto?"),
        )

    # --- Comandos --------------------------------------------------------
    @solo_autorizados
    async def _cmd_start(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        assert update.message is not None and chat is not None
        persona = self.app.inventario.persona_de(CANAL, self._usuario(update))
        saludo = f"{self.app.nombre_asistente} a su servicio"
        if persona is not None:
            saludo += f", {persona.nombre}"
        await update.message.reply_text(
            f"{saludo}.\n\n"
            "Hablame normal, por texto o nota de voz:\n"
            "• «cuanto esta produciendo el sol?»\n"
            "• «pon la lista de la cocina y sube el volumen»\n"
            "• «hay alguien en la puerta?»\n"
            "• «quien esta conectado al wifi?»\n\n"
            "Las acciones con consecuencias (bateria, wifi, bus KNX) no se\n"
            "ejecutan solas: te saldra un boton para confirmarlas.\n\n"
            "/estado subsistemas conectados\n"
            "/verificar lee de verdad cada subsistema y dice que falla\n"
            "/descubrir busca los aparatos en la red y escribe su configuracion\n"
            "/sondear la planta Sungrow: que equipos responden tras el Logger\n"
            "/auditoria ultimas acciones ejecutadas\n"
            "/bloquear emergencia: solo consultas hasta /desbloquear (solo el dueno)\n"
            "/reset olvida la conversacion"
        )

    @solo_autorizados
    async def _cmd_estado(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        assert update.message is not None and chat is not None
        cfg = self.app.resumen_configuracion()
        lineas = [
            f"{'✅' if v else '❌'} {k}"
            for k, v in cfg.items()
            if isinstance(v, bool)
        ]
        persona = self.app.inventario.persona_de(CANAL, self._usuario(update))
        if persona is not None:
            lineas.append(f"\nHablas como {persona.nombre} ({persona.nivel}).")
        if (bloqueo := self.app.store.bloqueo()) is not None:
            lineas.append(f"\n🔒 CASA BLOQUEADA por {bloqueo['quien']}. /desbloquear para abrirla.")
        herramientas = cfg.get("herramientas_activas", [])
        assert isinstance(herramientas, list)
        lineas.append(f"\n{len(herramientas)} herramientas activas.")
        ausentes = cfg.get("herramientas_ausentes", {})
        assert isinstance(ausentes, dict)
        if ausentes:
            lineas.append("\nNo disponibles:")
            lineas += [f"• {n}: {motivo}" for n, motivo in ausentes.items()]
        await update.message.reply_text(partir("\n".join(lineas), LIMITE_MENSAJE)[0])

    def _es_nino(self, update: Update) -> bool:
        chat = update.effective_chat
        persona = self.app.inventario.persona_de(CANAL, self._usuario(update)) if chat else None
        return persona is not None and persona.es_nino

    @solo_autorizados
    async def _cmd_verificar(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Lectura real de cada subsistema. Es la puesta en marcha desde el movil."""
        assert update.message is not None
        if self._es_nino(update):
            await update.message.reply_text("Eso es cosa de los mayores.")
            return
        await update.message.reply_text("Comprobando cada sistema, un momento…")
        texto = await self.app.comprobar()
        for trozo in partir(texto, LIMITE_MENSAJE):
            await update.message.reply_text(trozo)

    @solo_autorizados
    async def _cmd_descubrir(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Barre la red y devuelve los bloques de configuracion para pegar."""
        assert update.message is not None
        if self._es_nino(update):
            await update.message.reply_text("Eso es cosa de los mayores.")
            return
        partes = (update.message.text or "").split(maxsplit=1)
        red = partes[1].strip() if len(partes) > 1 else None
        await update.message.reply_text("Buscando aparatos en la red, tarda unos segundos…")
        texto = await self.app.descubrir(red)
        for trozo in partir(texto, LIMITE_MENSAJE):
            await update.message.reply_text(trozo)

    @solo_autorizados
    async def _cmd_sondear(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Sondeo de la planta Sungrow: `/sondear`, `/sondear leer`, `/sondear barrer 2 0 50`."""
        assert update.message is not None
        if self._es_nino(update):
            await update.message.reply_text("Eso es cosa de los mayores.")
            return
        partes = (update.message.text or "").split(maxsplit=1)
        await update.message.reply_text("Sondeando la planta, puede tardar un minuto…")
        texto = await self.app.sondear_planta(partes[1] if len(partes) > 1 else "sondear")
        for trozo in partir(texto, LIMITE_MENSAJE):
            await update.message.reply_text(trozo)

    @solo_autorizados
    async def _cmd_bloquear(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Interruptor de emergencia: solo consultas hasta /desbloquear."""
        assert update.message is not None
        if not self.app.es_dueno(CANAL, self._usuario(update)):
            await update.message.reply_text("Solo el dueno puede bloquear la casa.")
            return
        self.app.store.bloquear(self._usuario(update))
        await update.message.reply_text(
            "Casa bloqueada. No se ejecuta ninguna accion por ningun canal, solo "
            "consultas, y las propuestas pendientes quedan canceladas. /desbloquear "
            "para volver a la normalidad."
        )

    @solo_autorizados
    async def _cmd_desbloquear(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        assert update.message is not None
        if not self.app.es_dueno(CANAL, self._usuario(update)):
            await update.message.reply_text("Solo el dueno puede desbloquear la casa.")
            return
        self.app.store.desbloquear()
        await update.message.reply_text("Casa desbloqueada.")

    @solo_autorizados
    async def _cmd_reset(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        assert update.message is not None and chat is not None
        self.app.store.limpiar_conversacion(f"{CANAL}:{chat.id}")
        await update.message.reply_text("Conversacion olvidada. Empezamos de cero.")

    @solo_autorizados
    async def _cmd_auditoria(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        assert update.message is not None
        registros = self.app.store.auditoria(10)
        if not registros:
            await update.message.reply_text("Todavia no se ha ejecutado ninguna accion.")
            return
        lineas = [
            f"{formatear(r['ts'], self.app.settings, '%d/%m %H:%M')} "
            f"{'⚠️' if r['error'] else '•'} {r['herramienta']} → {r['resultado'][:90]}"
            for r in registros
        ]
        await update.message.reply_text("\n".join(lineas))

    async def _on_error(self, update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        log.exception("Error procesando update de Telegram", exc_info=ctx.error)
        if not isinstance(update, Update):
            return

        aviso = "Algo ha fallado por dentro. Revisa los logs del backend."
        # En una pulsacion de boton no hay `message`, y callar ahi es lo peor:
        # la accion puede haberse ejecutado ya y el usuario no se enteraria.
        if update.callback_query is not None:
            try:
                await update.callback_query.answer(aviso, show_alert=True)
            except Exception:  # noqa: BLE001 - ya estamos en el manejador de errores
                log.warning("Tampoco se pudo avisar por el callback")
            return

        if update.message is not None:
            await update.message.reply_text(aviso)

