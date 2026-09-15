"""Herramientas de camaras.

Lo importante es que `camara_ver` no devuelve metadatos: devuelve la captura
JPEG como bloque de imagen dentro del tool_result. El modelo la MIRA. Por eso
"hay alguien en la puerta?" se responde sobre la imagen real y no sobre un
sensor de movimiento.

Que camino se usa para conseguirla —UniFi Protect directo o el proxy de Home
Assistant— lo decide `ctx.camaras`, no estas herramientas.
"""

from __future__ import annotations

from typing import Any

from ..adapters.base import AdapterError
from ..adapters.camaras_base import MAX_BYTES_IMAGEN
from ..agent.registry import Contexto, Herramienta, Riesgo, esquema
from ..bloques import imagen_jpeg


async def _listar(ctx: Contexto) -> dict[str, Any]:
    return await ctx.camaras.listar()


async def _ver(ctx: Contexto, camara: str) -> list[dict[str, Any]]:
    imagen, identificador = await ctx.camaras.captura(camara)

    if len(imagen) > MAX_BYTES_IMAGEN:
        raise AdapterError(
            f"La captura de \'{camara}\' pesa {len(imagen) // 1024} KB, demasiado "
            "para analizarla. Reduce la resolucion de esa camara."
        )

    return imagen_jpeg(
        imagen,
        f"Captura en directo de la camara \'{camara}\' ({identificador}, via "
        f"{ctx.camaras.via}). Describe solo lo que se ve realmente en la imagen.",
    )


async def _eventos(ctx: Contexto, horas: int | None = None) -> dict[str, Any]:
    ventana = horas or 2
    return {"ventana_horas": ventana, "eventos": await ctx.camaras.eventos(ventana)}


HERRAMIENTAS = [
    Herramienta(
        nombre="camaras_listar",
        descripcion=(
            "Lista las camaras disponibles con su identificador. Con UniFi "
            "Protect incluye si estan conectadas, si graban y si hay movimiento "
            "ahora mismo; via Home Assistant solo el listado."
        ),
        esquema=esquema({}),
        riesgo=Riesgo.LECTURA,
        handler=_listar,
        requiere="camaras",
    ),
    Herramienta(
        nombre="camara_ver",
        descripcion=(
            "Captura la imagen en directo de una camara y te la devuelve para que "
            "la veas. Usala cuando el usuario pregunte que pasa en un sitio, si hay "
            "alguien, si algo esta abierto o cerrado. Describe unicamente lo que "
            "aparece en la imagen; si no se distingue, dilo."
        ),
        esquema=esquema(
            {"camara": {"type": "string", "description": "Nombre o id de la camara"}},
            obligatorias=["camara"],
        ),
        riesgo=Riesgo.LECTURA,
        handler=_ver,
        requiere="camaras",
    ),
    Herramienta(
        nombre="camaras_eventos",
        descripcion=(
            "Eventos recientes de las camaras (movimiento, deteccion inteligente de "
            "persona/vehiculo, timbre). Por defecto las ultimas 2 horas. Necesita "
            "acceso directo a UniFi Protect."
        ),
        esquema=esquema({"horas": {"type": "integer"}}),
        riesgo=Riesgo.LECTURA,
        handler=_eventos,
        requiere="camaras",
        # Que haya camaras no implica que haya eventos: solo Protect los da.
        disponible_si=lambda ctx: ctx.camaras.tiene_eventos,
    ),
]
