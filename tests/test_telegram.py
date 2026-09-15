"""Canal de Telegram: autorizacion y manejo de mensajes.

La autorizacion de este canal es critica: un bot de Telegram es alcanzable por
cualquiera que adivine su nombre, y este bot puede apagar la casa. Estaba sin
cubrir por completo.
"""

from __future__ import annotations

from typing import Any

import pytest

from casa_ai.channels.comun import partir
from casa_ai.channels.telegram import LIMITE_MENSAJE, BotTelegram
from casa_ai.settings import Settings

from .dobles import AplicacionFalsa


class MensajeFalso:
    def __init__(self, texto: str | None = None, caption: str | None = None) -> None:
        self.text = texto
        self.caption = caption
        self.voice = None
        self.audio = None
        self.photo: list[Any] = []
        self.respuestas: list[str] = []
        self.teclados: list[Any] = []
        self.chat = self
        self.acciones: list[Any] = []

    async def reply_text(self, texto: str, **kwargs: Any) -> None:
        self.respuestas.append(texto)
        self.teclados.append(kwargs.get("reply_markup"))

    async def send_action(self, accion: Any) -> None:
        self.acciones.append(accion)


class ChatFalso:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id


class UpdateFalso:
    def __init__(self, chat_id: int, mensaje: MensajeFalso | None = None) -> None:
        self.effective_chat = ChatFalso(chat_id)
        self.message = mensaje if mensaje is not None else MensajeFalso("hola")


@pytest.fixture
def bot(settings: Settings) -> BotTelegram:
    autorizado = settings.model_copy(
        update={"telegram_token": "123:abc", "telegram_chats_autorizados": "555"}
    )
    return BotTelegram(AplicacionFalsa(autorizado))  # type: ignore[arg-type]


# --- Autorizacion -----------------------------------------------------------


async def test_un_chat_no_autorizado_no_ejecuta_nada(bot: BotTelegram) -> None:
    update = UpdateFalso(chat_id=999)

    await bot._texto(update, None)  # type: ignore[arg-type]

    assert bot.app.turnos == []  # type: ignore[attr-defined]
    assert "No estas autorizado" in update.message.respuestas[0]


async def test_el_rechazo_dice_el_chat_id_para_poder_anadirlo(
    bot: BotTelegram,
) -> None:
    update = UpdateFalso(chat_id=999)
    await bot._texto(update, None)  # type: ignore[arg-type]
    assert "999" in update.message.respuestas[0]


async def test_un_chat_autorizado_si_pasa(bot: BotTelegram) -> None:
    update = UpdateFalso(chat_id=555, mensaje=MensajeFalso("enciende el salon"))

    await bot._texto(update, None)  # type: ignore[arg-type]

    turnos = bot.app.turnos  # type: ignore[attr-defined]
    assert len(turnos) == 1
    assert turnos[0]["canal"] == "telegram"
    assert turnos[0]["usuario"] == "555"
    assert turnos[0]["conversacion"] == "telegram:555"
    assert turnos[0]["entrada"] == "enciende el salon"
    assert update.message.respuestas == ["hecho"]


async def test_con_la_lista_vacia_no_se_atiende_a_nadie(settings: Settings) -> None:
    """Falla cerrada a proposito."""
    sin_lista = settings.model_copy(
        update={"telegram_token": "123:abc", "telegram_chats_autorizados": ""}
    )
    bot = BotTelegram(AplicacionFalsa(sin_lista))  # type: ignore[arg-type]
    update = UpdateFalso(chat_id=555)

    await bot._texto(update, None)  # type: ignore[arg-type]

    assert bot.app.turnos == []  # type: ignore[attr-defined]


async def test_todos_los_comandos_comprueban_autorizacion(bot: BotTelegram) -> None:
    for handler in (bot._cmd_start, bot._cmd_estado, bot._cmd_reset, bot._cmd_auditoria,
                    bot._cmd_verificar, bot._cmd_descubrir, bot._cmd_sondear):
        update = UpdateFalso(chat_id=999)
        await handler(update, None)  # type: ignore[arg-type]
        assert "No estas autorizado" in update.message.respuestas[0], handler.__name__

    # Y el reset no ha borrado nada de un chat ajeno.
    assert bot.app.store.limpiadas == []  # type: ignore[attr-defined]


async def test_las_fotos_tambien_comprueban_autorizacion(bot: BotTelegram) -> None:
    update = UpdateFalso(chat_id=999)
    await bot._foto(update, None)  # type: ignore[arg-type]
    assert bot.app.turnos == []  # type: ignore[attr-defined]


async def test_las_notas_de_voz_tambien(bot: BotTelegram) -> None:
    update = UpdateFalso(chat_id=999)
    await bot._voz(update, None)  # type: ignore[arg-type]
    assert bot.app.turnos == []  # type: ignore[attr-defined]


# --- Comandos ---------------------------------------------------------------


async def test_reset_olvida_solo_su_conversacion(bot: BotTelegram) -> None:
    update = UpdateFalso(chat_id=555)
    await bot._cmd_reset(update, None)  # type: ignore[arg-type]

    assert bot.app.store.limpiadas == ["telegram:555"]  # type: ignore[attr-defined]
    assert "de cero" in update.message.respuestas[0]


async def test_estado_muestra_los_subsistemas(bot: BotTelegram) -> None:
    update = UpdateFalso(chat_id=555)
    await bot._cmd_estado(update, None)  # type: ignore[arg-type]

    texto = update.message.respuestas[0]
    assert "✅ home_assistant" in texto
    assert "❌ unifi" in texto
    assert "2 herramientas activas" in texto


async def test_auditoria_vacia(bot: BotTelegram) -> None:
    update = UpdateFalso(chat_id=555)
    await bot._cmd_auditoria(update, None)  # type: ignore[arg-type]
    assert "ninguna accion" in update.message.respuestas[0]


async def test_auditoria_con_registros_en_hora_de_la_casa(bot: BotTelegram) -> None:
    bot.app.store.registros = [  # type: ignore[attr-defined]
        {"ts": 1789041600.0, "herramienta": "energia_modo_bateria",
         "resultado": "cargando a 3000 W", "error": 0},
        {"ts": 1789041600.0, "herramienta": "red_wifi_activar",
         "resultado": "ERROR: no existe", "error": 1},
    ]
    update = UpdateFalso(chat_id=555)
    await bot._cmd_auditoria(update, None)  # type: ignore[arg-type]

    texto = update.message.respuestas[0]
    assert "14:00" in texto           # Europe/Madrid en septiembre, no UTC
    assert "energia_modo_bateria" in texto
    assert "⚠️" in texto              # el fallo se marca


async def test_verificar_y_descubrir_desde_el_movil(bot: BotTelegram) -> None:
    """El complemento no tiene terminal: la puesta en marcha se hace por Telegram."""
    update = UpdateFalso(chat_id=555)
    await bot._cmd_verificar(update, None)  # type: ignore[arg-type]
    assert any("❌ Musica BluOS" in r for r in update.message.respuestas)

    update = UpdateFalso(chat_id=555, mensaje=MensajeFalso("/descubrir 192.168.0.0/24"))
    await bot._cmd_descubrir(update, None)  # type: ignore[arg-type]
    assert any("bluos:" in r for r in update.message.respuestas)
    assert bot.app.redes_pedidas == ["192.168.0.0/24"]  # type: ignore[attr-defined]

    update = UpdateFalso(chat_id=555, mensaje=MensajeFalso("/sondear barrer 2 0 50"))
    await bot._cmd_sondear(update, None)  # type: ignore[arg-type]
    assert bot.app.sondeos == ["barrer 2 0 50"]  # type: ignore[attr-defined]
    assert any("0x2C0B" in r for r in update.message.respuestas)


async def test_un_nino_no_verifica_ni_descubre(bot: BotTelegram) -> None:
    from casa_ai.settings import Inventario

    bot.app.inventario = Inventario.model_validate(
        {"personas": [{"nombre": "Leo", "nivel": "nino", "telegram": ["555"]}]}
    )
    update = UpdateFalso(chat_id=555)
    await bot._cmd_descubrir(update, None)  # type: ignore[arg-type]
    assert update.message.respuestas == ["Eso es cosa de los mayores."]


async def test_start_explica_para_que_sirve(bot: BotTelegram) -> None:
    update = UpdateFalso(chat_id=555)
    await bot._cmd_start(update, None)  # type: ignore[arg-type]
    texto = update.message.respuestas[0]
    assert texto.startswith("Jarvis a su servicio")
    assert "/estado" in texto and "/auditoria" in texto


# --- Respuestas largas ------------------------------------------------------


def test_un_mensaje_corto_va_entero() -> None:
    assert partir("hola", LIMITE_MENSAJE) == ["hola"]


def test_una_respuesta_vacia_no_manda_un_mensaje_vacio() -> None:
    """Telegram rechaza un mensaje sin texto."""
    assert partir("", LIMITE_MENSAJE) == ["Hecho."]


def test_una_respuesta_larga_se_parte_por_lineas() -> None:
    parrafo = "\n".join(f"linea {i} con algo de texto" for i in range(400))
    trozos = partir(parrafo, LIMITE_MENSAJE)

    assert len(trozos) > 1
    assert all(len(x) <= 4096 for x in trozos)
    # Se parte en un salto de linea, no a mitad de palabra.
    assert trozos[0].endswith("texto")


def test_una_linea_larguisima_se_corta_igual() -> None:
    """Sin saltos de linea no queda otra que cortar por longitud."""
    trozos = partir("x" * 10000, LIMITE_MENSAJE)
    assert len(trozos) == 3
    assert all(len(x) <= 4096 for x in trozos)
    assert "".join(trozos) == "x" * 10000


async def test_la_respuesta_se_manda_en_varios_mensajes(bot: BotTelegram) -> None:
    bot.app.respuesta = "\n".join(f"linea {i}" * 20 for i in range(400))  # type: ignore[attr-defined]
    update = UpdateFalso(chat_id=555)

    await bot._texto(update, None)  # type: ignore[arg-type]

    assert len(update.message.respuestas) > 1


# --- Confirmacion fuera de banda (botones) ----------------------------------


class ConsultaFalsa:
    def __init__(self, datos: str) -> None:
        self.data = datos
        self.respondida = False
        self.editado: list[str] = []

    async def answer(self) -> None:
        self.respondida = True

    async def edit_message_text(self, texto: str, **kwargs: Any) -> None:
        self.editado.append(texto)


class UpdateBoton:
    def __init__(self, chat_id: int, datos: str) -> None:
        self.effective_chat = ChatFalso(chat_id)
        self.callback_query = ConsultaFalsa(datos)
        self.message = None


async def test_el_turno_pide_confirmacion_fuera_de_banda(bot: BotTelegram) -> None:
    """Es lo que hace que el token no entre en el contexto del modelo."""
    update = UpdateFalso(chat_id=555)
    await bot._texto(update, None)  # type: ignore[arg-type]

    assert bot.app.turnos[0]["confirmacion"] == "boton"  # type: ignore[attr-defined]


async def test_una_accion_pendiente_saca_botones(bot: BotTelegram) -> None:
    bot.app.store.pendientes = [  # type: ignore[attr-defined]
        {"token": "abc123", "resumen": "Forzar carga a 3000 W",
         "herramienta": "energia_modo_bateria", "ts": 0, "turno": 3}
    ]
    update = UpdateFalso(chat_id=555)

    await bot._texto(update, None)  # type: ignore[arg-type]

    # El ultimo mensaje es el del boton, con el resumen de la accion.
    assert "Forzar carga a 3000 W" in update.message.respuestas[-1]
    teclado = update.message.teclados[-1]
    botones = [b for fila in teclado.inline_keyboard for b in fila]
    assert [b.callback_data for b in botones] == ["ok:abc123", "no:abc123"]


async def test_sin_acciones_pendientes_no_hay_botones(bot: BotTelegram) -> None:
    update = UpdateFalso(chat_id=555)
    await bot._texto(update, None)  # type: ignore[arg-type]
    assert all(t is None for t in update.message.teclados)


async def test_pulsar_confirmar_ejecuta_sin_pasar_por_el_modelo(
    bot: BotTelegram,
) -> None:
    update = UpdateBoton(chat_id=555, datos="ok:abc123")

    await bot._boton(update, None)  # type: ignore[arg-type]

    assert bot.app.confirmadas == [{  # type: ignore[attr-defined]
        "canal": "telegram", "usuario": "555",
        "conversacion": "telegram:555", "token": "abc123",
    }]
    # Y no se ha dado ningun turno al agente por el camino.
    assert bot.app.turnos == []  # type: ignore[attr-defined]
    assert "cargando a 3000 W" in update.callback_query.editado[0]


async def test_pulsar_cancelar_descarta(bot: BotTelegram) -> None:
    update = UpdateBoton(chat_id=555, datos="no:abc123")

    await bot._boton(update, None)  # type: ignore[arg-type]

    assert bot.app.canceladas[0]["token"] == "abc123"  # type: ignore[attr-defined]
    assert "Cancelado" in update.callback_query.editado[0]
    assert bot.app.confirmadas == []  # type: ignore[attr-defined]


async def test_un_boton_pulsado_por_un_chat_ajeno_no_hace_nada(
    bot: BotTelegram,
) -> None:
    """El callback_data viaja al cliente: hay que reautorizar al volver."""
    update = UpdateBoton(chat_id=999, datos="ok:abc123")

    await bot._boton(update, None)  # type: ignore[arg-type]

    assert bot.app.confirmadas == []  # type: ignore[attr-defined]
    assert "No estas autorizado" in update.callback_query.editado[0]


async def test_un_fallo_al_confirmar_se_cuenta(bot: BotTelegram) -> None:
    async def falla(**kwargs: Any) -> tuple[str, bool]:
        return ("ha caducado", True)

    bot.app.confirmar_pendiente = falla  # type: ignore[attr-defined]
    update = UpdateBoton(chat_id=555, datos="ok:viejo")

    await bot._boton(update, None)  # type: ignore[arg-type]

    assert "No se pudo" in update.callback_query.editado[0]
    assert "caducado" in update.callback_query.editado[0]


async def test_un_callback_desconocido_se_ignora(bot: BotTelegram) -> None:
    update = UpdateBoton(chat_id=555, datos="otracosa:x")
    await bot._boton(update, None)  # type: ignore[arg-type]
    assert bot.app.confirmadas == []  # type: ignore[attr-defined]
    assert update.callback_query.editado == []


async def test_una_propuesta_de_un_turno_anterior_no_reaparece(
    bot: BotTelegram,
) -> None:
    """Sin el filtro de turno, una accion que el usuario ignoro sacaba un boton
    vivo tras cada mensaje posterior durante 15 minutos. Y como la pulsacion se
    salta el guardia de turno, un toque por error habria ejecutado algo que en
    la practica ya estaba declinado."""
    bot.app.store.pendientes = [  # type: ignore[attr-defined]
        {"token": "viejo", "resumen": "Apagar el wifi",
         "herramienta": "red_wifi_activar", "ts": 0, "turno": 1},
    ]
    bot.app.store.turno = 4  # type: ignore[attr-defined]
    update = UpdateFalso(chat_id=555)

    await bot._texto(update, None)  # type: ignore[arg-type]

    assert all(t is None for t in update.message.teclados)
    # Y se pidio filtrando por el turno actual, no sin filtro.
    assert bot.app.store.turnos_pedidos == [4]  # type: ignore[attr-defined]


# --- Codificacion de los botones -------------------------------------------


def test_el_largo_del_prefijo_no_esta_a_mano() -> None:
    """Estaba como un 3 literal en los dos canales, asi que un prefijo de otro
    largo cortaba el token por la mitad sin que nada avisara."""
    from casa_ai.channels.comun import CANCELAR, CONFIRMAR, codificar, decodificar

    token = "un-token-de-22-chars12"
    assert decodificar(codificar(CONFIRMAR, token)) == (CONFIRMAR, token)
    assert decodificar(codificar(CANCELAR, token)) == (CANCELAR, token)
    assert decodificar("otracosa:" + token) is None
    assert decodificar("") is None


async def test_en_un_grupo_la_persona_es_quien_escribe(settings: Settings) -> None:
    """Un invitado anadido al grupo del dueno no hereda su nivel ni puede
    pulsar el boton de una accion que propuso otro: la identidad es el usuario."""
    from casa_ai.settings import Inventario

    autorizado = settings.model_copy(
        update={"telegram_token": "123:abc", "telegram_chats_autorizados": "-1001"}
    )
    bot = BotTelegram(AplicacionFalsa(autorizado))  # type: ignore[arg-type]
    bot.app.inventario = Inventario.model_validate(
        {"personas": [{"nombre": "Papa", "nivel": "dueno", "telegram": ["42"]}]}
    )

    class UsuarioFalso:
        id = 42

    update = UpdateFalso(chat_id=-1001)
    update.effective_user = UsuarioFalso()
    await bot._texto(update, None)  # type: ignore[arg-type]
    turno = bot.app.turnos[-1]  # type: ignore[attr-defined]
    assert turno["usuario"] == "42" and turno["conversacion"] == "telegram:-1001"

    class Invitado:
        id = 99

    update = UpdateFalso(chat_id=-1001)
    update.effective_user = Invitado()
    await bot._cmd_estado(update, None)  # type: ignore[arg-type]
    assert "alguien sin registrar (nino)" in update.message.respuestas[0]
