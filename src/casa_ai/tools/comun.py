"""Ayudas compartidas por las herramientas."""

from __future__ import annotations

from typing import Any

from ..adapters.base import AdapterError


def resolver_unica(
    candidatas: list[dict[str, Any]], termino: str, plural: str, pista: str
) -> str:
    """El `entity_id` de la unica candidata, o un error que dice como seguir.

    Los tres casos (una, ninguna, varias) estaban escritos dos veces con los
    mismos textos. Para el agente, "concreta cual" y la lista de opciones son
    lo que le permite arreglarlo por su cuenta en la siguiente vuelta.
    """
    if len(candidatas) == 1:
        return str(candidatas[0]["entity_id"])
    if not candidatas:
        raise AdapterError(f"No encontre {plural} parecidas a '{termino}'. {pista}")
    opciones = ", ".join(str(c["entity_id"]) for c in candidatas[:8])
    raise AdapterError(f"Varias {plural} encajan con '{termino}': {opciones}. Concreta cual.")
