"""Home Assistant: la lista blanca de servicios es la barrera real."""

from __future__ import annotations

import httpx
import pytest
import respx

from casa_ai.adapters.base import AdapterError, NoConfigurado
from casa_ai.adapters.homeassistant import HomeAssistant
from casa_ai.settings import Settings

from .conftest import HA_URL

ESTADOS = [
    {
        "entity_id": "light.salon",
        "state": "on",
        "attributes": {"friendly_name": "Luz salon", "brightness": 200},
    },
    {
        "entity_id": "cover.persiana_salon",
        "state": "open",
        "attributes": {"friendly_name": "Persiana salon"},
    },
    {
        "entity_id": "sensor.bateria_soc",
        "state": "65",
        "attributes": {"friendly_name": "Bateria", "unit_of_measurement": "%"},
    },
]


@pytest.fixture
def ha(settings: Settings) -> HomeAssistant:
    return HomeAssistant(settings)


async def test_sin_token_no_arranca(settings: Settings) -> None:
    sin_token = HomeAssistant(settings.model_copy(update={"ha_token": None}))
    assert sin_token.configurado is False
    with pytest.raises(NoConfigurado, match="HA_TOKEN"):
        await sin_token.estados()


@respx.mock
async def test_busqueda_por_dominio_y_texto(ha: HomeAssistant) -> None:
    respx.get(f"{HA_URL}/api/states").mock(
        return_value=httpx.Response(200, json=ESTADOS)
    )
    solo_luces = await ha.buscar_entidades(dominio="light")
    assert [e["entity_id"] for e in solo_luces] == ["light.salon"]

    por_texto = await ha.buscar_entidades(texto="persiana")
    assert [e["entity_id"] for e in por_texto] == ["cover.persiana_salon"]
    await ha.cerrar()


@respx.mock
async def test_servicio_permitido_llega_a_ha(ha: HomeAssistant) -> None:
    ruta = respx.post(f"{HA_URL}/api/services/light/turn_on").mock(
        return_value=httpx.Response(200, json=[])
    )
    await ha.llamar_servicio("light", "turn_on", {"entity_id": "light.salon"})

    assert ruta.called
    await ha.cerrar()


async def test_dominio_no_permitido_se_bloquea(ha: HomeAssistant) -> None:
    # `homeassistant.stop` apagaria el propio Home Assistant.
    with pytest.raises(AdapterError, match="no permitido"):
        await ha.llamar_servicio("homeassistant", "stop", {})
    await ha.cerrar()


async def test_servicio_no_permitido_del_dominio_se_bloquea(ha: HomeAssistant) -> None:
    # Abrir cerraduras por chat no esta en la lista blanca, a proposito.
    with pytest.raises(AdapterError, match="no permitido"):
        await ha.llamar_servicio("lock", "unlock", {"entity_id": "lock.puerta"})
    await ha.cerrar()


async def test_desarmar_alarma_se_bloquea(ha: HomeAssistant) -> None:
    with pytest.raises(AdapterError, match="no permitido"):
        await ha.llamar_servicio(
            "alarm_control_panel", "alarm_disarm", {"entity_id": "alarm_control_panel.casa"}
        )
    await ha.cerrar()


@respx.mock
async def test_error_http_se_traduce_a_mensaje_util(ha: HomeAssistant) -> None:
    respx.post(f"{HA_URL}/api/services/light/turn_on").mock(
        return_value=httpx.Response(400, text="entity not found")
    )
    with pytest.raises(AdapterError, match="rechazo"):
        await ha.llamar_servicio("light", "turn_on", {"entity_id": "light.fantasma"})
    await ha.cerrar()


@respx.mock
async def test_historico_submuestrea(ha: HomeAssistant) -> None:
    serie = [{"state": str(i), "last_changed": f"2026-01-01T00:{i:02d}:00Z"} for i in range(60)]
    respx.get(url__regex=r"http://ha\.test:8123/api/history/period/.*").mock(
        return_value=httpx.Response(200, json=[serie])
    )
    puntos = await ha.historico("sensor.bateria_soc", horas=1, max_puntos=10)

    assert 0 < len(puntos) <= 12
    await ha.cerrar()


@respx.mock
async def test_snapshot_de_camara_via_proxy(ha: HomeAssistant) -> None:
    """La via que mantiene la vision cuando el backend no esta en casa."""
    jpeg = b"\xff\xd8\xff\xe0" + b"x" * 500
    respx.get(f"{HA_URL}/api/camera_proxy/camera.puerta").mock(
        return_value=httpx.Response(200, content=jpeg)
    )
    assert await ha.snapshot_camara("camera.puerta") == jpeg
    await ha.cerrar()


@respx.mock
async def test_snapshot_vacio_se_detecta(ha: HomeAssistant) -> None:
    respx.get(f"{HA_URL}/api/camera_proxy/camera.rota").mock(
        return_value=httpx.Response(200, content=b"")
    )
    with pytest.raises(AdapterError, match="imagen vacia"):
        await ha.snapshot_camara("camera.rota")
    await ha.cerrar()


@respx.mock
async def test_snapshot_de_entidad_inexistente(ha: HomeAssistant) -> None:
    respx.get(f"{HA_URL}/api/camera_proxy/camera.fantasma").mock(
        return_value=httpx.Response(404, text="not found")
    )
    with pytest.raises(AdapterError, match="no dio imagen"):
        await ha.snapshot_camara("camera.fantasma")
    await ha.cerrar()


# --- Lista blanca de scripts ------------------------------------------------


async def test_los_scripts_estan_denegados_por_defecto(ha: HomeAssistant) -> None:
    """Un script de HA ejecuta cualquier secuencia: puede abrir una cerradura,
    asi que se salta la exclusion de lock.unlock de la lista de servicios."""
    with pytest.raises(AdapterError, match="scripts_permitidos"):
        await ha.llamar_servicio(
            "script", "turn_on", {"entity_id": "script.abrir_todo"}
        )
    await ha.cerrar()


@respx.mock
async def test_un_script_declarado_si_se_puede_llamar(settings: Settings) -> None:
    con_lista = HomeAssistant(settings, ["script.bienvenida"])
    ruta = respx.post(f"{HA_URL}/api/services/script/turn_on").mock(
        return_value=httpx.Response(200, json=[])
    )

    await con_lista.llamar_servicio(
        "script", "turn_on", {"entity_id": "script.bienvenida"}
    )

    assert ruta.called
    await con_lista.cerrar()


async def test_un_script_no_declarado_se_rechaza(settings: Settings) -> None:
    con_lista = HomeAssistant(settings, ["script.bienvenida"])
    with pytest.raises(AdapterError, match="script.bienvenida"):
        await con_lista.llamar_servicio(
            "script", "turn_on", {"entity_id": "script.abrir_cerradura"}
        )
    await con_lista.cerrar()


async def test_la_lista_llega_desde_el_inventario(settings: Settings, tmp_path) -> None:
    import yaml

    from casa_ai.app import Aplicacion

    config = tmp_path / "scripts.yaml"
    config.write_text(
        yaml.safe_dump({"scripts_permitidos": ["script.buenas_noches"]}), "utf-8"
    )
    app = Aplicacion(settings.model_copy(update={"config_path": config}))

    assert app.ctx.ha._scripts == {"script.buenas_noches"}


# --- Cache corta de /api/states --------------------------------------------


@respx.mock
async def test_la_cache_colapsa_las_busquedas_de_un_mismo_turno(
    ha: HomeAssistant,
) -> None:
    """Un turno pide entidades tres o cuatro veces, y el panel cada 10 s."""
    ruta = respx.get(f"{HA_URL}/api/states").mock(
        return_value=httpx.Response(200, json=[
            {"entity_id": "light.salon", "state": "on", "attributes": {}},
        ])
    )

    await ha.buscar_entidades(dominio="light")
    await ha.buscar_entidades(texto="salon")
    await ha.estados()

    assert ruta.call_count == 1
    await ha.cerrar()


@respx.mock
async def test_una_entidad_sale_de_la_lista_ya_leida(ha: HomeAssistant) -> None:
    """Pedirlas una por una era lo que disparaba ~33 GET por pasada del panel."""
    lista = respx.get(f"{HA_URL}/api/states").mock(
        return_value=httpx.Response(200, json=[
            {"entity_id": "switch.termo", "state": "off", "attributes": {"a": 1}},
        ])
    )
    suelta = respx.get(f"{HA_URL}/api/states/switch.termo")

    await ha.estados()
    assert (await ha.estado("switch.termo"))["state"] == "off"

    assert lista.call_count == 1
    assert suelta.call_count == 0
    await ha.cerrar()


@respx.mock
async def test_escribir_tira_la_cache(ha: HomeAssistant) -> None:
    """Sin esto, un casa_accion seguido de casa_estado contaria el estado
    viejo, que es peor que no cachear nada."""
    respx.get(f"{HA_URL}/api/states").mock(
        return_value=httpx.Response(200, json=[
            {"entity_id": "light.salon", "state": "off", "attributes": {}}])
    )
    # Con la cache tirada, una entidad suelta se pide suelta: es mas barato que
    # volver a traer la lista entera.
    suelta = respx.get(f"{HA_URL}/api/states/light.salon").mock(
        return_value=httpx.Response(200, json={
            "entity_id": "light.salon", "state": "on", "attributes": {}})
    )
    respx.post(f"{HA_URL}/api/services/light/turn_on").mock(
        return_value=httpx.Response(200, json=[])
    )

    await ha.estados()
    assert (await ha.estado("light.salon"))["state"] == "off"
    assert suelta.call_count == 0  # servido de la cache

    await ha.llamar_servicio("light", "turn_on", {"entity_id": "light.salon"})
    assert (await ha.estado("light.salon"))["state"] == "on"
    assert suelta.call_count == 1
    await ha.cerrar()
