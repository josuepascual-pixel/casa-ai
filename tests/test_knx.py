"""Bus KNX de ONNA: la lista blanca de direcciones es la barrera real.

Escribir un telegrama crudo acciona un actuador sin pasar por Home Assistant,
asi que una direccion equivocada puede mover cualquier cosa de la casa. Por eso
solo se permiten las declaradas en el YAML.
"""

from __future__ import annotations

from typing import Any

import pytest

from casa_ai.adapters.base import AdapterError, NoConfigurado
from casa_ai.adapters.knx_onna import KNXOnna
from casa_ai.settings import Inventario, Settings


@pytest.fixture
def knx(settings: Settings, inventario: Inventario) -> KNXOnna:
    activo = settings.model_copy(
        update={"knx_habilitado": True, "knx_gateway_ip": "10.0.0.9"}
    )
    return KNXOnna(activo, inventario)


def test_desactivado_por_defecto(settings: Settings, inventario: Inventario) -> None:
    apagado = KNXOnna(settings, inventario)
    assert apagado.configurado is False


async def test_sin_gateway_explica_que_hace_falta(
    settings: Settings, inventario: Inventario
) -> None:
    apagado = KNXOnna(settings, inventario)
    with pytest.raises(NoConfigurado, match="KNX_GATEWAY_IP"):
        await apagado.leer("riego")


def test_listar_las_direcciones_declaradas(knx: KNXOnna) -> None:
    direcciones = knx.listar()
    por_nombre = {d["nombre"]: d for d in direcciones}

    assert por_nombre["riego"]["direccion"] == "2/1/10"
    assert por_nombre["temp exterior"]["solo_lectura"] is True


async def test_una_direccion_no_declarada_se_rechaza(knx: KNXOnna) -> None:
    """Es la proteccion de fondo: el agente no puede inventarse una direccion."""
    with pytest.raises(AdapterError, match="no esta declarada"):
        await knx.escribir("5/5/5", True)


async def test_el_error_lista_las_que_si_valen(knx: KNXOnna) -> None:
    with pytest.raises(AdapterError, match="riego"):
        await knx.escribir("9/9/9", True)


async def test_una_direccion_de_solo_lectura_no_se_escribe(knx: KNXOnna) -> None:
    with pytest.raises(AdapterError, match="solo lectura"):
        await knx.escribir("temp exterior", 21.0)


async def test_escribir_por_nombre_declarado(knx: KNXOnna, monkeypatch) -> None:
    escrituras: list[tuple[str, Any, str | None]] = []

    async def falso_write(xknx: Any, direccion: str, valor: Any, value_type: Any = None):
        escrituras.append((direccion, valor, value_type))

    async def falso_conectar() -> Any:
        return object()

    monkeypatch.setattr(knx, "_conectar", falso_conectar)
    import xknx.tools

    monkeypatch.setattr(xknx.tools, "group_value_write", falso_write)

    r = await knx.escribir("riego", True)

    # Se traduce el nombre a su direccion y se usa el DPT declarado.
    assert escrituras == [("2/1/10", True, "binary")]
    assert r["direccion"] == "2/1/10"
    assert "no confirma ejecucion" in r["detalle"]


async def test_tambien_vale_dar_la_direccion_directamente(
    knx: KNXOnna, monkeypatch
) -> None:
    escrituras: list[str] = []

    async def falso_write(xknx: Any, direccion: str, valor: Any, value_type: Any = None):
        escrituras.append(direccion)

    monkeypatch.setattr(knx, "_conectar", lambda: _nada())
    import xknx.tools

    monkeypatch.setattr(xknx.tools, "group_value_write", falso_write)

    await knx.escribir("2/1/10", False)
    assert escrituras == ["2/1/10"]


async def _nada() -> Any:
    return object()


async def test_leer_devuelve_el_valor(knx: KNXOnna, monkeypatch) -> None:
    async def falso_read(xknx: Any, direccion: str, value_type: Any = None):
        return 21.5

    monkeypatch.setattr(knx, "_conectar", lambda: _nada())
    import xknx.tools

    monkeypatch.setattr(xknx.tools, "read_group_value", falso_read)

    r = await knx.leer("temp exterior")
    assert r["valor"] == 21.5
    assert r["direccion"] == "3/2/1"


async def test_si_nadie_responde_se_dice_por_que(knx: KNXOnna, monkeypatch) -> None:
    """Una direccion sin bandera de lectura en ETS no responde, y eso es
    distinto de que el bus este caido."""
    async def falso_read(xknx: Any, direccion: str, value_type: Any = None):
        return None

    monkeypatch.setattr(knx, "_conectar", lambda: _nada())
    import xknx.tools

    monkeypatch.setattr(xknx.tools, "read_group_value", falso_read)

    with pytest.raises(AdapterError, match="bandera de lectura"):
        await knx.leer("temp exterior")


async def test_un_fallo_del_bus_se_envuelve(knx: KNXOnna, monkeypatch) -> None:
    async def falso_read(xknx: Any, direccion: str, value_type: Any = None):
        raise OSError("bus ocupado")

    monkeypatch.setattr(knx, "_conectar", lambda: _nada())
    import xknx.tools

    monkeypatch.setattr(xknx.tools, "read_group_value", falso_read)

    with pytest.raises(AdapterError, match="bus ocupado"):
        await knx.leer("riego")
