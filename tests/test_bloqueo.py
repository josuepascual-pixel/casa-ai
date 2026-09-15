"""El interruptor de emergencia y el aviso de intrusos.

Si se pierde un movil, /bloquear deja la casa en solo lectura por todos los
canales hasta que el dueno la desbloquee. Y si alguien llama a la puerta del
bot, el dueno se entera: el bot es publico por construccion, lo que no puede
ser es que eso pase en silencio.
"""

from __future__ import annotations

from typing import Any

import pytest

from casa_ai.agent.registry import Contexto, Herramienta, Registro, Riesgo, esquema
from casa_ai.agent.safety import Ejecutor
from casa_ai.channels.telegram import BotTelegram
from casa_ai.settings import Inventario, Settings
from casa_ai.store import Store

from .dobles import AplicacionFalsa, contexto
from .test_telegram import MensajeFalso, UpdateFalso


def test_el_bloqueo_se_guarda_y_cancela_lo_pendiente(store: Store) -> None:
    store.nuevo_turno("c")
    store.crear_pendiente(canal="telegram", usuario="1", conversacion="c",
                          herramienta="x", argumentos={}, resumen="algo")
    assert store.bloqueo() is None

    store.bloquear("1")

    assert store.bloqueo()["quien"] == "1"
    assert store.pendientes_de("c") == []
    store.desbloquear()
    assert store.bloqueo() is None


async def test_con_la_casa_bloqueada_solo_se_lee(settings: Settings, store: Store) -> None:
    hechas: list[str] = []

    async def leer(_ctx: Contexto) -> dict[str, Any]:
        hechas.append("leer")
        return {}

    async def tocar(_ctx: Contexto) -> dict[str, Any]:
        hechas.append("tocar")
        return {}

    registro = Registro()
    registro.anadir(
        Herramienta(nombre="leer", descripcion="d" * 50, esquema=esquema({}),
                    riesgo=Riesgo.LECTURA, handler=leer),
        Herramienta(nombre="tocar", descripcion="d" * 50, esquema=esquema({}),
                    riesgo=Riesgo.MEDIO, handler=tocar),
        Herramienta(nombre="gordo", descripcion="d" * 50, esquema=esquema({}),
                    riesgo=Riesgo.ALTO, handler=tocar, resumen_confirmacion=lambda a: "gordo"),
    )
    ctx = contexto(settings, Inventario(), store, por_defecto=True,
                   canal="telegram", usuario="1", conversacion="c")
    ejecutor = Ejecutor(registro, ctx)
    store.nuevo_turno("c")
    await ejecutor.ejecutar("gordo", {})
    token = store.pendientes_de("c")[0]["token"]

    store.bloquear("1")

    _, es_error = await ejecutor.ejecutar("leer", {})
    assert es_error is False
    resultado, es_error = await ejecutor.ejecutar("tocar", {})
    assert es_error is True and "BLOQUEADA" in resultado
    # Ni siquiera con un boton: la pendiente se cancelo al bloquear, y aunque
    # no, confirmar tambien se niega.
    resultado, es_error = await ejecutor.confirmar(token)
    assert es_error is True
    assert hechas == ["leer"]


@pytest.fixture
def bot(settings: Settings) -> BotTelegram:
    autorizado = settings.model_copy(
        update={"telegram_token": "123:abc", "telegram_chats_autorizados": "555,777"}
    )
    bot = BotTelegram(AplicacionFalsa(autorizado))  # type: ignore[arg-type]
    bot.app.inventario = Inventario.model_validate(
        {"personas": [{"nombre": "Papa", "nivel": "dueno", "telegram": ["555"]},
                      {"nombre": "Mama", "nivel": "adulto", "telegram": ["777"]}]}
    )
    return bot


async def test_solo_el_dueno_bloquea_y_desbloquea(bot: BotTelegram) -> None:
    update = UpdateFalso(chat_id=777, mensaje=MensajeFalso("/bloquear"))
    await bot._cmd_bloquear(update, None)  # type: ignore[arg-type]
    assert "Solo el dueno" in update.message.respuestas[0]
    assert bot.app.store.bloqueo() is None

    update = UpdateFalso(chat_id=555, mensaje=MensajeFalso("/bloquear"))
    await bot._cmd_bloquear(update, None)  # type: ignore[arg-type]
    assert bot.app.store.bloqueo()["quien"] == "555"

    update = UpdateFalso(chat_id=777, mensaje=MensajeFalso("/desbloquear"))
    await bot._cmd_desbloquear(update, None)  # type: ignore[arg-type]
    assert bot.app.store.bloqueo() is not None
    update = UpdateFalso(chat_id=555, mensaje=MensajeFalso("/desbloquear"))
    await bot._cmd_desbloquear(update, None)  # type: ignore[arg-type]
    assert bot.app.store.bloqueo() is None


class _BotFalso:
    def __init__(self) -> None:
        self.bot = self
        self.enviados: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.enviados.append((chat_id, text))


async def test_un_desconocido_que_escribe_hace_saltar_el_aviso_al_dueno(
    bot: BotTelegram,
) -> None:
    bot.application = _BotFalso()  # type: ignore[assignment]

    class ChatDesconocido:
        id = 999
        first_name = "Fulano"
        username = "fulanito"

    update = UpdateFalso(chat_id=999)
    update.effective_chat = ChatDesconocido()
    await bot._texto(update, None)  # type: ignore[arg-type]
    await bot._texto(update, None)  # type: ignore[arg-type]

    # Al dueno, no a la madre; y una vez, no una por mensaje.
    assert [c for c, _ in bot.application.enviados] == [555]  # type: ignore[attr-defined]
    aviso = bot.application.enviados[0][1]  # type: ignore[attr-defined]
    assert "999" in aviso and "Fulano" in aviso and "rechazado" in aviso
