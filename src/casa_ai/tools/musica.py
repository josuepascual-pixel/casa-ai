"""Herramientas de musica: reproductores BluOS."""

from __future__ import annotations

from typing import Any

from ..agent.registry import Contexto, Herramienta, Riesgo, esquema


async def _estado(ctx: Contexto, reproductor: str | None = None) -> Any:
    if reproductor:
        return await ctx.musica.estado(reproductor)
    return {"reproductores": await ctx.musica.estado_todos()}


async def _control(
    ctx: Contexto, accion: str, reproductor: str | None = None, valor: int | None = None
) -> dict[str, Any]:
    return await ctx.musica.control(accion, reproductor, valor)


async def _presets(ctx: Contexto, reproductor: str | None = None) -> dict[str, Any]:
    return {"presets": await ctx.musica.presets(reproductor)}


async def _agrupar(
    ctx: Contexto, maestro: str, zonas: list[str], desagrupar: bool | None = None
) -> dict[str, Any]:
    if desagrupar:
        return await ctx.musica.desagrupar(maestro, zonas or None)
    return await ctx.musica.agrupar(maestro, zonas)


HERRAMIENTAS = [
    Herramienta(
        nombre="musica_estado",
        descripcion=(
            "Que esta sonando en el sistema BluOS: estado, volumen, titulo, artista "
            "y servicio de cada reproductor. Sin argumento devuelve todas las zonas."
        ),
        esquema=esquema(
            {"reproductor": {"type": "string", "description": "Nombre o zona del reproductor"}}
        ),
        riesgo=Riesgo.LECTURA,
        para_ninos=True,
        handler=_estado,
        requiere="musica",
    ),
    Herramienta(
        nombre="musica_control",
        descripcion=(
            "Controla un reproductor BluOS. Acciones: play, pausa, stop, siguiente, "
            "anterior, volumen (valor 0-100), silenciar, desilenciar, preset (valor "
            "= numero de preset). Si no se indica reproductor se usa el primero del "
            "inventario. Devuelve el estado resultante."
        ),
        esquema=esquema(
            {
                "accion": {
                    "type": "string",
                    "enum": [
                        "play", "pausa", "stop", "siguiente", "anterior",
                        "volumen", "silenciar", "desilenciar", "preset",
                    ],
                },
                "reproductor": {"type": "string"},
                "valor": {"type": "integer", "description": "Volumen 0-100 o numero de preset"},
            },
            obligatorias=["accion"],
        ),
        riesgo=Riesgo.MEDIO,
        para_ninos=True,
        handler=_control,
        requiere="musica",
    ),
    Herramienta(
        nombre="musica_presets",
        descripcion=(
            "Lista los presets guardados en un reproductor BluOS "
            "(radios, listas, entradas)."
        ),
        esquema=esquema({"reproductor": {"type": "string"}}),
        riesgo=Riesgo.LECTURA,
        para_ninos=True,
        handler=_presets,
        requiere="musica",
    ),
    Herramienta(
        nombre="musica_agrupar",
        descripcion=(
            "Agrupa o desagrupa zonas BluOS para que suenen sincronizadas. El "
            "'maestro' manda la reproduccion; 'zonas' son las que se le unen. "
            "Con desagrupar=true las separa."
        ),
        esquema=esquema(
            {
                "maestro": {"type": "string"},
                "zonas": {"type": "array", "items": {"type": "string"}},
                "desagrupar": {"type": "boolean"},
            },
            obligatorias=["maestro", "zonas"],
        ),
        riesgo=Riesgo.MEDIO,
        para_ninos=True,
        handler=_agrupar,
        requiere="musica",
    ),
]
