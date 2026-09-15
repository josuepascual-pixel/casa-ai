"""Adaptador BluOS: parseo del XML y resolucion de zonas."""

from __future__ import annotations

import httpx
import pytest
import respx

from casa_ai.adapters.base import AdapterError
from casa_ai.adapters.bluos import BluOS
from casa_ai.settings import Inventario

XML_STATUS = """<?xml version="1.0" encoding="UTF-8"?>
<status etag="abc">
  <state>play</state>
  <volume>34</volume>
  <mute>0</mute>
  <title1>Blue in Green</title1>
  <title2>Miles Davis</title2>
  <title3>Kind of Blue</title3>
  <service>Qobuz</service>
  <secs>72</secs>
  <totlen>337</totlen>
</status>
"""

XML_PRESETS = """<?xml version="1.0" encoding="UTF-8"?>
<presets>
  <preset id="1" name="Radio 3" url="http://radio3"/>
  <preset id="2" name="Jazz" url="http://jazz"/>
</presets>
"""


@pytest.fixture
def musica(inventario: Inventario) -> BluOS:
    return BluOS(inventario)


@respx.mock
async def test_estado_parsea_lo_que_suena(musica: BluOS) -> None:
    respx.get("http://10.0.0.50:11000/Status").mock(
        return_value=httpx.Response(200, text=XML_STATUS)
    )
    estado = await musica.estado("Salon")

    assert estado["estado"] == "play"
    assert estado["volumen"] == 34
    assert estado["silenciado"] is False
    assert estado["titulo"] == "Blue in Green"
    assert estado["artista"] == "Miles Davis"
    assert estado["servicio"] == "Qobuz"
    await musica.cerrar()


@respx.mock
async def test_se_puede_referir_por_zona(musica: BluOS) -> None:
    """"pon musica en la cocina" tiene que encontrar el reproductor de la cocina."""
    respx.get("http://10.0.0.51:11000/Status").mock(
        return_value=httpx.Response(200, text=XML_STATUS)
    )
    estado = await musica.estado("cocina")
    assert estado["reproductor"] == "Cocina"
    await musica.cerrar()


async def test_reproductor_desconocido_lista_los_validos(musica: BluOS) -> None:
    with pytest.raises(AdapterError, match="Salon, Cocina"):
        await musica.estado("buhardilla")
    await musica.cerrar()


@respx.mock
async def test_volumen_se_limita_al_rango(musica: BluOS) -> None:
    ruta = respx.get("http://10.0.0.50:11000/Volume").mock(
        return_value=httpx.Response(200, text="<volume>100</volume>")
    )
    respx.get("http://10.0.0.50:11000/Status").mock(
        return_value=httpx.Response(200, text=XML_STATUS)
    )
    await musica.control("volumen", "Salon", 250)

    assert ruta.calls.last.request.url.params["level"] == "100"
    await musica.cerrar()


@respx.mock
async def test_volumen_sin_valor_falla_claro(musica: BluOS) -> None:
    with pytest.raises(AdapterError, match="0 a 100"):
        await musica.control("volumen", "Salon")
    await musica.cerrar()


@respx.mock
async def test_presets(musica: BluOS) -> None:
    respx.get("http://10.0.0.50:11000/Presets").mock(
        return_value=httpx.Response(200, text=XML_PRESETS)
    )
    presets = await musica.presets("Salon")
    assert [p["nombre"] for p in presets] == ["Radio 3", "Jazz"]
    await musica.cerrar()


@respx.mock
async def test_agrupar_manda_addslave_al_maestro(musica: BluOS) -> None:
    ruta = respx.get("http://10.0.0.50:11000/AddSlave").mock(
        return_value=httpx.Response(200, text="<addSlave/>")
    )
    resultado = await musica.agrupar("Salon", ["Cocina"])

    assert resultado["agrupados"] == ["Cocina"]
    assert ruta.calls.last.request.url.params["slave"] == "10.0.0.51"
    await musica.cerrar()


@respx.mock
async def test_un_reproductor_caido_no_tumba_el_resto(musica: BluOS) -> None:
    respx.get("http://10.0.0.50:11000/Status").mock(
        return_value=httpx.Response(200, text=XML_STATUS)
    )
    respx.get("http://10.0.0.51:11000/Status").mock(
        side_effect=httpx.ConnectError("sin ruta al host")
    )
    estados = await musica.estado_todos()

    assert estados[0]["titulo"] == "Blue in Green"
    assert "error" in estados[1]
    await musica.cerrar()


async def test_sin_inventario_lo_dice(settings) -> None:
    vacio = BluOS(Inventario())
    assert vacio.configurado is False
    with pytest.raises(AdapterError, match="config/config.yaml"):
        await vacio.estado()
    await vacio.cerrar()


# --- Casos que faltaban -----------------------------------------------------


@respx.mock
async def test_sin_nombre_usa_el_primero_del_inventario(musica: BluOS) -> None:
    """Para que "pon musica" funcione sin tener que decir la zona."""
    respx.get("http://10.0.0.50:11000/Status").mock(
        return_value=httpx.Response(200, text=XML_STATUS)
    )
    estado = await musica.estado()
    assert estado["reproductor"] == "Salon"
    await musica.cerrar()


@respx.mock
async def test_un_http_de_error_dice_que_reproductor_fue(musica: BluOS) -> None:
    respx.get("http://10.0.0.50:11000/Status").mock(
        return_value=httpx.Response(503, text="busy")
    )
    with pytest.raises(AdapterError, match="Salon respondio HTTP 503"):
        await musica.estado("Salon")
    await musica.cerrar()


@respx.mock
async def test_una_respuesta_vacia_no_revienta(musica: BluOS) -> None:
    """Varios endpoints de BluOS contestan con el cuerpo vacio."""
    respx.get("http://10.0.0.50:11000/Play").mock(
        return_value=httpx.Response(200, text="")
    )
    respx.get("http://10.0.0.50:11000/Status").mock(
        return_value=httpx.Response(200, text=XML_STATUS)
    )
    estado = await musica.control("play", "Salon")
    assert estado["estado"] == "play"
    await musica.cerrar()


@respx.mock
async def test_un_xml_roto_se_explica(musica: BluOS) -> None:
    respx.get("http://10.0.0.50:11000/Status").mock(
        return_value=httpx.Response(200, text="<status><no cerrado")
    )
    with pytest.raises(AdapterError, match="XML ilegible"):
        await musica.estado("Salon")
    await musica.cerrar()


async def test_estado_todos_sin_inventario(settings) -> None:
    assert await BluOS(Inventario()).estado_todos() == []


@respx.mock
async def test_un_solo_preset_llega_como_dict(musica: BluOS) -> None:
    """xmltodict devuelve un dict en vez de una lista cuando hay un elemento."""
    respx.get("http://10.0.0.50:11000/Presets").mock(
        return_value=httpx.Response(200, text=
            '<presets><preset id="1" name="Radio 3" url="http://r3"/></presets>'
        )
    )
    presets = await musica.presets("Salon")
    assert [p["nombre"] for p in presets] == ["Radio 3"]
    await musica.cerrar()


@respx.mock
async def test_sin_presets(musica: BluOS) -> None:
    respx.get("http://10.0.0.50:11000/Presets").mock(
        return_value=httpx.Response(200, text="<presets/>")
    )
    assert await musica.presets("Salon") == []
    await musica.cerrar()


@respx.mock
async def test_silenciar_y_desilenciar(musica: BluOS) -> None:
    ruta = respx.get("http://10.0.0.50:11000/Volume").mock(
        return_value=httpx.Response(200, text="<volume/>")
    )
    respx.get("http://10.0.0.50:11000/Status").mock(
        return_value=httpx.Response(200, text=XML_STATUS)
    )

    await musica.control("silenciar", "Salon")
    assert ruta.calls.last.request.url.params["mute"] == "1"

    await musica.control("desilenciar", "Salon")
    assert ruta.calls.last.request.url.params["mute"] == "0"
    await musica.cerrar()


@respx.mock
async def test_cargar_un_preset(musica: BluOS) -> None:
    ruta = respx.get("http://10.0.0.50:11000/Preset").mock(
        return_value=httpx.Response(200, text="<preset/>")
    )
    respx.get("http://10.0.0.50:11000/Status").mock(
        return_value=httpx.Response(200, text=XML_STATUS)
    )
    await musica.control("preset", "Salon", 2)
    assert ruta.calls.last.request.url.params["id"] == "2"
    await musica.cerrar()


async def test_un_preset_sin_numero_falla_claro(musica: BluOS) -> None:
    with pytest.raises(AdapterError, match="numero de preset"):
        await musica.control("preset", "Salon")
    await musica.cerrar()


async def test_una_accion_inventada_lista_las_validas(musica: BluOS) -> None:
    with pytest.raises(AdapterError, match="play, pausa, stop"):
        await musica.control("bailar", "Salon")
    await musica.cerrar()


@respx.mock
async def test_agrupar_ignora_al_propio_maestro(musica: BluOS) -> None:
    """Pedir que el salon se una a si mismo no debe mandar nada al aparato."""
    ruta = respx.get("http://10.0.0.50:11000/AddSlave").mock(
        return_value=httpx.Response(200, text="<addSlave/>")
    )
    resultado = await musica.agrupar("Salon", ["Salon"])

    assert resultado["agrupados"] == []
    assert not ruta.called
    await musica.cerrar()


@respx.mock
async def test_desagrupar_todo_por_defecto(musica: BluOS) -> None:
    """Sin lista de zonas, se sueltan todas las demas."""
    ruta = respx.get("http://10.0.0.50:11000/RemoveSlave").mock(
        return_value=httpx.Response(200, text="<removeSlave/>")
    )
    resultado = await musica.desagrupar("Salon")

    assert resultado["desagrupados"] == ["Cocina"]
    assert ruta.calls.last.request.url.params["slave"] == "10.0.0.51"
    await musica.cerrar()


@respx.mock
async def test_grupo_con_esclavos(musica: BluOS) -> None:
    respx.get("http://10.0.0.50:11000/SyncStatus").mock(
        return_value=httpx.Response(200, text=
            '<SyncStatus model="N130" name="Salon">'
            '<slave id="10.0.0.51" port="11000"/></SyncStatus>'
        )
    )
    grupo = await musica.grupo("Salon")

    assert grupo["modelo"] == "N130"
    assert grupo["es_maestro_de"] == ["10.0.0.51"]
    await musica.cerrar()


@respx.mock
async def test_grupo_con_un_solo_esclavo_como_dict(musica: BluOS) -> None:
    respx.get("http://10.0.0.51:11000/SyncStatus").mock(
        return_value=httpx.Response(200, text=
            '<SyncStatus model="PULSE"><master port="11000">10.0.0.50</master>'
            "</SyncStatus>"
        )
    )
    grupo = await musica.grupo("Cocina")

    assert grupo["es_maestro_de"] == []
    assert grupo["maestro"] == "10.0.0.50"
    await musica.cerrar()


@respx.mock
async def test_un_volumen_sin_numero_en_el_xml_no_revienta(musica: BluOS) -> None:
    respx.get("http://10.0.0.50:11000/Status").mock(
        return_value=httpx.Response(200, text="<status><volume>-</volume></status>")
    )
    estado = await musica.estado("Salon")
    assert estado["volumen"] is None
    await musica.cerrar()
