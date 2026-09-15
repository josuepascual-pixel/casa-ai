"""Herramientas de domotica: la traduccion de acciones a servicios de HA.

Es la herramienta mas usada del sistema: casi todo lo que el usuario pide
acaba aqui. La logica de traduccion y los rangos de validacion no estaban
cubiertos.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from casa_ai.adapters.base import AdapterError
from casa_ai.adapters.homeassistant import HomeAssistant
from casa_ai.agent.registry import Contexto
from casa_ai.settings import Inventario, Settings
from casa_ai.store import Store
from casa_ai.tools.casa import HERRAMIENTAS

from .conftest import HA_URL
from .dobles import contexto, entidad, estado_ha, estados_ha, servicio_ha

ACCION = next(h for h in HERRAMIENTAS if h.nombre == "casa_accion")
ESTADO = next(h for h in HERRAMIENTAS if h.nombre == "casa_estado")
ESCENA = next(h for h in HERRAMIENTAS if h.nombre == "casa_escena")


@pytest.fixture
def ctx(settings: Settings, store: Store, inventario: Inventario) -> Contexto:
    return contexto(settings, inventario, store, ha=HomeAssistant(settings))




def _cuerpo(ruta) -> dict[str, Any]:
    import json

    return json.loads(ruta.calls.last.request.content)


# --- Traduccion de acciones -------------------------------------------------


@respx.mock
async def test_encender_y_apagar(ctx: Contexto) -> None:
    on = servicio_ha("light", "turn_on")
    off = servicio_ha("light", "turn_off")

    await ACCION.handler(ctx, entidad="light.salon", accion="encender")
    await ACCION.handler(ctx, entidad="light.salon", accion="apagar")

    assert on.called and off.called
    assert _cuerpo(on)["entity_id"] == "light.salon"
    await ctx.ha.cerrar()


@respx.mock
async def test_los_alias_se_resuelven(ctx: Contexto) -> None:
    """El inventario declara "luz salon" -> light.salon."""
    on = servicio_ha("light", "turn_on")
    await ACCION.handler(ctx, entidad="luz salon", accion="encender")
    assert _cuerpo(on)["entity_id"] == "light.salon"
    await ctx.ha.cerrar()


@respx.mock
async def test_brillo(ctx: Contexto) -> None:
    on = servicio_ha("light", "turn_on")
    await ACCION.handler(ctx, entidad="light.salon", accion="brillo", valor=40)
    assert _cuerpo(on)["brightness_pct"] == 40
    await ctx.ha.cerrar()


@respx.mock
async def test_el_brillo_se_acota(ctx: Contexto) -> None:
    on = servicio_ha("light", "turn_on")
    await ACCION.handler(ctx, entidad="light.salon", accion="brillo", valor=250)
    assert _cuerpo(on)["brightness_pct"] == 100
    await ctx.ha.cerrar()


async def test_brillo_sin_valor_falla_claro(ctx: Contexto) -> None:
    with pytest.raises(AdapterError, match="0 a 100"):
        await ACCION.handler(ctx, entidad="light.salon", accion="brillo")
    await ctx.ha.cerrar()


@respx.mock
async def test_persianas(ctx: Contexto) -> None:
    estado_ha("cover.persiana_salon", "closed", device_class="shutter")
    arriba = servicio_ha("cover", "open_cover")
    posicion = servicio_ha("cover", "set_cover_position")

    await ACCION.handler(ctx, entidad="cover.persiana_salon", accion="subir")
    await ACCION.handler(ctx, entidad="cover.persiana_salon", accion="posicion", valor=30)

    assert arriba.called
    assert _cuerpo(posicion)["position"] == 30
    await ctx.ha.cerrar()


async def test_posicion_sin_valor_falla_claro(ctx: Contexto) -> None:
    with pytest.raises(AdapterError, match="0 \\(cerrada\\)"):
        await ACCION.handler(ctx, entidad="cover.persiana_salon", accion="posicion")
    await ctx.ha.cerrar()


@respx.mock
async def test_temperatura_de_clima(ctx: Contexto) -> None:
    ruta = servicio_ha("climate", "set_temperature")
    await ACCION.handler(ctx, entidad="climate.salon", accion="temperatura", valor=21.5)
    assert _cuerpo(ruta)["temperature"] == 21.5
    await ctx.ha.cerrar()


@respx.mock
async def test_el_parametro_temperatura_tambien_vale(ctx: Contexto) -> None:
    ruta = servicio_ha("climate", "set_temperature")
    await ACCION.handler(
        ctx, entidad="climate.salon", accion="temperatura", temperatura=22
    )
    assert _cuerpo(ruta)["temperature"] == 22.0
    await ctx.ha.cerrar()


async def test_una_temperatura_absurda_de_clima_se_rechaza(ctx: Contexto) -> None:
    with pytest.raises(AdapterError, match="fuera del rango"):
        await ACCION.handler(ctx, entidad="climate.salon", accion="temperatura", valor=45)
    await ctx.ha.cerrar()


@respx.mock
async def test_el_spa_admite_temperaturas_de_agua(ctx: Contexto) -> None:
    """El spa y el termo son `water_heater` y van a 38-60 C: el rango de
    habitacion (5-32) los rechazaba a todos."""
    ruta = servicio_ha("water_heater", "set_temperature")
    await ACCION.handler(ctx, entidad="water_heater.spa", accion="temperatura", valor=38)
    assert _cuerpo(ruta)["temperature"] == 38.0
    await ctx.ha.cerrar()


async def test_el_agua_tambien_tiene_tope(ctx: Contexto) -> None:
    with pytest.raises(AdapterError, match="fuera del rango"):
        await ACCION.handler(
            ctx, entidad="water_heater.spa", accion="temperatura", valor=90
        )
    await ctx.ha.cerrar()


@respx.mock
async def test_modo_de_clima_envia_el_modo(ctx: Contexto) -> None:
    """`modo` mapeaba a set_hvac_mode sin enviar el modo: la llamada no hacia
    nada porque faltaba el parametro."""
    ruta = servicio_ha("climate", "set_hvac_mode")
    await ACCION.handler(ctx, entidad="climate.salon", accion="modo", modo="heat")
    assert _cuerpo(ruta)["hvac_mode"] == "heat"
    await ctx.ha.cerrar()


@respx.mock
async def test_modo_sin_indicar_cual_dice_los_que_valen(ctx: Contexto) -> None:
    respx.get(f"{HA_URL}/api/states/climate.salon").mock(
        return_value=httpx.Response(200, json={
            "entity_id": "climate.salon", "state": "heat",
            "attributes": {"hvac_modes": ["off", "heat", "cool"]},
        })
    )
    with pytest.raises(AdapterError, match="off, heat, cool"):
        await ACCION.handler(ctx, entidad="climate.salon", accion="modo")
    await ctx.ha.cerrar()


async def test_si_no_se_pueden_leer_los_modos_el_error_sigue_siendo_util(
    ctx: Contexto,
) -> None:
    """La ruta de error no debe depender de que la red responda."""
    with pytest.raises(AdapterError, match="casa_estado"):
        await ACCION.handler(ctx, entidad="climate.salon", accion="modo")
    await ctx.ha.cerrar()


@respx.mock
async def test_modo_de_agua_caliente_usa_su_propio_servicio(ctx: Contexto) -> None:
    ruta = servicio_ha("water_heater", "set_operation_mode")
    await ACCION.handler(ctx, entidad="water_heater.spa", accion="modo", modo="eco")
    assert _cuerpo(ruta)["operation_mode"] == "eco"
    await ctx.ha.cerrar()


async def test_no_se_cambia_de_modo_una_luz(ctx: Contexto) -> None:
    with pytest.raises(AdapterError, match="No se cambiar de modo"):
        await ACCION.handler(ctx, entidad="light.salon", accion="modo", modo="heat")
    await ctx.ha.cerrar()


async def test_una_entidad_inventada_falla_claro(ctx: Contexto) -> None:
    with pytest.raises(AdapterError, match="casa_buscar_entidades"):
        await ACCION.handler(ctx, entidad="el salon", accion="encender")
    await ctx.ha.cerrar()


async def test_una_accion_fuera_de_la_lista_se_bloquea(ctx: Contexto) -> None:
    """`accion` era texto libre y acababa como nombre de servicio: `unlock`,
    `alarm_arm_away`, `vacuum.start`... Ahora es una lista cerrada."""
    with pytest.raises(AdapterError, match="no es una accion de casa_accion"):
        await ACCION.handler(ctx, entidad="lock.puerta", accion="unlock")
    with pytest.raises(AdapterError, match="no es una accion de casa_accion"):
        await ACCION.handler(ctx, entidad="alarm_control_panel.casa", accion="alarm_arm_away")
    assert set(ACCION.esquema["properties"]["accion"]["enum"]) >= {"subir", "modo", "pulsar"}
    await ctx.ha.cerrar()


# --- Puertas y portones -----------------------------------------------------


@respx.mock
async def test_un_garaje_no_se_abre_con_casa_accion(ctx: Contexto) -> None:
    """Un cover con device_class garage/gate/door es abrir la casa: va por la
    puerta estrecha de riesgo alto, como la cerradura."""
    estado_ha("cover.garaje", "closed", device_class="garage")
    abrir = servicio_ha("cover", "open_cover")
    with pytest.raises(AdapterError, match="casa_abrir_acceso"):
        await ACCION.handler(ctx, entidad="cover.garaje", accion="subir")
    assert not abrir.called
    # Cerrarlo si se puede: no abre nada.
    cerrar = servicio_ha("cover", "close_cover")
    await ACCION.handler(ctx, entidad="cover.garaje", accion="bajar")
    assert cerrar.called
    await ctx.ha.cerrar()


@respx.mock
async def test_casa_abrir_acceso_abre_solo_accesos(ctx: Contexto) -> None:
    from casa_ai.agent.registry import Riesgo
    from casa_ai.tools import herramienta

    abrir_acceso = herramienta("casa_abrir_acceso")
    assert abrir_acceso.riesgo is Riesgo.ALTO and not abrir_acceso.para_ninos

    estado_ha("cover.garaje", "closed", device_class="garage")
    estado_ha("cover.persiana_salon", "closed", device_class="shutter")
    abrir = servicio_ha("cover", "open_cover")

    await abrir_acceso.handler(ctx, entidad="cover.garaje")
    assert _cuerpo(abrir)["entity_id"] == "cover.garaje"
    with pytest.raises(AdapterError, match="no es una puerta ni un porton"):
        await abrir_acceso.handler(ctx, entidad="cover.persiana_salon")
    await ctx.ha.cerrar()


# --- Escenas ----------------------------------------------------------------


@respx.mock
async def test_escena_por_entity_id(ctx: Contexto) -> None:
    ruta = servicio_ha("scene", "turn_on")
    await ESCENA.handler(ctx, nombre="scene.cine")
    assert _cuerpo(ruta)["entity_id"] == "scene.cine"
    await ctx.ha.cerrar()


@respx.mock
async def test_escena_por_nombre_unico(ctx: Contexto) -> None:
    estados_ha(entidad("scene.modo_cine", friendly_name="Modo cine"))
    ruta = servicio_ha("scene", "turn_on")
    await ESCENA.handler(ctx, nombre="cine")
    assert _cuerpo(ruta)["entity_id"] == "scene.modo_cine"
    await ctx.ha.cerrar()


@respx.mock
async def test_escena_ambigua_pide_concretar(ctx: Contexto) -> None:
    estados_ha(entidad("scene.cine_noche"), entidad("scene.cine_dia"))
    with pytest.raises(AdapterError, match="Concreta cual"):
        await ESCENA.handler(ctx, nombre="cine")
    await ctx.ha.cerrar()


@respx.mock
async def test_escena_inexistente(ctx: Contexto) -> None:
    estados_ha()
    with pytest.raises(AdapterError, match="No encontre"):
        await ESCENA.handler(ctx, nombre="fiesta")
    await ctx.ha.cerrar()


# --- Estado -----------------------------------------------------------------


@respx.mock
async def test_estado_limpia_los_atributos_de_ruido(ctx: Contexto) -> None:
    respx.get(f"{HA_URL}/api/states/light.salon").mock(
        return_value=httpx.Response(200, json={
            "entity_id": "light.salon", "state": "on",
            "attributes": {
                "friendly_name": "Luz salon", "brightness": 200,
                "icon": "mdi:lightbulb", "supported_features": 63,
                "supported_color_modes": ["hs"],
            },
            "last_changed": "2026-09-14T10:00:00Z",
        })
    )
    r = await ESTADO.handler(ctx, entidad="light.salon")

    assert r["nombre"] == "Luz salon"
    assert r["atributos"]["brightness"] == 200
    assert "icon" not in r["atributos"]
    assert "supported_features" not in r["atributos"]
    await ctx.ha.cerrar()
