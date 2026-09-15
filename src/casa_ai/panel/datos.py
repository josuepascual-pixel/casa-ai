"""Datos agregados para el panel web.

El panel es un lector: compone en una sola respuesta lo que el agente
consultaria con varias herramientas. No ejecuta acciones; para eso esta el
chat, que pasa por la capa de seguridad.

La mezcla de suministro se deriva aqui, no en el navegador, porque depende de
convenciones de signo que ya estan resueltas en el adaptador de energia y no
conviene duplicar en JavaScript.
"""

from __future__ import annotations

from typing import Any

from ..agent.registry import Contexto
from ..agregar import reunir
from ..tiempo import ahora


def mezcla_de_suministro(resumen: dict[str, Any]) -> dict[str, int]:
    """De donde sale ahora mismo lo que consume la casa.

    La casa se alimenta de tres sitios a la vez: el sol, la bateria cuando
    descarga, y la red cuando importa. Con el balance energetico ya resuelto en
    el adaptador, el reparto sale de los signos:

    * bateria negativa = esta descargando, aporta a la casa
    * red negativa = esta importando, aporta a la casa
    * lo que falta para cubrir el consumo lo pone el sol
    """
    consumo = max(0, int(resumen.get("consumo_casa_w") or 0))
    bateria = int(resumen.get("bateria_w") or 0)
    red = int(resumen.get("red_w") or 0)

    # Se reparte en cascada para que las partes sumen EXACTAMENTE el consumo.
    # Recortando cada aporte por separado contra el consumo, un desfase entre
    # lecturas podia dar un total mayor que el consumo, y la barra contradecia
    # al indicador de la misma pantalla.
    de_bateria = min(-bateria if bateria < 0 else 0, consumo)
    de_red = min(-red if red < 0 else 0, consumo - de_bateria)
    de_sol = consumo - de_bateria - de_red

    return {
        "consumo_w": consumo,
        "solar_w": de_sol,
        "bateria_w": de_bateria,
        "red_w": de_red,
    }


async def recopilar(ctx: Contexto) -> dict[str, Any]:
    """Todo lo que el panel muestra, en una sola pasada y en paralelo."""

    tareas: dict[str, Any] = {}
    if ctx.energia.configurado:
        tareas["energia"] = _energia(ctx)
        if ctx.ha.configurado and ctx.inventario.gestionables_por_excedente():
            tareas["excedente"] = _excedente(ctx)
    if ctx.ha.configurado and ctx.inventario.dispositivos:
        tareas["dispositivos"] = _dispositivos(ctx)
    if ctx.unifi.configurado:
        tareas["red"] = ctx.unifi.salud()
    if ctx.musica.configurado:
        tareas["musica"] = ctx.musica.estado_todos()

    datos: dict[str, Any] = await reunir(**tareas)
    datos["momento"] = ahora(ctx.settings).strftime("%H:%M:%S")
    # A un nino no se le listan camaras: ni los nombres. El endpoint de la
    # captura lo rechaza ademas por su cuenta.
    datos["camaras"] = [] if ctx.es_nino else [
        {"nombre": c.nombre, "zona": c.zona} for c in ctx.inventario.camaras
    ]
    return datos


async def _energia(ctx: Contexto) -> dict[str, Any]:
    estado = await ctx.energia.estado()
    resumen = estado.resumen()
    return {"resumen": resumen, "mezcla": mezcla_de_suministro(resumen)}


async def _excedente(ctx: Contexto) -> dict[str, Any]:
    from ..tools import herramienta

    datos = await herramienta("excedente_solar").handler(ctx)
    return {"excedente": datos["excedente"], "recomendacion": datos["recomendacion"]}


async def _dispositivos(ctx: Contexto) -> list[dict[str, Any]]:
    from ..tools import herramienta

    return (await herramienta("dispositivos_estado").handler(ctx)).get("dispositivos", [])
