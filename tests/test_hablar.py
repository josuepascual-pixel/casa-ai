"""Jarvis habla por un altavoz: por el texto a voz de Home Assistant y nada mas."""

from __future__ import annotations

import json

import pytest
import respx

from casa_ai.adapters.base import AdapterError
from casa_ai.adapters.homeassistant import HomeAssistant
from casa_ai.automations.rutinas import Rutinas
from casa_ai.settings import Inventario, Settings
from casa_ai.store import Store
from casa_ai.tools import construir_registro
from casa_ai.tools.hablar import _hablar

from .dobles import AplicacionFalsa, adaptador, contexto, servicio_ha


@pytest.fixture
def con_voz(settings: Settings) -> Settings:
    return settings.model_copy(update={"tts_entidad": "tts.elevenlabs"})


@respx.mock
async def test_hablar_llama_a_tts_speak_con_el_altavoz(con_voz: Settings) -> None:
    ruta = servicio_ha("tts", "speak")
    ha = HomeAssistant(con_voz)

    r = await ha.hablar("La lavadora ha terminado.", "media_player.cocina")

    cuerpo = json.loads(ruta.calls[0].request.read())
    assert cuerpo == {
        "entity_id": "tts.elevenlabs",
        "media_player_entity_id": "media_player.cocina",
        "message": "La lavadora ha terminado.",
        "language": "es",
    }
    assert r["altavoz"] == "media_player.cocina"
    await ha.cerrar()


async def test_sin_entidad_tts_no_se_ofrece_ni_funciona(settings: Settings, con_voz, store):
    sin = HomeAssistant(settings)
    assert sin.puede_hablar is False
    with pytest.raises(AdapterError, match="TTS_ENTIDAD"):
        await sin.hablar("hola", "media_player.cocina")

    registro = construir_registro()
    ctx_sin = contexto(settings, Inventario(), store, ha=adaptador(True, puede_hablar=False))
    ctx_con = contexto(con_voz, Inventario(), store, ha=adaptador(True, puede_hablar=True))
    assert "voz_hablar" not in {h.nombre for h in registro.disponibles(ctx_sin)}
    assert "voz_hablar" in {h.nombre for h in registro.disponibles(ctx_con)}
    assert registro.ausentes(ctx_sin)["voz_hablar"] == "el sistema configurado no puede hacerlo"


async def test_la_herramienta_resuelve_el_alias_del_altavoz(con_voz: Settings, store) -> None:
    dichos = []

    async def hablar(_self, texto, altavoz):
        dichos.append((texto, altavoz))
        return {}

    inv = Inventario.model_validate({"alias_entidades": {"cocina": "media_player.homepod_cocina"}})
    ctx = contexto(con_voz, inv, store, ha=adaptador(True, puede_hablar=True, hablar=hablar))
    await _hablar(ctx, " Hola ", "Cocina")
    assert dichos == [("Hola", "media_player.homepod_cocina")]


async def test_un_altavoz_que_no_lo_es_se_rechaza(con_voz: Settings) -> None:
    with pytest.raises(AdapterError, match="no es un altavoz"):
        await HomeAssistant(con_voz).hablar("hola", "light.cocina")


async def test_el_informe_se_lee_por_el_altavoz_y_la_vigilancia_no(settings: Settings) -> None:
    dichos = []

    async def hablar(_self, texto, altavoz):
        dichos.append(altavoz)
        return {}

    con_altavoz = settings.model_copy(
        update={"tts_entidad": "tts.elevenlabs", "rutinas_altavoz": "cocina"}
    )
    app = AplicacionFalsa(con_altavoz, "Todo en orden.", store=Store(con_altavoz.db_path))
    app.inventario = Inventario.model_validate(
        {"alias_entidades": {"cocina": "media_player.cocina"}}
    )
    app.ctx = contexto(con_altavoz, app.inventario, app.store, ha=adaptador(True, hablar=hablar))
    rutinas = Rutinas(app, None)  # type: ignore[arg-type]

    await rutinas._informe()
    await rutinas._vigilancia()

    assert dichos == ["media_player.cocina"]

    # Con la casa bloqueada, ni el altavoz: era lo unico que se saltaba el Ejecutor.
    app.store.bloquear("telegram:555")
    await rutinas._informe()
    assert dichos == ["media_player.cocina"]
