"""Entregar un archivo junto a la respuesta.

Es lo que hace util pedirle a Jarvis un programa o un documento desde el
movil: el codigo entero llega como archivo, no troceado en burbujas de 4096
caracteres. La herramienta solo apunta el archivo en el contexto del turno;
quien lo manda es el canal (Telegram como documento, el panel como enlace de
descarga), y los canales que no pueden mandar archivos (voz, WhatsApp) no la
ofrecen, asi que el modelo lo pone todo en el mensaje.
"""

from __future__ import annotations

import re
from typing import Any

from ..adapters.base import AdapterError
from ..agent.registry import Adjunto, Contexto, Herramienta, Riesgo, esquema

# Canales que saben entregar un archivo a quien pregunta.
CANALES_CON_ARCHIVOS = frozenset({"telegram", "panel", "http"})
# 1 MB de texto: un programa o un informe caben de sobra, y un bucle del
# modelo escribiendo sin fin no se come el disco ni el chat.
MAX_CARACTERES = 1_000_000
MAX_ARCHIVOS_POR_TURNO = 10

_NOMBRE_INVALIDO = re.compile(r"[^A-Za-z0-9._-]+")


def nombre_seguro(nombre: str) -> str:
    """Solo un nombre de archivo: sin rutas, sin caracteres raros, con limite."""
    base = nombre.replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = _NOMBRE_INVALIDO.sub("_", base).strip("._")
    if not base:
        base = "archivo.txt"
    if "." not in base:
        base += ".txt"
    return base[:80]


async def _entregar(ctx: Contexto, nombre: str, contenido: str) -> dict[str, Any]:
    if len(ctx.adjuntos) >= MAX_ARCHIVOS_POR_TURNO:
        raise AdapterError(f"Ya hay {MAX_ARCHIVOS_POR_TURNO} archivos en esta respuesta.")
    if len(contenido) > MAX_CARACTERES:
        raise AdapterError(
            f"El archivo pasa de {MAX_CARACTERES} caracteres; partelo en varios."
        )
    adjunto = Adjunto(nombre=nombre_seguro(nombre), contenido=contenido)
    ctx.adjuntos.append(adjunto)
    return {"entregado": adjunto.nombre, "caracteres": len(contenido)}


HERRAMIENTAS = [
    Herramienta(
        nombre="archivo_entregar",
        descripcion=(
            "Entrega un archivo de texto junto a tu respuesta: un programa, un "
            "documento largo, un CSV, una lista. El usuario lo recibe como archivo "
            "descargable en el mismo chat. Pon el contenido COMPLETO en `contenido` "
            "(el codigo entero, sin recortes) y en tu mensaje solo un resumen corto "
            "de que es y como usarlo. Un archivo por llamada; para un proyecto de "
            "varios ficheros, una llamada por fichero."
        ),
        esquema=esquema(
            {
                "nombre": {
                    "type": "string",
                    "description": "Nombre con extension (p. ej. 'calculadora.py', 'carta.md')",
                },
                "contenido": {"type": "string", "description": "El contenido completo"},
            },
            obligatorias=["nombre", "contenido"],
        ),
        riesgo=Riesgo.LECTURA,
        handler=_entregar,
        para_ninos=True,
        disponible_si=lambda ctx: ctx.canal in CANALES_CON_ARCHIVOS,
    ),
]
