"""Disenar la voz de Jarvis con Voice Design de ElevenLabs.

No se clona a nadie: Voice Design genera una voz sintetica nueva a partir de
una descripcion. Se piden varias vistas previas con la misma frase, se
escuchan, y la elegida se guarda como voz con nombre, que es lo que luego usa
la salida de voz por los HomePods.

    ELEVENLABS_API_KEY=... python -m casa_ai.voz disenar          # tres mp3
    ELEVENLABS_API_KEY=... python -m casa_ai.voz guardar <id>     # la elegida

Va como modulo aparte porque necesita la clave y salir a internet: el
complemento la tiene en su formulario, y quien lo ejecute en su ordenador la
pone en el entorno. Nunca en un chat.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path

import httpx

from .adapters.base import AdapterError

API = "https://api.elevenlabs.io/v1"
MODELO = "eleven_multilingual_ttv_v2"

# La descripcion va en ingles porque es el idioma en el que el modelo de
# diseno entiende mejor los matices; la frase de muestra, en castellano, es
# lo que fija el idioma de la voz.
DESCRIPCION_JARVIS = (
    "A calm, deep male voice in his forties with the refined poise of a British "
    "butler, speaking Castilian Spanish from Spain with impeccable diction and a "
    "slightly slow, unhurried pace. Composed and quietly confident, with a dry, "
    "subtle irony; never servile, never theatrical. Studio quality, close "
    "microphone, no background noise."
)

FRASE_MUESTRA = (
    "Buenas tardes, señor. La casa está en orden: el sol produce cuarenta y un "
    "kilovatios, la batería va al ochenta y ocho por ciento y sobran seis para lo "
    "que quiera encender. Si me permite una sugerencia, es buen momento para el "
    "termo. Y sí, señor: la cámara del cuarto de Leo sigue siendo cosa de ustedes dos."
)


@dataclass
class VistaPrevia:
    id_generado: str
    audio_mp3: bytes


def _cliente(clave: str | None) -> httpx.AsyncClient:
    if not clave:
        raise AdapterError(
            "Falta ELEVENLABS_API_KEY. Crea una clave en elevenlabs.io (Profile → "
            "API keys) y exportala en el entorno antes de ejecutar esto; no la "
            "pegues en ningun chat."
        )
    return httpx.AsyncClient(
        base_url=API, headers={"xi-api-key": clave}, timeout=httpx.Timeout(120.0)
    )


def _error(resp: httpx.Response, haciendo: str) -> AdapterError:
    detalle = resp.text[:300]
    if resp.status_code == 401:
        detalle = "la clave no es valida o ha caducado"
    return AdapterError(f"ElevenLabs devolvio {resp.status_code} {haciendo}: {detalle}")


async def disenar(
    clave: str | None,
    descripcion: str = DESCRIPCION_JARVIS,
    texto: str = FRASE_MUESTRA,
) -> list[VistaPrevia]:
    """Pide las vistas previas (normalmente tres) de una voz descrita con palabras."""
    async with _cliente(clave) as http:
        resp = await http.post(
            "/text-to-voice/design",
            json={"voice_description": descripcion, "text": texto, "model_id": MODELO},
        )
        if resp.status_code != 200:
            raise _error(resp, "disenando la voz")
        vistas = resp.json().get("previews", [])
    if not vistas:
        raise AdapterError("ElevenLabs no devolvio ninguna vista previa.")
    return [
        VistaPrevia(v["generated_voice_id"], base64.b64decode(v["audio_base_64"]))
        for v in vistas
    ]


async def guardar(
    clave: str | None,
    id_generado: str,
    nombre: str = "Jarvis",
    descripcion: str = DESCRIPCION_JARVIS,
) -> str:
    """Convierte una vista previa en una voz con nombre. Devuelve su `voice_id`."""
    async with _cliente(clave) as http:
        resp = await http.post(
            "/text-to-voice",
            json={
                "voice_name": nombre,
                "voice_description": descripcion,
                "generated_voice_id": id_generado,
            },
        )
        if resp.status_code != 200:
            raise _error(resp, "guardando la voz")
        return str(resp.json()["voice_id"])


async def _principal() -> None:  # pragma: no cover - utilidad manual
    import argparse

    from .settings import get_settings

    ap = argparse.ArgumentParser(description="Voz de Jarvis con Voice Design de ElevenLabs")
    sub = ap.add_subparsers(dest="orden", required=True)
    d = sub.add_parser("disenar", help="genera las vistas previas como mp3")
    d.add_argument("--carpeta", type=Path, default=Path("voces"))
    d.add_argument("--descripcion", default=DESCRIPCION_JARVIS)
    g = sub.add_parser("guardar", help="guarda una vista previa como la voz de Jarvis")
    g.add_argument("id_generado")
    g.add_argument("--nombre", default="Jarvis")
    args = ap.parse_args()

    clave = get_settings().elevenlabs_api_key
    if args.orden == "disenar":
        args.carpeta.mkdir(parents=True, exist_ok=True)
        for i, vista in enumerate(await disenar(clave, args.descripcion), start=1):
            ruta = args.carpeta / f"jarvis-{i}.mp3"
            ruta.write_bytes(vista.audio_mp3)
            print(f"{ruta}  (id {vista.id_generado})")
        print(
            "\nEscuchalos. Para quedarte con uno:\n"
            "  python -m casa_ai.voz guardar <id>\n"
            "y pon el voice_id que devuelva en la integracion ElevenLabs de Home "
            "Assistant, que es la que habla."
        )
    else:
        print(await guardar(clave, args.id_generado, args.nombre))


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_principal())
