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

from ..adapters.homeassistant import es_acceso
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
    if ctx.ha.configurado:
        tareas["casa"] = _casa(ctx)
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


# --- La casa segun Home Assistant --------------------------------------------
# Todo lo que no tiene adaptador propio (persianas de TaHoma, cerradura Nuki,
# clima, Wallbox, EcoWater, spa, riego...) llega por Home Assistant, y el panel
# lo ensena desde la lista de estados: una sola lectura, ya cacheada, y la
# vista restringida del nino ya ha quitado lo que no le toca.

_ESTADOS = {
    "on": "encendida", "off": "apagada", "open": "abierta", "closed": "cerrada",
    "opening": "abriendo", "closing": "cerrando", "locked": "cerrada con llave",
    "unlocked": "abierta", "locking": "cerrando", "unlocking": "abriendo",
    "jammed": "atascada", "home": "en casa", "not_home": "fuera",
    "unavailable": "sin conexion", "unknown": "sin dato", "heat": "calor",
    "cool": "frio", "heat_cool": "auto", "auto": "auto", "dry": "seco", "fan_only": "ventilar",
    "idle": "en reposo", "playing": "sonando", "paused": "en pausa",
}


def _nombre(e: dict[str, Any]) -> str:
    atributos = e.get("attributes") or {}
    return str(atributos.get("friendly_name") or e.get("entity_id", "?"))


def _texto_estado(e: dict[str, Any]) -> str:
    estado = str(e.get("state", ""))
    unidad = (e.get("attributes") or {}).get("unit_of_measurement")
    if unidad:
        return f"{estado} {unidad}"
    return _ESTADOS.get(estado, estado)


async def _casa(ctx: Contexto) -> dict[str, Any]:
    estados = await ctx.ha.estados()
    por_id = {str(e.get("entity_id", "")): e for e in estados}
    por_dominio: dict[str, list[dict[str, Any]]] = {}
    for e in estados:
        por_dominio.setdefault(str(e.get("entity_id", "")).split(".", 1)[0], []).append(e)

    luces = por_dominio.get("light", [])
    covers = por_dominio.get("cover", [])
    accesos = [c for c in covers if es_acceso(c)] + por_dominio.get("lock", [])

    casa: dict[str, Any] = {
        "luces": {
            "total": len(luces),
            "encendidas": sorted(_nombre(e) for e in luces if e.get("state") == "on"),
        },
        "persianas": [
            {
                "nombre": _nombre(c),
                "estado": _texto_estado(c),
                "posicion": (c.get("attributes") or {}).get("current_position"),
            }
            for c in covers if not es_acceso(c)
        ],
        "accesos": [
            {
                "nombre": _nombre(a),
                "estado": _texto_estado(a),
                # Abierto o sin llave es lo que hay que ver de un vistazo.
                "abierto": str(a.get("state")) in ("open", "opening", "unlocked", "unlocking",
                                                   "jammed"),
            }
            for a in accesos
        ],
        "clima": [
            {
                "nombre": _nombre(c),
                "actual": (c.get("attributes") or {}).get("current_temperature"),
                "objetivo": (c.get("attributes") or {}).get("temperature"),
                "modo": _ESTADOS.get(str(c.get("state")), str(c.get("state"))),
            }
            for c in por_dominio.get("climate", [])
        ],
        "sistemas": [
            {
                "titulo": tarjeta.titulo,
                "lineas": [
                    {
                        "nombre": etiqueta,
                        "valor": _texto_estado(por_id[eid]) if eid in por_id else "sin dato",
                    }
                    for etiqueta, ref in tarjeta.entidades.items()
                    for eid in [ctx.inventario.resolver_alias(ref)]
                ],
            }
            for tarjeta in ctx.inventario.panel
        ],
    }
    # Quien esta en casa no es cosa de un nino ni de una tablet compartida.
    if not ctx.es_nino:
        casa["presencia"] = [
            {"nombre": _nombre(p), "en_casa": p.get("state") == "home"}
            for p in por_dominio.get("person", [])
        ]
    return casa
