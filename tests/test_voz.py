"""Voice Design: una voz nueva a partir de una descripcion, sin clonar a nadie."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from casa_ai.adapters.base import AdapterError
from casa_ai.voz import API, DESCRIPCION_JARVIS, FRASE_MUESTRA, disenar, guardar


def _vista(id_generado: str, mp3: bytes) -> dict[str, str]:
    return {"generated_voice_id": id_generado, "audio_base_64": base64.b64encode(mp3).decode()}


async def test_sin_clave_dice_donde_conseguirla() -> None:
    with pytest.raises(AdapterError, match="ELEVENLABS_API_KEY"):
        await disenar(None)


@respx.mock
async def test_disenar_pide_vistas_con_la_frase_en_castellano() -> None:
    ruta = respx.post(f"{API}/text-to-voice/design").mock(
        return_value=httpx.Response(
            200,
            json={"previews": [_vista("g1", b"mp3-1"), _vista("g2", b"mp3-2")]},
        )
    )

    vistas = await disenar("clave")

    assert [(v.id_generado, v.audio_mp3) for v in vistas] == [("g1", b"mp3-1"), ("g2", b"mp3-2")]
    peticion = ruta.calls[0].request
    assert peticion.headers["xi-api-key"] == "clave"
    cuerpo = peticion.read().decode()
    assert "Castilian Spanish" in cuerpo and "señor" in cuerpo
    assert DESCRIPCION_JARVIS[:20] in cuerpo and FRASE_MUESTRA[:20] in cuerpo


@respx.mock
async def test_una_clave_mala_se_explica() -> None:
    respx.post(f"{API}/text-to-voice/design").mock(return_value=httpx.Response(401, text="x"))
    with pytest.raises(AdapterError, match="401.*no es valida"):
        await disenar("mala")


@respx.mock
async def test_guardar_devuelve_el_voice_id() -> None:
    ruta = respx.post(f"{API}/text-to-voice").mock(
        return_value=httpx.Response(200, json={"voice_id": "v-jarvis"})
    )
    assert await guardar("clave", "g2") == "v-jarvis"
    import json

    cuerpo = json.loads(ruta.calls[0].request.read())
    assert cuerpo["voice_name"] == "Jarvis" and cuerpo["generated_voice_id"] == "g2"
