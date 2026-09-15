"""Quien habla decide que se le ofrece. La identidad es el canal, no la voz."""

from __future__ import annotations

import json

import pytest
import respx

from casa_ai.adapters.base import AdapterError
from casa_ai.agent.prompts import construir_system
from casa_ai.agent.registry import Riesgo
from casa_ai.agent.safety import Ejecutor
from casa_ai.settings import Inventario, Persona, Settings
from casa_ai.store import Store
from casa_ai.tools import construir_registro

from .dobles import contexto, entidad, estado_ha, estados_ha, servicio_ha

PERSONAS = {
    "personas": [
        {"nombre": "Josue", "nivel": "dueno", "telegram": ["1"]},
        {"nombre": "Ana", "nivel": "adulto", "telegram": ["2"], "whatsapp": ["+34600"]},
        {"nombre": "Leo", "nivel": "nino", "dispositivos": ["satelite-leo"]},
    ]
}


def test_la_persona_sale_del_canal_y_falla_cerrado() -> None:
    inv = Inventario.model_validate(PERSONAS)
    assert inv.persona_de("telegram", "1").nombre == "Josue"
    assert inv.persona_de("whatsapp", "+34600").nombre == "Ana"
    assert inv.persona_de("voz", "satelite-leo").es_nino
    # El panel por ingress usa la misma identidad que la voz: el usuario de HA.
    assert inv.persona_de("panel", inv.personas[2].dispositivos[0]).es_nino
    # Autorizado pero no declarado: nino. Y el token del API es del dueno.
    desconocido = inv.persona_de("telegram", "999")
    assert desconocido is not None and desconocido.es_nino
    assert inv.persona_de("http", "api").nivel == "dueno"
    # Las rutinas hablan por su propio canal y son del dueno: con personas
    # declaradas, el informe de la manana no puede quedarse en nivel nino.
    assert inv.persona_de("rutina", "programada").nivel == "dueno"
    # Sin personas declaradas, nadie es nino: como hasta ahora...
    assert Inventario().persona_de("telegram", "999") is None
    # ...salvo un aparato de voz sin declarar, que es el cuarto del nino
    # hasta que se diga lo contrario.
    assert Inventario().persona_de("voz", "satelite-nuevo").es_nino
    assert inv.persona_de("voz", "satelite-nuevo").es_nino


@pytest.fixture
def registro():
    return construir_registro()


def _ctx(settings: Settings, store: Store, persona: Persona | None):
    return contexto(settings, Inventario(), store, por_defecto=True, persona=persona)


def test_un_nino_solo_ve_lo_marcado_y_nada_de_riesgo(settings, store, registro) -> None:
    nino = _ctx(settings, store, Persona(nombre="Leo", nivel="nino"))
    adulto = _ctx(settings, store, Persona(nombre="Ana", nivel="adulto"))
    nombres_nino = {h.nombre for h in registro.disponibles(nino)}
    nombres_adulto = {h.nombre for h in registro.disponibles(adulto)}

    assert nombres_nino < nombres_adulto
    assert "musica_control" in nombres_nino and "casa_accion" in nombres_nino
    for prohibida in ("camara_ver", "camaras_listar", "red_clientes", "energia_modo_bateria",
                      "knx_escribir", "auditoria_acciones", "informe_casa"):
        assert prohibida not in nombres_nino, prohibida
    assert all(h.riesgo is not Riesgo.ALTO for h in registro.disponibles(nino))
    assert registro.ausentes(nino)["camara_ver"] == "no es para ninos (Leo)"


def test_ninguna_herramienta_de_riesgo_alto_es_para_ninos(registro) -> None:
    """La lista blanca se puede ampliar; esto es lo que nunca puede entrar."""
    for h in registro.herramientas.values():
        assert not (h.riesgo is Riesgo.ALTO and h.para_ninos), h.nombre


async def test_el_ejecutor_no_ejecuta_lo_que_no_se_ofrece(settings, store, registro) -> None:
    nino = _ctx(settings, store, Persona(nombre="Leo", nivel="nino"))
    resultado, es_error = await Ejecutor(registro, nino).ejecutar("red_clientes", {})
    assert es_error and "no esta disponible para quien habla" in resultado


def test_el_prompt_lista_las_personas_y_como_tratar_a_un_nino(settings) -> None:
    prompt = construir_system(settings, Inventario.model_validate(PERSONAS))
    assert "- Leo (nino)" in prompt and "- Ana (adulto)" in prompt
    assert "tutealo" in prompt and "Nunca le des datos de camaras" in prompt
    assert "Personas de la casa" not in construir_system(settings, Inventario())


async def test_un_nino_recibe_home_assistant_restringido(settings, store) -> None:
    """El veto esta en el adaptador, no en cada herramienta: asi
    dispositivos_estado o una herramienta nueva no pueden saltarselo."""
    from casa_ai.adapters.homeassistant import HomeAssistant, HomeAssistantRestringido
    from casa_ai.app import Aplicacion

    app = Aplicacion.__new__(Aplicacion)
    app.inventario = Inventario.model_validate(PERSONAS)
    app.ctx = contexto(settings, app.inventario, store, ha=HomeAssistant(settings))

    nino = app.contexto_para("voz", "satelite-leo", "voz:satelite-leo")
    assert nino.es_nino and isinstance(nino.ha, HomeAssistantRestringido)
    adulto = app.contexto_para("telegram", "2", "telegram:2")
    assert isinstance(adulto.ha, HomeAssistant)


@respx.mock
async def test_la_vista_restringida_no_toca_ni_mira_fuera_de_su_lista(settings) -> None:
    """El estado de una camara trae el token de su stream y el de una persona
    dice quien esta en casa: la lista blanca vale para leer, no solo para tocar."""
    from casa_ai.adapters.homeassistant import DOMINIOS_PARA_NINOS, HomeAssistant

    estados_ha(entidad("light.leo"), entidad("camera.leo"), entidad("person.papa"),
               entidad("switch.bomba_piscina"))
    encendidos = servicio_ha("light", "turn_on")
    vista = HomeAssistant(settings).restringido_a(DOMINIOS_PARA_NINOS)

    for entidad_vetada in ("camera.leo", "person.papa", "lock.puerta"):
        with pytest.raises(AdapterError, match="ni consultar"):
            await vista.estado(entidad_vetada)
    with pytest.raises(AdapterError, match="ni consultar"):
        await vista.buscar_entidades(dominio="camera")
    with pytest.raises(AdapterError, match="ni consultar"):
        await vista.llamar_servicio("switch", "turn_on", {"entity_id": "switch.bomba_piscina"})
    with pytest.raises(AdapterError, match="ni consultar"):
        await vista.llamar_servicio("light", "turn_on", {"entity_id": ["light.a", "lock.puerta"]})

    assert [e["entity_id"] for e in await vista.estados()] == ["light.leo"]
    # `cover` esta permitido, pero un garaje no es una persiana.
    estado_ha("cover.garaje", "closed", device_class="garage")
    with pytest.raises(AdapterError, match="puerta o un porton"):
        await vista.llamar_servicio("cover", "open_cover", {"entity_id": "cover.garaje"})
    assert [e["entity_id"] for e in await vista.buscar_entidades(texto="leo")] == ["light.leo"]
    assert (await vista.estado("light.leo"))["entity_id"] == "light.leo"
    await vista.llamar_servicio("light", "turn_on", {"entity_id": "light.leo"})
    assert encendidos.called


# --- Entidades privadas -----------------------------------------------------

CON_PRIVADAS = {
    "personas": [
        {"nombre": "Josue", "nivel": "dueno", "telegram": ["1"],
         "privadas": ["input_number.peso_josue"]},
        {"nombre": "Ana", "nivel": "adulto", "telegram": ["2"],
         "privadas": ["input_number.peso_ana"]},
        {"nombre": "Leo", "nivel": "nino", "dispositivos": ["satelite-leo"]},
    ]
}


def test_las_privadas_de_los_demas_no_existen_para_nadie_mas() -> None:
    inv = Inventario.model_validate(CON_PRIVADAS)
    josue = inv.persona_de("telegram", "1")
    assert inv.privadas_ajenas(josue) == {"input_number.peso_ana"}
    assert inv.privadas_ajenas(inv.persona_de("telegram", "2")) == {"input_number.peso_josue"}
    # Una rutina, el API, un chat sin registrar: todas. El token del API no es
    # dueno del peso de nadie.
    for canal, usuario in (("rutina", "programada"), ("http", "api"), ("telegram", "999")):
        assert inv.privadas_ajenas(inv.persona_de(canal, usuario)) == {
            "input_number.peso_ana", "input_number.peso_josue",
        }
    assert Inventario().privadas_ajenas(None) == frozenset()


@respx.mock
async def test_la_vista_oculta_lo_privado_como_si_no_existiera(settings, store) -> None:
    from casa_ai.adapters.homeassistant import HomeAssistant
    from casa_ai.app import Aplicacion

    estados_ha(entidad("input_number.peso_josue", "82.4"), entidad("input_number.peso_ana", "61.0"),
               entidad("light.salon"))
    app = Aplicacion.__new__(Aplicacion)
    app.inventario = Inventario.model_validate(CON_PRIVADAS)
    app.ctx = contexto(settings, app.inventario, store, ha=HomeAssistant(settings))

    ana = app.contexto_para("telegram", "2", "telegram:2")
    vistas = [e["entity_id"] for e in await ana.ha.estados()]
    assert "input_number.peso_ana" in vistas and "input_number.peso_josue" not in vistas
    assert [e["entity_id"] for e in await ana.ha.buscar_entidades(texto="peso")] == [
        "input_number.peso_ana"
    ]
    # Y no se dice que es privada: el mensaje es el de una entidad que no existe.
    with pytest.raises(AdapterError, match="No hay ninguna entidad"):
        await ana.ha.estado("input_number.peso_josue")
    # Un adulto sigue viendo todo lo demas, sin la restriccion de dominios.
    assert (await ana.ha.estado("light.salon"))["entity_id"] == "light.salon"

    informe = app.contexto_para("rutina", "programada", "rutina:informe")
    assert not [e for e in await informe.ha.estados() if "peso" in e["entity_id"]]


@respx.mock
async def test_el_veto_normaliza_el_entity_id_como_hace_home_assistant(settings) -> None:
    """HA pasa a minusculas, acepta listas con comas y descarta lo que sigue a
    `?`: comparar el texto literal dejaba leer y ESCRIBIR la entidad privada
    de otro con `Input_number.peso_ana` o `a,input_number.peso_ana`."""
    from casa_ai.adapters.homeassistant import HomeAssistant

    estados_ha(entidad("input_number.peso_ana", "70"), entidad("light.leo"))
    escrituras = servicio_ha("input_number", "set_value")
    vista = HomeAssistant(settings).restringido_a(ocultas=frozenset({"input_number.peso_ana"}))

    for disfraz in ("Input_number.peso_ana", " input_number.peso_ana ", "INPUT_NUMBER.PESO_ANA"):
        with pytest.raises(AdapterError, match="No hay ninguna entidad"):
            await vista.estado(disfraz)
        with pytest.raises(AdapterError, match="No hay ninguna entidad"):
            await vista.llamar_servicio(
                "input_number", "set_value", {"entity_id": disfraz, "value": 1}
            )
    for roto in ("input_number.peso_ana?x", "light.leo,input_number.peso_ana",
                 "input_number.peso_ana/../x", "light.leo x"):
        with pytest.raises(AdapterError, match="no es un identificador|No hay ninguna"):
            await vista.historico(roto)
    with pytest.raises(AdapterError, match="No hay ninguna entidad"):
        await vista.llamar_servicio(
            "input_number", "set_value",
            {"entity_id": "light.leo,input_number.peso_ana", "value": 1},
        )
    assert not escrituras.called

    # Y lo que no es privado se sigue normalizando, no rechazando.
    estado_ha("light.leo", "on")
    assert (await vista.estado("Light.Leo"))["entity_id"] == "light.leo"
    ruta = servicio_ha("light", "turn_on")
    await vista.llamar_servicio("Light", "turn_on", {"entity_id": "LIGHT.leo"})
    assert ruta.calls[0].request.url.path == "/api/services/light/turn_on"
    assert json.loads(ruta.calls[0].request.read()) == {"entity_id": "light.leo"}


@respx.mock
async def test_el_adaptador_de_verdad_tambien_normaliza(settings) -> None:
    """Sin vista (dueno, rutina): el mismo control en la puerta del adaptador."""
    from casa_ai.adapters.homeassistant import HomeAssistant

    ha = HomeAssistant(settings)
    for roto in ("light.a?x", "a,b", "sin_punto", "", "light.a b"):
        with pytest.raises(AdapterError, match="no es un identificador"):
            await ha.estado(roto)
        with pytest.raises(AdapterError, match="no es un identificador"):
            await ha.llamar_servicio("light", "turn_on", {"entity_id": roto})
    with pytest.raises(AdapterError, match="no es un dominio"):
        await ha.llamar_servicio("light/../x", "turn_on", {})
