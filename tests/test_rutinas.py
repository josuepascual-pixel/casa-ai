"""Rutinas proactivas: el agente que habla primero.

Aqui se comprueba una afirmacion de la documentacion que no tenia test: una
rutina programada NO puede ejecutar una accion de riesgo. A las 8 de la manana
no hay nadie para confirmar, asi que si pudiera, el sistema estaria actuando
sobre la casa sin supervision.
"""

from __future__ import annotations

from typing import Any

import pytest

from casa_ai.agent.registry import Contexto, Herramienta, Registro, Riesgo, esquema
from casa_ai.agent.safety import Ejecutor
from casa_ai.automations.rutinas import SIN_NOVEDAD, Rutinas
from casa_ai.settings import Inventario, Settings
from casa_ai.store import Store

from .dobles import AplicacionFalsa, contexto


def app_falsa(settings: Settings, respuesta: str = "todo bien") -> Any:
    """Con el Store de verdad: las rutinas cancelan pendientes al arrancar."""
    return AplicacionFalsa(settings, respuesta, store=Store(settings.db_path))


class BotFalso:
    def __init__(self) -> None:
        self.application = self
        self.bot = self
        self.enviados: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.enviados.append((chat_id, text))


@pytest.fixture
def settings_con_chat(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"telegram_token": "123:abc", "telegram_chats_autorizados": "555,777"}
    )


# --- Riesgo -----------------------------------------------------------------


async def test_una_rutina_no_puede_ejecutar_una_accion_de_riesgo(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    """La rutina propone, el sistema exige confirmacion, y nadie confirma."""
    ejecutadas: list[str] = []

    async def peligrosa(_ctx: Contexto) -> dict[str, Any]:
        ejecutadas.append("peligrosa")
        return {"hecho": True}

    registro = Registro()
    registro.anadir(
        Herramienta(
            nombre="peligrosa", descripcion="d" * 50, esquema=esquema({}),
            riesgo=Riesgo.ALTO, handler=peligrosa,
            resumen_confirmacion=lambda a: "algo gordo",
        ),
    )
    ctx = contexto(
        settings, inventario, store, por_defecto=True,
        canal="rutina", usuario="programada", conversacion="rutina:vigilancia",
        confirmacion="imposible",
    )
    ejecutor = Ejecutor(registro, ctx)
    store.nuevo_turno("rutina:vigilancia")

    aviso, _ = await ejecutor.ejecutar("peligrosa", {})

    # Declarado imposible: ni pendiente ni token, y ninguna herramienta con la
    # que el modelo pudiera confirmar aunque quisiera.
    assert "NO HAY NADIE" in aviso
    assert store.pendientes_de("rutina:vigilancia") == []
    _, es_error = await ejecutor.ejecutar("ejecutar_accion_pendiente", {"token": "x"})
    assert es_error is True
    assert ejecutadas == []


async def test_la_rutina_limpia_su_conversacion_y_cancela_lo_pendiente(
    settings_con_chat: Settings
) -> None:
    """Cada ejecucion empieza de cero: un token de la vez anterior no puede
    quedarse vivo esperando a que el modelo lo use."""
    app = app_falsa(settings_con_chat)
    app.store.nuevo_turno("rutina:vigilancia")
    token = app.store.crear_pendiente(
        canal="rutina", usuario="programada", conversacion="rutina:vigilancia",
        herramienta="peligrosa", argumentos={}, resumen="algo",
    )

    rutinas = Rutinas(app, None)  # type: ignore[arg-type]
    await rutinas._turno("revisa", "rutina:vigilancia")

    app.store.nuevo_turno("rutina:vigilancia")
    accion, motivo = app.store.tomar_pendiente(
        token, canal="rutina", usuario="programada", conversacion="rutina:vigilancia"
    )
    assert accion is None
    assert "cancelada" in motivo


# --- Difusion ---------------------------------------------------------------


async def test_el_informe_llega_a_todos_los_chats(settings_con_chat: Settings) -> None:
    app = app_falsa(settings_con_chat, "La bateria al 80 %.")
    bot = BotFalso()
    rutinas = Rutinas(app, bot)  # type: ignore[arg-type]

    await rutinas._informe()

    assert {chat for chat, _ in bot.enviados} == {555, 777}
    assert "Buenos dias" in bot.enviados[0][1]
    assert "La bateria al 80 %" in bot.enviados[0][1]


async def test_la_vigilancia_calla_si_no_hay_novedad(
    settings_con_chat: Settings
) -> None:
    """Interrumpir cada media hora para decir que todo va bien es peor que
    no avisar."""
    app = app_falsa(settings_con_chat, SIN_NOVEDAD)
    bot = BotFalso()
    rutinas = Rutinas(app, bot)  # type: ignore[arg-type]

    await rutinas._vigilancia()

    assert bot.enviados == []


async def test_la_vigilancia_avisa_cuando_hay_algo(
    settings_con_chat: Settings
) -> None:
    app = app_falsa(settings_con_chat, "El punto de acceso del garaje esta caido.")
    bot = BotFalso()
    rutinas = Rutinas(app, bot)  # type: ignore[arg-type]

    await rutinas._vigilancia()

    assert len(bot.enviados) == 2
    assert "Aviso de la casa" in bot.enviados[0][1]


async def test_un_fallo_de_la_rutina_no_tumba_el_proceso(
    settings_con_chat: Settings
) -> None:
    class Rota(AplicacionFalsa):
        async def responder(self, **kwargs: Any) -> str:
            raise RuntimeError("el inversor no responde")

    rutinas = Rutinas(Rota(settings_con_chat, store=Store(settings_con_chat.db_path)), BotFalso())  # type: ignore[arg-type]

    await rutinas._informe()     # no debe lanzar
    await rutinas._vigilancia()  # tampoco


async def test_un_chat_que_falla_no_impide_avisar_al_resto(
    settings_con_chat: Settings
) -> None:
    class BotParcial(BotFalso):
        async def send_message(self, chat_id: int, text: str) -> None:
            if chat_id == 555:
                raise RuntimeError("chat bloqueado")
            await super().send_message(chat_id, text)

    bot = BotParcial()
    rutinas = Rutinas(app_falsa(settings_con_chat), bot)  # type: ignore[arg-type]

    await rutinas._informe()

    assert [chat for chat, _ in bot.enviados] == [777]


# --- Programacion -----------------------------------------------------------


def test_sin_chats_autorizados_no_se_programa_nada(settings: Settings) -> None:
    """Una rutina que no tiene a quien avisar solo gastaria tokens."""
    rutinas = Rutinas(app_falsa(settings), None)  # type: ignore[arg-type]
    rutinas.iniciar()

    assert not rutinas.scheduler.running
    assert rutinas.scheduler.get_jobs() == []


async def test_con_chats_se_programan_las_dos_rutinas(
    settings_con_chat: Settings,
) -> None:
    """Async porque AsyncIOScheduler.start() necesita un bucle corriendo; en
    produccion se arranca desde el lifespan, que ya lo tiene."""
    bot = BotFalso()
    rutinas = Rutinas(app_falsa(settings_con_chat), bot)  # type: ignore[arg-type]
    try:
        rutinas.iniciar(hora_informe=7, minutos_vigilancia=15)
        ids = {j.id for j in rutinas.scheduler.get_jobs()}
        assert ids == {"informe_matinal", "vigilancia", "programaciones"}
    finally:
        rutinas.detener()


def test_detener_es_seguro_aunque_no_se_haya_iniciado(settings: Settings) -> None:
    rutinas = Rutinas(app_falsa(settings), None)  # type: ignore[arg-type]
    rutinas.detener()  # no debe lanzar


async def test_una_rutina_no_recibe_ni_token_ni_accion_pendiente(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    """Antes se apoyaba solo en el guardia de turno: la accion quedaba
    pendiente y el token vivo 15 minutos, dentro de un texto que ademas sale
    por Telegram en el aviso de la rutina."""
    ejecutadas: list[str] = []

    async def peligrosa(_ctx: Contexto) -> dict[str, Any]:
        ejecutadas.append("peligrosa")
        return {"hecho": True}

    registro = Registro()
    registro.anadir(
        Herramienta(
            nombre="peligrosa", descripcion="d" * 50, esquema=esquema({}),
            riesgo=Riesgo.ALTO, handler=peligrosa,
            resumen_confirmacion=lambda a: "algo gordo",
        ),
    )
    ctx = contexto(
        settings, inventario, store, por_defecto=True,
        canal="rutina", usuario="programada", conversacion="rutina:vigilancia",
        confirmacion="imposible",
    )
    store.nuevo_turno("rutina:vigilancia")

    aviso, es_error = await Ejecutor(registro, ctx).ejecutar("peligrosa", {})

    assert es_error is False
    assert ejecutadas == []
    assert "token" not in aviso.lower()
    assert store.pendientes_de("rutina:vigilancia") == []
    # Y queda constancia de que se intento, que es lo que el dueno querra ver.
    assert "sin nadie que la confirme" in store.auditoria(1)[0]["resultado"]


async def test_una_rutina_no_ve_la_herramienta_de_confirmacion(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    """Ofrecerla seria darle a una inyeccion algo a lo que apuntar."""
    from casa_ai.tools import construir_registro

    registro = construir_registro()

    def nombres(confirmacion: str) -> set[str]:
        ctx = contexto(
            settings, inventario, store, por_defecto=True, confirmacion=confirmacion
        )
        return {h.nombre for h in registro.disponibles(ctx)}

    for confirmacion in ("en_banda", "boton", "imposible"):
        assert "ejecutar_accion_pendiente" not in nombres(confirmacion)


async def test_el_informe_no_llega_a_los_chats_de_ninos(settings_con_chat: Settings) -> None:
    """El informe corre como dueno y cuenta camaras y quien esta en casa."""
    app = app_falsa(settings_con_chat, "Todo bien.")
    app.inventario = Inventario.model_validate(
        {"personas": [{"nombre": "Papa", "nivel": "dueno", "telegram": ["555"]},
                      {"nombre": "Peque", "nivel": "nino", "telegram": ["777"]}]}
    )
    bot = BotFalso()
    rutinas = Rutinas(app, bot)  # type: ignore[arg-type]

    await rutinas._informe()

    assert {chat for chat, _ in bot.enviados} == {555}
