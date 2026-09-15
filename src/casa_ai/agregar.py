"""Reunir varios subsistemas sin que uno caido tumbe la respuesta.

La politica es la misma en el informe del agente y en el panel: se piden en
paralelo y el que falle sale marcado como no disponible. Estaba escrita dos
veces, con el mismo cierre `seguro(...)` byte a byte.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any


async def reunir(**tareas: Awaitable[Any]) -> dict[str, Any]:
    """Espera todas las corrutinas y devuelve `{nombre: resultado}`.

    Una que lance no propaga: su hueco queda con `{"no_disponible": motivo}`,
    y quien pinta decide si lo muestra o lo omite.
    """
    if not tareas:
        return {}
    nombres = list(tareas)
    resultados = await asyncio.gather(
        *(_seguro(tareas[nombre]) for nombre in nombres)
    )
    return dict(zip(nombres, resultados, strict=True))


async def _seguro(corutina: Awaitable[Any]) -> Any:
    try:
        return await corutina
    except Exception as e:  # noqa: BLE001 - la respuesta entera nunca debe fallar
        return {"no_disponible": str(e)}
