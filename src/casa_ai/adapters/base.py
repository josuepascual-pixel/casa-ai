"""Base comun de los adaptadores."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import httpx


class AdapterError(RuntimeError):
    """Fallo al hablar con un sistema fisico.

    Se propaga al agente como texto en el tool_result con is_error=True, para
    que pueda explicarlo o reintentar por otra via en vez de inventarse datos.
    """


class NoConfigurado(AdapterError):
    """El adaptador no tiene credenciales o host configurados."""


@contextmanager
def contacto(sistema: str) -> Iterator[None]:
    """Traduce un fallo de transporte a `AdapterError` con el mismo mensaje.

    Solo el transporte: un codigo HTTP de respuesta lo sigue interpretando cada
    sitio, porque "404 en camera_proxy" y "404 en /api/services" se arreglan de
    formas distintas y el agente necesita saber cual. Eso es informacion, no
    repeticion; el "no llego a responder" si era la misma frase cinco veces.
    """
    try:
        yield
    except httpx.HTTPStatusError:
        raise
    except httpx.HTTPError as e:
        raise AdapterError(f"No se pudo contactar con {sistema}: {e}") from e
