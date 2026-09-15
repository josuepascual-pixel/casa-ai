"""Bloques de contenido para la API de Claude.

Una imagen se le pasa al modelo como bloque, no como metadatos: es lo que hace
que «¿hay alguien en la puerta?» funcione. El empaquetado estaba escrito tres
veces (las dos entradas de foto de los canales y la captura de camara), asi que
vive aqui.
"""

from __future__ import annotations

import base64
from typing import Any


def imagen_jpeg(datos: bytes, pie: str) -> list[dict[str, Any]]:
    """Una imagen mas el texto que dice al modelo que hacer con ella."""
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(datos).decode("ascii"),
            },
        },
        {"type": "text", "text": pie},
    ]
