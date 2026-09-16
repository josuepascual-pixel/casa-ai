"""Ordenes para mas tarde: se apuntan por chat, se lanzan a su hora con la
identidad de quien las pidio y el resultado vuelve por su mismo chat."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from casa_ai.adapters.base import AdapterError
from casa_ai.agent.prompts import construir_system
from casa_ai.automations.rutinas import Rutinas
from casa_ai.settings import Inventario, Persona, Settings
from casa_ai.store import Store
from casa_ai.tools import construir_registro
from casa_ai.tools.programar import (
    _cancelar,
    _listar,
    _programar,
    dias_de,
    siguiente_repeticion,
)

from .dobles import AplicacionFalsa, contexto
from .test_rutinas import BotFalso

MADRID = ZoneInfo("Europe/Madrid")


def _manana_a_las_8() -> datetime:
    return (datetime.now(MADRID) + timedelta(days=1)).replace(
        hour=8, minute=0, second=0, microsecond=0
    )


def _ctx(settings: Settings, store: Store, **partes: Any):
    partes.setdefault("canal", "telegram")
    partes.setdefault("usuario", "555")
    partes.setdefault("conversacion", "telegram:555")
    return contexto(settings, Inventario(), store, por_defecto=True, **partes)


# --- Fechas ------------------------------------------------------------------


def test_dias_en_castellano() -> None:
    assert dias_de("diario") == set(range(7))
    assert dias_de("laborables") == {0, 1, 2, 3, 4}
    assert dias_de("fin de semana") == {5, 6}
    assert dias_de("lun, mie, viernes") == {0, 2, 4}
    with pytest.raises(AdapterError, match="No entiendo el dia"):
        dias_de("nunca")


def test_la_siguiente_repeticion_respeta_hora_y_dias() -> None:
    martes = datetime(2026, 9, 15, 9, 0, tzinfo=MADRID)
    # Misma hora ya pasada hoy: manana.
    assert siguiente_repeticion(martes, "08:00", "diario") == datetime(
        2026, 9, 16, 8, 0, tzinfo=MADRID
    )
    # Aun no ha llegado: hoy.
    assert siguiente_repeticion(martes, "21:30", "diario") == datetime(
        2026, 9, 15, 21, 30, tzinfo=MADRID
    )
    # El sabado que viene.
    assert siguiente_repeticion(martes, "10:00", "fin de semana") == datetime(
        2026, 9, 19, 10, 0, tzinfo=MADRID
    )
    with pytest.raises(AdapterError, match="HH:MM"):
        siguiente_repeticion(martes, "8 de la manana", "diario")


# --- La herramienta ------------------------------------------------------------


async def test_programar_una_vez_apunta_la_orden(settings: Settings, store: Store) -> None:
    ctx = _ctx(settings, store)
    manana = _manana_a_las_8()
    r = await _programar(
        ctx, "sube las persianas del salon", manana.strftime("%Y-%m-%dT%H:%M"), None, None
    )

    assert r["programada"]["orden"] == "sube las persianas del salon"
    assert r["programada"]["proxima_vez"].endswith(manana.strftime("%d/%m/%Y 08:00"))
    assert "repite" not in r["programada"]

    filas = store.programaciones(canal="telegram", usuario="555")
    assert len(filas) == 1 and filas[0]["conversacion"] == "telegram:555"
    assert filas[0]["siguiente"] == manana.timestamp()


async def test_programar_repetida_calcula_el_primer_disparo(
    settings: Settings, store: Store
) -> None:
    ctx = _ctx(settings, store)
    r = await _programar(ctx, "pon jazz en la cocina", None, "7:30", "laborables")
    assert r["programada"]["repite"] == "laborables a las 07:30"
    fila = store.programaciones(canal="telegram", usuario="555")[0]
    assert fila["hora"] == "07:30" and fila["dias"] == "laborables"
    assert fila["siguiente"] > datetime.now(MADRID).timestamp()


async def test_programar_rechaza_lo_que_no_tiene_sentido(
    settings: Settings, store: Store
) -> None:
    ctx = _ctx(settings, store)
    with pytest.raises(AdapterError, match="ya ha pasado"):
        await _programar(ctx, "algo", "2001-01-01T08:00", None, None)
    with pytest.raises(AdapterError, match="un ano"):
        await _programar(ctx, "algo", "2100-01-01T08:00", None, None)
    with pytest.raises(AdapterError, match="Di cuando"):
        await _programar(ctx, "algo", None, None, None)
    with pytest.raises(AdapterError, match="No entiendo la fecha"):
        await _programar(ctx, "algo", "manana a las 8", None, None)
    with pytest.raises(AdapterError, match="vacia"):
        await _programar(ctx, "   ", _manana_a_las_8().isoformat(), None, None)
    assert store.programaciones(canal="telegram", usuario="555") == []


async def test_listar_y_cancelar_solo_lo_propio(settings: Settings, store: Store) -> None:
    mio = _ctx(settings, store)
    otro = _ctx(settings, store, usuario="777", conversacion="telegram:777")
    await _programar(mio, "riego", _manana_a_las_8().isoformat(), None, None)
    id_ = (await _listar(mio))["programaciones"][0]["id"]

    assert (await _listar(otro))["programaciones"] == []
    with pytest.raises(AdapterError, match="ninguna programacion tuya"):
        await _cancelar(otro, id_)
    assert await _cancelar(mio, id_) == {"cancelada": id_}
    assert (await _listar(mio))["programaciones"] == []


def test_solo_donde_se_puede_avisar_y_tambien_para_ninos(
    settings: Settings, store: Store
) -> None:
    registro = construir_registro()

    def ofrecidas(canal: str, **partes: Any) -> set[str]:
        ctx = _ctx(settings, store, canal=canal, **partes)
        return {h.nombre for h in registro.disponibles(ctx)}

    tres = {"programar", "programaciones_listar", "programacion_cancelar"}
    assert tres <= ofrecidas("telegram") and tres <= ofrecidas("whatsapp")
    assert not tres & ofrecidas("panel") and not tres & ofrecidas("voz")
    assert tres <= ofrecidas("telegram", persona=Persona(nombre="Leo", nivel="nino"))


def test_el_prompt_manda_programar_en_vez_de_prometer(settings: Settings) -> None:
    prompt = construir_system(settings, Inventario())
    assert "se programa, no se promete" in prompt


# --- El planificador ------------------------------------------------------------


async def test_a_su_hora_se_ejecuta_como_quien_la_pidio_y_vuelve_por_su_chat(
    settings: Settings,
) -> None:
    store = Store(settings.db_path)
    app = AplicacionFalsa(settings, "Persianas del salon arriba.", store=store)
    bot = BotFalso()
    rutinas = Rutinas(app, bot)  # type: ignore[arg-type]
    store.programar(
        canal="telegram", usuario="555", conversacion="telegram:555",
        orden="sube las persianas del salon", siguiente=1000.0,
    )

    await rutinas._programadas(ahora=2000.0)

    turno = app.turnos[-1]
    assert turno["canal"] == "telegram" and turno["usuario"] == "555"
    assert turno["conversacion"] == "programada:1"
    assert turno["confirmacion"] == "imposible"
    assert "sube las persianas del salon" in turno["entrada"]
    assert bot.enviados == [(555, "⏰ Persianas del salon arriba.")]
    # Una sola vez: ya no esta activa, y otro tic no la repite.
    assert store.programaciones(canal="telegram", usuario="555") == []
    await rutinas._programadas(ahora=3000.0)
    assert len(app.turnos) == 1


async def test_una_repetida_se_reprograma_para_la_siguiente(settings: Settings) -> None:
    store = Store(settings.db_path)
    app = AplicacionFalsa(settings, "Jazz en la cocina.", store=store)
    rutinas = Rutinas(app, BotFalso())  # type: ignore[arg-type]
    vencida = datetime(2026, 9, 15, 7, 30, tzinfo=MADRID).timestamp()
    store.programar(
        canal="telegram", usuario="555", conversacion="telegram:555",
        orden="pon jazz en la cocina", siguiente=vencida, hora="07:30", dias="laborables",
    )

    await rutinas._programadas(ahora=vencida + 30)

    fila = store.programaciones(canal="telegram", usuario="555")[0]
    assert fila["siguiente"] == datetime(2026, 9, 16, 7, 30, tzinfo=MADRID).timestamp()


async def test_un_fallo_no_la_repite_cada_minuto_ni_tumba_las_demas(
    settings: Settings,
) -> None:
    store = Store(settings.db_path)

    class AppQueFalla(AplicacionFalsa):
        async def responder_completo(self, **kwargs: Any):
            if "rompe" in kwargs["entrada"]:
                self.turnos.append(kwargs)
                raise RuntimeError("boom")
            return await super().responder_completo(**kwargs)

    app = AppQueFalla(settings, "hecho", store=store)
    bot = BotFalso()
    rutinas = Rutinas(app, bot)  # type: ignore[arg-type]
    store.programar(canal="telegram", usuario="555", conversacion="telegram:555",
                    orden="rompe", siguiente=1.0)
    store.programar(canal="telegram", usuario="555", conversacion="telegram:555",
                    orden="riega", siguiente=2.0)

    await rutinas._programadas(ahora=10.0)
    await rutinas._programadas(ahora=20.0)

    assert [(c, t) for c, t in bot.enviados] == [(555, "⏰ hecho")]
    assert len(app.turnos) == 2  # la rota no se reintenta


async def test_con_whatsapp_se_entrega_por_whatsapp(settings: Settings) -> None:
    store = Store(settings.db_path)
    app = AplicacionFalsa(settings, "hecho", store=store)

    class WhatsAppFalso:
        enviados: list[tuple[str, str]] = []

        async def _enviar(self, numero: str, texto: str) -> None:
            self.enviados.append((numero, texto))

    wa = WhatsAppFalso()
    rutinas = Rutinas(app, None, wa)  # type: ignore[arg-type]
    store.programar(canal="whatsapp", usuario="34600", conversacion="whatsapp:34600",
                    orden="riega", siguiente=1.0)
    await rutinas._programadas(ahora=10.0)
    assert wa.enviados == [("34600", "⏰ hecho")]


async def test_el_tic_solo_existe_si_hay_por_donde_avisar(settings: Settings) -> None:
    sin_canal = Rutinas(AplicacionFalsa(settings, store=Store(settings.db_path)), None)  # type: ignore[arg-type]
    sin_canal.iniciar()
    assert sin_canal.scheduler.get_jobs() == []

    con_bot = Rutinas(AplicacionFalsa(settings, store=Store(settings.db_path)), BotFalso())  # type: ignore[arg-type]
    try:
        con_bot.iniciar()
        assert {j.id for j in con_bot.scheduler.get_jobs()} == {"programaciones"}
        assert con_bot.scheduler.running
    finally:
        con_bot.detener()
