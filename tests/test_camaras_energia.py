"""Herramientas de camaras y de energia.

Las camaras tienen dos caminos (UniFi Protect directo y el proxy de Home
Assistant) y hay que probar los dos, porque el segundo es el que sostiene el
despliegue fuera de casa.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
import respx

from casa_ai.adapters.base import AdapterError
from casa_ai.adapters.energia_base import EstadoEnergia
from casa_ai.adapters.homeassistant import HomeAssistant
from casa_ai.agent.registry import Contexto
from casa_ai.settings import Inventario, Settings
from casa_ai.store import Store
from casa_ai.tools.camaras import HERRAMIENTAS as CAMARAS
from casa_ai.tools.energia import HERRAMIENTAS as ENERGIA

from .conftest import HA_URL
from .dobles import contexto, estados_ha

VER = next(h for h in CAMARAS if h.nombre == "camara_ver")
LISTAR = next(h for h in CAMARAS if h.nombre == "camaras_listar")
EVENTOS = next(h for h in CAMARAS if h.nombre == "camaras_eventos")
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 200


class UniFiFalso:
    def __init__(self, configurado: bool = True) -> None:
        self.configurado = configurado
        self.pedidas: list[tuple[str, bool]] = []

    async def camaras(self) -> list[dict[str, Any]]:
        return [{"id": "cam-abc", "nombre": "Puerta", "conectada": True}]

    async def snapshot(self, id_camara: str, *, alta_calidad: bool = True) -> bytes:
        self.pedidas.append((id_camara, alta_calidad))
        return JPEG

    async def eventos(self, *, horas: int = 2, limite: int = 20):
        return [{"tipo": "motion", "camara": "cam-abc"}]

    async def cerrar(self) -> None:
        return None


class EnergiaFalsa:
    configurado = True
    puede_controlar = True

    def __init__(self) -> None:
        self.ordenes: list[tuple[str, int | None]] = []

    async def estado(self, *, usar_cache: bool = True) -> EstadoEnergia:
        return EstadoEnergia(
            pv_w=3000, bateria_w=-500, bateria_soc=72.0, red_w=-200,
            consumo_casa_w=3700, modo_ems=0, pv_total_kwh=1234.5,
        )

    async def fijar_modo_bateria(self, modo: str, potencia_w: int | None = None):
        self.ordenes.append((modo, potencia_w))
        return {"modo": modo, "potencia_w": potencia_w}

    async def cerrar(self) -> None:
        return None


def _ctx(
    settings: Settings, store: Store, inventario: Inventario, **partes: Any
) -> Contexto:
    return contexto(settings, inventario, store, **partes)


# --- Camaras por UniFi Protect ----------------------------------------------


async def test_ver_por_unifi_devuelve_un_bloque_de_imagen(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    """Lo importante: es una imagen, no metadatos. El modelo la mira."""
    unifi = UniFiFalso()
    ctx = _ctx(settings, store, inventario, unifi=unifi)

    bloques = await VER.handler(ctx, camara="Puerta")

    assert bloques[0]["type"] == "image"
    assert base64.standard_b64decode(bloques[0]["source"]["data"]) == JPEG
    assert "solo lo que se ve" in bloques[1]["text"]
    # El alias del inventario traduce "Puerta" a su id de Protect.
    assert unifi.pedidas[0][0] == "cam1"


async def test_ver_por_el_nombre_que_tiene_en_protect(
    settings: Settings, store: Store
) -> None:
    unifi = UniFiFalso()
    ctx = _ctx(settings, store, Inventario(), unifi=unifi)

    await VER.handler(ctx, camara="puerta")

    assert unifi.pedidas[0][0] == "cam-abc"


async def test_una_captura_enorme_se_reintenta_en_calidad_normal(
    settings: Settings, store: Store, inventario: Inventario, monkeypatch
) -> None:
    from casa_ai.adapters import camaras_base
    from casa_ai.tools import camaras as modulo

    monkeypatch.setattr(camaras_base, "MAX_BYTES_IMAGEN", 10)
    monkeypatch.setattr(modulo, "MAX_BYTES_IMAGEN", 10)
    unifi = UniFiFalso()
    ctx = _ctx(settings, store, inventario, unifi=unifi)

    with pytest.raises(AdapterError, match="demasiado"):
        await VER.handler(ctx, camara="Puerta")

    # Se intento primero en alta y luego en normal antes de rendirse.
    assert [alta for _, alta in unifi.pedidas] == [True, False]


async def test_eventos_necesitan_protect(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    ctx = _ctx(settings, store, inventario, ha=HomeAssistant(settings))
    with pytest.raises(AdapterError, match="binary_sensor"):
        await EVENTOS.handler(ctx)
    await ctx.ha.cerrar()


async def test_eventos_por_unifi(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    ctx = _ctx(settings, store, inventario, unifi=UniFiFalso())
    r = await EVENTOS.handler(ctx, horas=3)
    assert r["ventana_horas"] == 3
    assert r["eventos"][0]["tipo"] == "motion"


# --- Camaras por Home Assistant (el camino del despliegue en la nube) -------


@respx.mock
async def test_ver_por_home_assistant(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    """El `id_protect` del inventario no es un entity_id de HA, asi que en este
    camino se cae a buscar la entidad por el nombre que uso el usuario."""
    respx.get(f"{HA_URL}/api/states").mock(
        return_value=httpx.Response(200, json=[
            {"entity_id": "camera.puerta", "state": "idle",
             "attributes": {"friendly_name": "Puerta"}},
        ])
    )
    respx.get(f"{HA_URL}/api/camera_proxy/camera.puerta").mock(
        return_value=httpx.Response(200, content=JPEG)
    )
    ctx = _ctx(settings, store, inventario, ha=HomeAssistant(settings))

    bloques = await VER.handler(ctx, camara="Puerta")

    assert bloques[0]["type"] == "image"
    assert "Home Assistant" in bloques[1]["text"]
    await ctx.ha.cerrar()


@respx.mock
async def test_en_ha_se_puede_declarar_el_entity_id_como_alias(
    settings: Settings, store: Store
) -> None:
    """Si declaras la camara con su entity_id de HA, se usa tal cual."""
    inv = Inventario.model_validate({
        "camaras": [{"nombre": "Garaje", "id_protect": "camera.garaje_hd"}]
    })
    respx.get(f"{HA_URL}/api/camera_proxy/camera.garaje_hd").mock(
        return_value=httpx.Response(200, content=JPEG)
    )
    ctx = _ctx(settings, store, inv, ha=HomeAssistant(settings))

    bloques = await VER.handler(ctx, camara="Garaje")

    assert bloques[0]["type"] == "image"
    await ctx.ha.cerrar()


@respx.mock
async def test_ver_por_ha_buscando_la_entidad(
    settings: Settings, store: Store
) -> None:
    respx.get(f"{HA_URL}/api/states").mock(
        return_value=httpx.Response(200, json=[
            {"entity_id": "camera.garaje", "state": "idle", "attributes": {}},
        ])
    )
    respx.get(f"{HA_URL}/api/camera_proxy/camera.garaje").mock(
        return_value=httpx.Response(200, content=JPEG)
    )
    ctx = _ctx(settings, store, Inventario(), ha=HomeAssistant(settings))

    bloques = await VER.handler(ctx, camara="garaje")

    assert bloques[0]["type"] == "image"
    await ctx.ha.cerrar()


@respx.mock
async def test_varias_camaras_parecidas_piden_concretar(
    settings: Settings, store: Store
) -> None:
    respx.get(f"{HA_URL}/api/states").mock(
        return_value=httpx.Response(200, json=[
            {"entity_id": "camera.garaje_dentro", "state": "idle", "attributes": {}},
            {"entity_id": "camera.garaje_fuera", "state": "idle", "attributes": {}},
        ])
    )
    ctx = _ctx(settings, store, Inventario(), ha=HomeAssistant(settings))

    with pytest.raises(AdapterError, match="Concreta cual"):
        await VER.handler(ctx, camara="garaje")
    await ctx.ha.cerrar()


@respx.mock
async def test_listar_por_ha_avisa_de_lo_que_no_trae(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    respx.get(f"{HA_URL}/api/states").mock(
        return_value=httpx.Response(200, json=[
            {"entity_id": "camera.puerta", "state": "idle", "attributes": {}},
        ])
    )
    ctx = _ctx(settings, store, inventario, ha=HomeAssistant(settings))

    r = await LISTAR.handler(ctx)

    assert r["via"] == "home_assistant"
    assert "UniFi Protect" in r["nota"]
    await ctx.ha.cerrar()


async def test_listar_prefiere_unifi_cuando_esta(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    ctx = _ctx(
        settings, store, inventario,
        unifi=UniFiFalso(), ha=HomeAssistant(settings),
    )
    r = await LISTAR.handler(ctx)
    assert r["via"] == "unifi_protect"
    await ctx.ha.cerrar()


async def test_sin_ningun_camino_lo_dice(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    ctx = _ctx(settings, store, inventario)
    with pytest.raises(AdapterError, match="No hay camino a las camaras"):
        await LISTAR.handler(ctx)


# --- Energia ----------------------------------------------------------------


async def test_energia_estado(settings: Settings, store: Store) -> None:
    herramienta = next(h for h in ENERGIA if h.nombre == "energia_estado")
    ctx = _ctx(settings, store, Inventario(), energia=EnergiaFalsa())

    r = await herramienta.handler(ctx)

    assert r["solar_w"] == 3000
    assert r["bateria_estado"] == "descargando"
    assert r["red_estado"] == "importando"
    assert r["acumulados_kwh"]["solar_generado"] == 1234.5


async def test_modo_bateria_llega_al_adaptador(settings: Settings, store: Store) -> None:
    herramienta = next(h for h in ENERGIA if h.nombre == "energia_modo_bateria")
    energia = EnergiaFalsa()
    ctx = _ctx(settings, store, Inventario(), energia=energia)

    await herramienta.handler(ctx, modo="cargar", potencia_w=2500)

    assert energia.ordenes == [("cargar", 2500)]


def test_el_resumen_de_confirmacion_es_legible() -> None:
    herramienta = next(h for h in ENERGIA if h.nombre == "energia_modo_bateria")
    assert herramienta.resumen_confirmacion is not None

    assert "autoconsumo" in herramienta.resumir({"modo": "autoconsumo"})
    assert "reposo" in herramienta.resumir({"modo": "parar"})
    texto = herramienta.resumir({"modo": "cargar", "potencia_w": 3000})
    assert "cargar" in texto and "3000" in texto


@respx.mock
async def test_historico_sin_entidad_propone_candidatas(
    settings: Settings, store: Store
) -> None:
    respx.get(f"{HA_URL}/api/states").mock(
        return_value=httpx.Response(200, json=[
            {"entity_id": "sensor.battery_level", "state": "70",
             "attributes": {"friendly_name": "Bateria"}},
        ])
    )
    herramienta = next(h for h in ENERGIA if h.nombre == "energia_historico")
    ctx = _ctx(settings, store, Inventario(), ha=HomeAssistant(settings))

    r = await herramienta.handler(ctx)

    assert "Indica que entidad" in r["aviso"]
    assert r["entidades"]
    await ctx.ha.cerrar()


async def test_historico_sin_home_assistant_lo_explica(
    settings: Settings, store: Store
) -> None:
    herramienta = next(h for h in ENERGIA if h.nombre == "energia_historico")
    ctx = _ctx(settings, store, Inventario())

    r = await herramienta.handler(ctx)

    assert "series temporales" in r


# --- Identificadores por sistema -------------------------------------------


@respx.mock
async def test_cada_sistema_tiene_su_identificador(
    settings: Settings, store: Store
) -> None:
    """`id_protect` guardaba a veces un entity_id de Home Assistant, y se
    distinguia mirando si empezaba por "camera.". Ahora cada sistema tiene el
    suyo y se puede declarar los dos."""
    inventario = Inventario.model_validate({
        "camaras": [{"nombre": "Puerta", "id_protect": "cam1",
                     "entidad_ha": "camera.puerta_hd"}]
    })
    estados_ha()  # no hace falta buscar: la entidad esta declarada
    respx.get(f"{HA_URL}/api/camera_proxy/camera.puerta_hd").mock(
        return_value=httpx.Response(200, content=JPEG)
    )

    # Con UniFi va por Protect y usa su id.
    unifi = UniFiFalso()
    ctx = _ctx(settings, store, inventario, unifi=unifi)
    await VER.handler(ctx, camara="Puerta")
    assert unifi.pedidas[0][0] == "cam1"

    # Sin UniFi va por Home Assistant y usa la entidad declarada, sin buscar.
    ctx = _ctx(settings, store, inventario, ha=HomeAssistant(settings))
    bloques = await VER.handler(ctx, camara="Puerta")
    assert base64.standard_b64decode(bloques[0]["source"]["data"]) == JPEG
    await ctx.ha.cerrar()


@respx.mock
async def test_el_entity_id_en_id_protect_sigue_funcionando(
    settings: Settings, store: Store
) -> None:
    """Era la unica forma de declararlo y hay casas con el YAML ya escrito asi."""
    inventario = Inventario.model_validate({
        "camaras": [{"nombre": "Puerta", "id_protect": "camera.vieja"}]
    })
    respx.get(f"{HA_URL}/api/camera_proxy/camera.vieja").mock(
        return_value=httpx.Response(200, content=JPEG)
    )
    ctx = _ctx(settings, store, inventario, ha=HomeAssistant(settings))

    bloques = await VER.handler(ctx, camara="Puerta")

    assert base64.standard_b64decode(bloques[0]["source"]["data"]) == JPEG
    await ctx.ha.cerrar()
