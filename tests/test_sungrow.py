"""Decodificado de registros Modbus del inversor Sungrow.

Lo delicado aqui son los signos: el firmware no es homogeneo y un signo al
reves convierte "estas exportando 3 kW" en "estas importando 3 kW".
"""

from __future__ import annotations

import pytest

from casa_ai.adapters.base import AdapterError
from casa_ai.adapters.modbus import i16 as _i16
from casa_ai.adapters.sungrow import REG_ESTADO_BATERIA, Sungrow, _i32, _u32
from casa_ai.settings import Settings


def test_u32_palabra_baja_primero() -> None:
    # Sungrow manda los uint32 con la palabra baja delante.
    assert _u32([0x1234, 0x0001]) == 0x00011234


def test_i32_negativo() -> None:
    assert _i32([0xFFFF, 0xFFFF]) == -1
    assert _i32([0x0000, 0x8000]) == -2147483648


def test_i16_negativo() -> None:
    assert _i16(0xFFFF) == -1
    assert _i16(0x8000) == -32768
    assert _i16(1500) == 1500


def _bloque(valores: dict[int, int]) -> list[int]:
    """Construye el bloque 13000..13049 con los valores indicados."""
    bloque = [0] * 50
    for direccion, valor in valores.items():
        bloque[direccion - REG_ESTADO_BATERIA] = valor
    return bloque


@pytest.fixture
def inversor(settings: Settings) -> Sungrow:
    return Sungrow(settings)


async def _con_registros(inversor: Sungrow, pv: int, bloque: list[int], monkeypatch) -> object:
    async def falso_leer(direccion: int, cantidad: int, *, mantenimiento: bool = False):
        if cantidad == 2 and direccion == 5016:
            return [pv & 0xFFFF, (pv >> 16) & 0xFFFF]
        if direccion == REG_ESTADO_BATERIA:
            return bloque
        raise AssertionError(f"lectura inesperada {direccion} x{cantidad}")

    monkeypatch.setattr(inversor, "_leer", falso_leer)
    return await inversor.estado(usar_cache=False)


async def test_bateria_descargando_por_bits_de_estado(inversor: Sungrow, monkeypatch) -> None:
    """El registro da magnitud sin signo: el signo sale del estado 13000."""
    bloque = _bloque(
        {
            13000: 0x04,   # bit de descarga
            13021: 1800,   # magnitud, sin signo
            13022: 650,    # 65.0 %
            13023: 990,
            13024: 250,
        }
    )
    estado = await _con_registros(inversor, pv=2000, bloque=bloque, monkeypatch=monkeypatch)

    assert estado.bateria_w == -1800
    assert estado.resumen()["bateria_estado"] == "descargando"
    assert estado.bateria_soc == pytest.approx(65.0)


async def test_bateria_cargando_por_bits_de_estado(inversor: Sungrow, monkeypatch) -> None:
    bloque = _bloque({13000: 0x02, 13021: 2500, 13022: 300})
    estado = await _con_registros(inversor, pv=6000, bloque=bloque, monkeypatch=monkeypatch)

    assert estado.bateria_w == 2500
    assert estado.resumen()["bateria_estado"] == "cargando"


async def test_valor_ya_firmado_se_respeta(inversor: Sungrow, monkeypatch) -> None:
    """Si el firmware firma el valor, no lo tocamos aunque los bits digan otra cosa."""
    bloque = _bloque({13000: 0x02, 13021: 0xF8A0, 13022: 500})  # -1888 firmado
    estado = await _con_registros(inversor, pv=100, bloque=bloque, monkeypatch=monkeypatch)

    assert estado.bateria_w == -1888


async def test_balance_de_consumo_de_la_casa(inversor: Sungrow, monkeypatch) -> None:
    """Consumo = solar - (a bateria) - (a red). 5000 - 1000 - 2000 = 2000 W."""
    bloque = _bloque({13000: 0x02, 13021: 1000, 13022: 800, 13009: 2000, 13010: 0})
    estado = await _con_registros(inversor, pv=5000, bloque=bloque, monkeypatch=monkeypatch)

    assert estado.red_w == 2000
    assert estado.resumen()["red_estado"] == "exportando"
    assert estado.consumo_casa_w == 2000


async def test_importando_de_red(inversor: Sungrow, monkeypatch) -> None:
    # -1500 W en int32 con palabra baja primero
    bloque = _bloque({13000: 0x04, 13021: 500, 13022: 200, 13009: 0xFA24, 13010: 0xFFFF})
    estado = await _con_registros(inversor, pv=0, bloque=bloque, monkeypatch=monkeypatch)

    assert estado.red_w == -1500
    assert estado.resumen()["red_estado"] == "importando"
    # 0 solar + 500 de bateria + 1500 de red = 2000 W de consumo
    assert estado.consumo_casa_w == 2000


async def test_signo_de_red_invertible(settings: Settings, monkeypatch) -> None:
    """Hay firmwares que reportan la red al contrario; debe poder corregirse."""
    inversor = Sungrow(settings, invertir_signo_red=True)
    bloque = _bloque({13000: 0x00, 13021: 0, 13022: 500, 13009: 2000, 13010: 0})
    estado = await _con_registros(inversor, pv=0, bloque=bloque, monkeypatch=monkeypatch)

    assert estado.red_w == -2000


async def test_consumo_nunca_negativo(inversor: Sungrow, monkeypatch) -> None:
    """Un desfase de lecturas no debe producir un consumo negativo absurdo."""
    bloque = _bloque({13000: 0x00, 13021: 0, 13022: 500, 13009: 5000, 13010: 0})
    estado = await _con_registros(inversor, pv=1000, bloque=bloque, monkeypatch=monkeypatch)

    assert estado.consumo_casa_w == 0


async def test_acumulados_escalados(inversor: Sungrow, monkeypatch) -> None:
    bloque = _bloque({13000: 0, 13022: 500, 13002: 1234, 13003: 0})
    estado = await _con_registros(inversor, pv=0, bloque=bloque, monkeypatch=monkeypatch)

    assert estado.pv_total_kwh == pytest.approx(123.4)


# --- Control de bateria: los topes de seguridad -----------------------------


async def test_potencia_por_encima_del_tope_se_rechaza(inversor: Sungrow, monkeypatch) -> None:
    escrituras: list[tuple[int, int]] = []

    async def falso_escribir(direccion: int, valor: int) -> None:
        escrituras.append((direccion, valor))

    monkeypatch.setattr(inversor, "_escribir", falso_escribir)

    with pytest.raises(Exception, match="tope de seguridad"):
        await inversor.fijar_modo_bateria("cargar", 99000)

    assert escrituras == []  # no se escribio nada en el inversor


async def test_cargar_escribe_los_tres_registros(inversor: Sungrow, monkeypatch) -> None:
    escrituras: list[tuple[int, int]] = []

    async def falso_escribir(direccion: int, valor: int) -> None:
        escrituras.append((direccion, valor))

    monkeypatch.setattr(inversor, "_escribir", falso_escribir)
    resultado = await inversor.fijar_modo_bateria("cargar", 3000)

    assert escrituras == [(13049, 2), (13051, 3000), (13050, 0xAA)]
    assert resultado["potencia_w"] == 3000


async def test_autoconsumo_devuelve_el_control_al_inversor(
    inversor: Sungrow, monkeypatch
) -> None:
    escrituras: list[tuple[int, int]] = []

    async def falso_escribir(direccion: int, valor: int) -> None:
        escrituras.append((direccion, valor))

    monkeypatch.setattr(inversor, "_escribir", falso_escribir)
    await inversor.fijar_modo_bateria("autoconsumo")

    assert escrituras == [(13050, 0xCC), (13049, 0)]


async def test_modo_invalido(inversor: Sungrow) -> None:
    with pytest.raises(Exception, match="no valido"):
        await inversor.fijar_modo_bateria("turbo", 1000)


async def test_cargar_sin_potencia(inversor: Sungrow) -> None:
    with pytest.raises(Exception, match="potencia"):
        await inversor.fijar_modo_bateria("cargar")


async def test_potencia_cero_se_rechaza(inversor: Sungrow) -> None:
    with pytest.raises(Exception, match="mayor que 0"):
        await inversor.fijar_modo_bateria("descargar", 0)


async def test_ajuste_de_inversion_llega_al_adaptador(settings: Settings, monkeypatch) -> None:
    """El ajuste de configuracion tiene que cablearse de verdad, no quedarse en el .env."""
    from casa_ai.app import Aplicacion

    invertido = settings.model_copy(update={"sungrow_invertir_signo_red": True})
    app = Aplicacion(invertido)
    assert app.ctx.energia._invertir_red is True

    normal = Aplicacion(settings)
    assert normal.ctx.energia._invertir_red is False


# --- Validacion compartida de las ordenes de bateria -----------------------


def test_el_tope_de_potencia_es_el_mismo_para_las_dos_fuentes() -> None:
    """Vivia duplicado en `Sungrow` y en `EnergiaHA`. Ahora es un solo sitio."""
    from casa_ai.adapters.energia_base import validar_orden_bateria

    assert validar_orden_bateria("Cargar", 3000, 5000) == ("cargar", 3000)

    with pytest.raises(AdapterError, match="tope de seguridad"):
        validar_orden_bateria("cargar", 6000, 5000)
    with pytest.raises(AdapterError, match="mayor que 0"):
        validar_orden_bateria("cargar", 0, 5000)
    with pytest.raises(AdapterError, match="hace falta indicar la potencia"):
        validar_orden_bateria("descargar", None, 5000)
    with pytest.raises(AdapterError, match="no valido"):
        validar_orden_bateria("apagar", 100, 5000)
    with pytest.raises(AdapterError, match="no lleva potencia"):
        validar_orden_bateria("parar", 100, 5000)


def test_las_dos_fuentes_cumplen_el_contrato(settings: Settings) -> None:
    """Sin el Protocol, `Contexto.energia` era `Any` y cada consumidor inventaba
    su propio `getattr(..., "puede_controlar", X)` con un default distinto: el
    panel podia decir que si se controla la bateria mientras el registro
    ocultaba la herramienta."""
    from casa_ai.adapters.energia_base import FuenteEnergia
    from casa_ai.adapters.energia_ha import EnergiaHA
    from casa_ai.adapters.homeassistant import HomeAssistant
    from casa_ai.settings import EnergiaHAConfig

    via_ha = EnergiaHA(settings, HomeAssistant(settings), EnergiaHAConfig())
    for fuente in (Sungrow(settings), via_ha):
        assert isinstance(fuente, FuenteEnergia)
        # Ninguna necesita que nadie adivine su capacidad.
        assert isinstance(fuente.puede_controlar, bool)

    # Modbus siempre puede: los registros de control estan ahi.
    assert Sungrow(settings).puede_controlar is True
    # Via HA depende de que la integracion exponga entidades de control.
    assert via_ha.puede_controlar is False
