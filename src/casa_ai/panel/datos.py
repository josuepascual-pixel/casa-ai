"""Datos agregados para el panel web.

El panel es un lector: compone en una sola respuesta lo que el agente
consultaria con varias herramientas. No ejecuta acciones; para eso esta el
chat, que pasa por la capa de seguridad.

La mezcla de suministro se deriva aqui, no en el navegador, porque depende de
convenciones de signo que ya estan resueltas en el adaptador de energia y no
conviene duplicar en JavaScript.
"""

from __future__ import annotations

import unicodedata
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
    # Las estancias necesitan los estados de HA (cacheados: la lectura de
    # `_casa` acaba de hacerse) y lo que suena, que llego en paralelo.
    casa = datos.get("casa")
    if ctx.ha.configurado and isinstance(casa, dict) and "no_disponible" not in casa:
        musica = datos.get("musica") if isinstance(datos.get("musica"), list) else []
        datos["casa"]["habitaciones"] = _habitaciones(ctx, await ctx.ha.estados(), musica)
    datos["plano"] = plano_de(ctx)
    datos["momento"] = ahora(ctx.settings).strftime("%H:%M:%S")
    # Para el saludo: solo si es una persona declarada (no «http sin registrar»).
    persona = ctx.persona
    declarada = persona is not None and persona in ctx.inventario.personas
    datos["quien"] = persona.nombre if declarada else None
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
    ventanas = [
        b for b in por_dominio.get("binary_sensor", [])
        if (b.get("attributes") or {}).get("device_class") in ("window", "door", "opening",
                                                                "garage_door")
    ]

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
        ] + [
            {
                "nombre": _nombre(b),
                "estado": "abierta" if b.get("state") == "on" else "cerrada",
                "abierto": b.get("state") == "on",
                "ventana": True,
            }
            for b in ventanas
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


# --- Las estancias del plano -------------------------------------------------
# Home Assistant no dice por REST en que area esta cada entidad, asi que la
# estancia se deduce del nombre: `light.salon_techo` o «Persiana suite» van a
# su zona. Cuando dos zonas encajan («suite» y «bano suite»), gana la mas
# larga. Lo que no encaje con ninguna se puede declarar a mano en `plano:`.


def _llano(texto: str) -> str:
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn"
    )
    return " ".join(sin_acentos.replace("_", " ").replace(".", " ").split())


def _habitaciones(
    ctx: Contexto, estados: list[dict[str, Any]], musica: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    inv = ctx.inventario
    estancias = list(inv.plano) or [
        type("E", (), {"zona": z, "alias": [], "entidades": [], "exterior": False})()
        for z in inv.zonas
    ]
    if not estancias:
        return []
    nombres = [(e, [_llano(e.zona), *(_llano(a) for a in e.alias)]) for e in estancias]
    fijas = {eid: e for e in estancias for eid in e.entidades}

    por_estancia: dict[str, list[dict[str, Any]]] = {e.zona: [] for e in estancias}
    for est in estados:
        eid = str(est.get("entity_id", ""))
        destino = fijas.get(eid)
        if destino is None:
            texto = f"{_llano(eid)} {_llano(_nombre(est))}"
            mejor = max(
                ((e, n) for e, ns in nombres for n in ns if f" {n} " in f" {texto} "),
                key=lambda par: len(par[1]), default=None,
            )
            destino = mejor[0] if mejor else None
        if destino is not None:
            por_estancia[destino.zona].append(est)

    que_suena_en = {_llano(str(m.get("zona") or m.get("reproductor") or "")): m for m in musica}
    camaras = {c.zona for c in inv.camaras}
    salida = []
    for e in estancias:
        ents = por_estancia[e.zona]

        def dominio(d: str, ents: list[dict[str, Any]] = ents) -> list[dict[str, Any]]:
            return [x for x in ents if str(x.get("entity_id", "")).startswith(f"{d}.")]

        luces = dominio("light")
        covers = dominio("cover")
        persianas = [c for c in covers if not es_acceso(c)]
        accesos = [c for c in covers if es_acceso(c)] + dominio("lock")
        climas = dominio("climate")
        clima = climas[0] if climas else None
        ventanas = [
            b for b in dominio("binary_sensor")
            if (b.get("attributes") or {}).get("device_class") in ("window", "door", "opening",
                                                                    "garage_door")
        ]
        temp = None
        for c in climas + dominio("sensor"):
            atributos = c.get("attributes") or {}
            if "current_temperature" in atributos:
                temp = atributos["current_temperature"]
                break
            if atributos.get("device_class") == "temperature":
                try:
                    temp = float(c.get("state"))
                except (TypeError, ValueError):
                    temp = None
                if temp is not None:
                    break
        que_suena = que_suena_en.get(_llano(e.zona))
        salida.append({
            "zona": e.zona,
            "exterior": bool(e.exterior),
            "luces": len(luces),
            "luces_encendidas": sum(1 for x in luces if x.get("state") == "on"),
            "persianas": len(persianas),
            "persianas_abiertas": sum(
                1 for x in persianas
                if ((x.get("attributes") or {}).get("current_position") or 0) > 0
                or (x.get("state") in ("open", "opening")
                    and (x.get("attributes") or {}).get("current_position") is None)
            ),
            "accesos_abiertos": sum(
                1 for x in accesos if str(x.get("state")) in ("open", "opening", "unlocked")
            ),
            "ventanas": len(ventanas),
            "ventanas_abiertas": [_nombre(b) for b in ventanas if b.get("state") == "on"],
            "temperatura": temp,
            "clima": _texto_estado(clima) if clima else None,
            "musica": (
                " — ".join(t for t in (que_suena.get("titulo"), que_suena.get("artista")) if t)
                or que_suena.get("servicio") or "sonando"
            ) if que_suena and que_suena.get("estado") in ("play", "stream") else None,
            "camara": e.zona in camaras,
            "entidades": [
                {
                    "nombre": _nombre(x),
                    "estado": _texto_estado(x),
                    "dominio": str(x.get("entity_id", "")).split(".", 1)[0],
                }
                for x in ents
            ],
        })
    return salida


def plano_de(ctx: Contexto) -> list[dict[str, Any]]:
    """La geometria del plano para dibujarlo, o una rejilla automatica."""
    inv = ctx.inventario
    if inv.plano:
        return [
            {"zona": e.zona, "planta": e.planta, "x": e.x, "y": e.y, "ancho": e.ancho,
             "alto": e.alto, "exterior": e.exterior}
            for e in inv.plano
        ]
    columnas = 4
    return [
        {"zona": z, "planta": "", "x": (i % columnas) * 2, "y": (i // columnas) * 2,
         "ancho": 2, "alto": 2, "exterior": False}
        for i, z in enumerate(inv.zonas)
    ]
