"""Herramientas de sistema: confirmacion de acciones, auditoria e informe global."""

from __future__ import annotations

from typing import Any

from ..agent.registry import Contexto, Herramienta, Riesgo, esquema
from ..agent.safety import TOOL_CONFIRMAR
from ..agregar import reunir
from ..tiempo import ahora, formatear


async def _confirmar(ctx: Contexto, token: str) -> Any:  # pragma: no cover
    """No se llega a ejecutar: el Ejecutor intercepta esta herramienta.

    Existe aqui solo para que aparezca en el esquema que ve el modelo.
    """
    raise RuntimeError("interceptada por el Ejecutor")


async def _auditoria(ctx: Contexto, limite: int | None = None) -> dict[str, Any]:
    registros = ctx.store.auditoria(limite or 15)
    return {
        "acciones": [
            {
                "cuando": formatear(r["ts"], ctx.settings),
                "quien": f"{r['canal']}:{r['usuario']}",
                "herramienta": r["herramienta"],
                "argumentos": r["argumentos"],
                "riesgo": r["riesgo"],
                "resultado": r["resultado"][:300],
                "fallo": bool(r["error"]),
            }
            for r in registros
        ]
    }


async def _informe(ctx: Contexto) -> dict[str, Any]:
    """Foto general de la casa en una sola llamada.

    Existe para que una pregunta como "como va todo?" no cueste cinco llamadas
    en serie: se consultan todos los subsistemas en paralelo y los que fallen
    se reportan como no disponibles sin tumbar el resto.
    """

    tareas: dict[str, Any] = {}
    if ctx.energia.configurado:
        tareas["energia"] = _resumen_energia(ctx)
    if ctx.unifi.configurado:
        tareas["red"] = ctx.unifi.salud()
        tareas["camaras"] = ctx.unifi.camaras()
    if ctx.musica.configurado:
        tareas["musica"] = ctx.musica.estado_todos()

    resultados = await reunir(**tareas)
    resultados["momento"] = ahora(ctx.settings).strftime("%Y-%m-%d %H:%M")
    if ctx.inventario.notas_casa:
        resultados["notas_de_la_casa"] = ctx.inventario.notas_casa
    return resultados


async def _resumen_energia(ctx: Contexto) -> dict[str, Any]:
    estado = await ctx.energia.estado()
    return estado.resumen()


HERRAMIENTAS = [
    Herramienta(
        nombre=TOOL_CONFIRMAR,
        descripcion=(
            "Ejecuta una accion de riesgo que quedo pendiente de confirmacion. "
            "Llamala SOLO despues de que el usuario haya confirmado explicitamente, "
            "con el token que se te dio. Nunca inventes un token."
        ),
        esquema=esquema(
            {"token": {"type": "string", "description": "Token de la accion pendiente"}},
            obligatorias=["token"],
        ),
        riesgo=Riesgo.MEDIO,
        handler=_confirmar,
        # Solo donde el modelo puede tener el token. Con botones se le estaba
        # ofreciendo la herramienta y acto seguido prohibiendosela en prosa:
        # una inyeccion tenia a que apuntar y el propio proyecto tiene la regla
        # de que lo que no sirve no se ofrece. `confirmacion` es constante por
        # canal, asi que el conjunto de herramientas sigue siendo estable
        # dentro de una conversacion, que es lo que la cache de prefijo pide.
        disponible_si=lambda ctx: ctx.confirmacion == "en_banda",
    ),
    Herramienta(
        nombre="informe_casa",
        descripcion=(
            "Foto general de toda la casa en una sola llamada: energia, red, "
            "camaras y musica. Usala cuando la pregunta sea amplia ('como va todo', "
            "'todo bien en casa?', informe diario) en lugar de consultar cada "
            "subsistema por separado."
        ),
        esquema=esquema({}),
        riesgo=Riesgo.LECTURA,
        handler=_informe,
    ),
    Herramienta(
        nombre="auditoria_acciones",
        descripcion=(
            "Ultimas acciones que los agentes han ejecutado en la casa, con quien "
            "las pidio y el resultado. Usala si el usuario pregunta que se ha hecho "
            "o por que algo ha cambiado."
        ),
        esquema=esquema({"limite": {"type": "integer"}}),
        riesgo=Riesgo.LECTURA,
        handler=_auditoria,
    ),
]
