"""Que Jarvis hable por un altavoz de la casa.

Es lo que convierte un chat en el asistente de la pelicula: «avisa en la
cocina de que la lavadora ha terminado» sale por el HomePod de la cocina con
su voz. Va por el servicio de texto a voz de Home Assistant, asi que la voz
y los altavoces son los que HA tenga; el agente solo elige que y donde.
"""

from __future__ import annotations

from typing import Any

from ..agent.registry import Contexto, Herramienta, Riesgo, esquema


async def _hablar(ctx: Contexto, texto: str, altavoz: str) -> dict[str, Any]:
    return await ctx.ha.hablar(texto.strip(), ctx.inventario.resolver_alias(altavoz))


HERRAMIENTAS = [
    Herramienta(
        nombre="voz_hablar",
        descripcion=(
            "Dice un texto en voz alta por un altavoz de la casa (HomePod, Apple TV "
            "o cualquier media_player de Home Assistant) con la voz del asistente. "
            "Para avisar a alguien que esta en otra habitacion o para responder por "
            "voz cuando te lo pidan. Frases cortas: se van a escuchar, no a leer. "
            "El altavoz es un alias del inventario o un entity_id media_player.*"
        ),
        esquema=esquema(
            {
                "texto": {"type": "string", "description": "Lo que se va a decir"},
                "altavoz": {
                    "type": "string",
                    "description": "Alias (p. ej. 'cocina') o entity_id media_player.*",
                },
            },
            obligatorias=["texto", "altavoz"],
        ),
        riesgo=Riesgo.MEDIO,
        handler=_hablar,
        requiere="ha",
        # Capacidad, no configuracion: HA puede estar y no tener voz.
        disponible_si=lambda ctx: ctx.ha.puede_hablar,
    ),
]
