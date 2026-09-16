"""Ensamblado del registro de herramientas."""

from __future__ import annotations

from functools import lru_cache

from ..agent.registry import Herramienta, Registro
from . import archivos, camaras, casa, dispositivos, energia, hablar, musica, red, sistema


def construir_registro() -> Registro:
    registro = Registro()
    for modulo in (
        energia, casa, dispositivos, musica, red, camaras, sistema, hablar, archivos,
    ):
        registro.anadir(*modulo.HERRAMIENTAS)
    return registro


@lru_cache
def _catalogo() -> Registro:
    return construir_registro()


def herramienta(nombre: str) -> Herramienta:
    """Una herramienta por su nombre, para llamarla desde fuera del agente.

    Con error explicito: el `next(h for h in ... if h.nombre == ...)` que
    habia en el panel vivia dentro de un try/except que lo habria convertido
    en un "no disponible" silencioso si alguien renombrara la herramienta.
    """
    h = _catalogo().get(nombre)
    if h is None:
        raise KeyError(f"No existe la herramienta '{nombre}'")
    return h


__all__ = ["construir_registro", "herramienta"]
