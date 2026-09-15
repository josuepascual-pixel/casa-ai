"""Aparatos de la casa con significado, y gestion del excedente solar.

La casa tiene mas sistemas que los cinco que este backend habla directamente:
cargador del coche, spa, riego, electrodomesticos, toldos, cerradura,
intercomunicador, audio de otra marca... Casi todos llegan a traves de Home
Assistant, que es la capa de integracion, y se controlan con `casa_accion`.

Lo que Home Assistant no aporta es el SIGNIFICADO. `switch.wallbox_carga` no
le dice nada al agente. Declarado en `dispositivos:` como "el cargador del
coche, 7,4 kW, candidato a excedente solar", ya puede responder a "aprovecha
el sol que sobra" sin que nadie se lo explique cada vez.

De ahi la herramienta de excedente: es lo unico que ninguna app por separado
puede hacer, porque hace falta ver el inversor y los consumos a la vez.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..adapters.base import AdapterError
from ..agent.registry import Contexto, Herramienta, Riesgo, Todos, esquema
from ..settings import Dispositivo

ESTADOS_ENCENDIDO = {"on", "heat", "heat_cool", "cool", "open", "playing", "charging"}


async def _estado_entidad(ctx: Contexto, entity_id: str) -> dict[str, Any]:
    try:
        datos = await ctx.ha.estado(entity_id)
    except AdapterError as e:
        return {"entity_id": entity_id, "error": str(e)}
    atributos = datos.get("attributes", {}) or {}
    return {
        "entity_id": entity_id,
        "estado": datos.get("state"),
        "unidad": atributos.get("unit_of_measurement"),
    }


async def _foto_dispositivo(ctx: Contexto, d: Dispositivo) -> dict[str, Any]:
    """Estado vivo de un aparato y de sus entidades auxiliares."""
    entidades = dict(d.entidades)
    if d.entidad:
        entidades["principal"] = d.entidad

    por_rol = dict(
        zip(
            entidades,
            await asyncio.gather(
                *(_estado_entidad(ctx, eid) for eid in entidades.values())
            ),
            strict=True,
        )
    )

    principal = por_rol.get("principal", {})
    encendido = str(principal.get("estado", "")).lower() in ESTADOS_ENCENDIDO

    foto: dict[str, Any] = {
        "nombre": d.nombre,
        "categoria": d.categoria,
        "encendido": encendido if d.entidad else None,
        "entidades": por_rol,
    }
    if d.consumo_w is not None:
        foto["consumo_nominal_w"] = d.consumo_w
    if d.excedente:
        foto["gestionable_por_excedente"] = True
        foto["prioridad"] = d.prioridad
    if d.notas:
        foto["notas"] = d.notas
    return foto


async def _fotos(ctx: Contexto, aparatos: list[Dispositivo]) -> list[dict[str, Any]]:
    """El estado vivo de varios aparatos, en una sola lectura de Home Assistant.

    Con dos o mas entidades sale mas barato traer la tabla entera una vez que
    pedirlas de una en una; con una sola, no, asi que no se trae.
    """
    if sum(len(d.entidades) + bool(d.entidad) for d in aparatos) > 1:
        await ctx.ha.precalentar()
    return list(await asyncio.gather(*(_foto_dispositivo(ctx, d) for d in aparatos)))


async def _listar(ctx: Contexto, categoria: str | None = None) -> dict[str, Any]:
    dispositivos = ctx.inventario.dispositivos
    if not dispositivos:
        return {
            "aviso": (
                "No hay aparatos declarados en la seccion `dispositivos:` de "
                "config/config.yaml. Puedes seguir usando casa_buscar_entidades "
                "para encontrar cualquier entidad de Home Assistant, pero "
                "declararlos aqui les da nombre y sentido."
            ),
            "dispositivos": [],
        }
    if categoria:
        objetivo = categoria.strip().lower()
        dispositivos = [d for d in dispositivos if d.categoria.lower() == objetivo]
        if not dispositivos:
            categorias = sorted({d.categoria for d in ctx.inventario.dispositivos})
            raise AdapterError(
                f"No hay aparatos de categoria '{categoria}'. Hay: {', '.join(categorias)}."
            )

    return {"dispositivos": await _fotos(ctx, dispositivos)}


async def _excedente(ctx: Contexto) -> dict[str, Any]:
    """Cuanto sol sobra ahora y en que se podria gastar."""
    estado = await ctx.energia.estado()
    s = ctx.settings

    # Lo que se esta vertiendo a la red es excedente puro.
    vertiendo_w = max(0, estado.red_w)

    # La potencia que entra en la bateria solo cuenta como redirigible cuando
    # la bateria ya va llena: antes de eso, cargarla es mejor uso.
    # Sin lectura de SOC no se asume nada: cargar la bateria es el uso por
    # defecto y no se le quita el sitio a ciegas.
    margen_bateria_w = 0
    if (
        estado.bateria_w > 0
        and estado.bateria_soc is not None
        and estado.bateria_soc >= s.excedente_soc_minimo
    ):
        margen_bateria_w = estado.bateria_w

    disponible_w = max(0, vertiendo_w + margen_bateria_w - s.excedente_margen_w)

    candidatos = ctx.inventario.gestionables_por_excedente()
    fotos = await _fotos(ctx, candidatos)

    # Reparto voraz por prioridad: que cabe en lo que hay disponible.
    restante = disponible_w
    encender: list[dict[str, Any]] = []
    for d, foto in zip(candidatos, fotos, strict=True):
        if foto.get("encendido"):
            continue  # ya esta aprovechando
        coste = d.consumo_w or 0
        if coste and coste <= restante:
            encender.append({"nombre": d.nombre, "entidad": d.entidad, "consumo_w": coste})
            restante -= coste

    # Si se esta importando de la red, sobra algo encendido. Solo se proponen
    # aparatos que declaran su consumo: de los que no, no se puede afirmar que
    # apagarlos ayude, y contarlos como 0 W hacia que el bucle nunca cubriera
    # el deficit y acabara proponiendo apagar la casa entera.
    apagar: list[dict[str, Any]] = []
    if estado.red_w < 0:
        deficit = abs(estado.red_w)
        for d, foto in zip(reversed(candidatos), reversed(fotos), strict=True):
            if not foto.get("encendido") or not d.consumo_w:
                continue
            apagar.append(
                {"nombre": d.nombre, "entidad": d.entidad, "consumo_w": d.consumo_w}
            )
            deficit -= d.consumo_w
            if deficit <= 0:
                break

    return {
        "energia": estado.resumen(),
        "excedente": {
            "vertiendo_a_red_w": vertiendo_w,
            "margen_de_bateria_w": margen_bateria_w,
            "margen_reservado_w": s.excedente_margen_w,
            "disponible_para_gastar_w": disponible_w,
        },
        "candidatos": fotos,
        "recomendacion": {
            "encender": encender,
            "apagar": apagar,
            "sobraria_sin_usar_w": restante if encender else disponible_w,
        },
        "como_actuar": (
            "Estas son recomendaciones, no acciones. Para ejecutarlas usa "
            "casa_accion sobre las entidades indicadas. Explica al usuario que "
            "vas a encender y cuanto consume antes de hacerlo."
        ),
        "criterio": (
            f"Solo cuenta como excedente lo que se vierte a la red, mas lo que "
            f"entra en la bateria si esta por encima del {s.excedente_soc_minimo} %. "
            f"Se reservan {s.excedente_margen_w} W de margen para no pasar a importar."
        ),
    }


HERRAMIENTAS = [
    Herramienta(
        nombre="dispositivos_estado",
        descripcion=(
            "Aparatos declarados de la casa con su estado real: cargador del "
            "coche, spa, riego, electrodomesticos, toldos, cerradura, audio... "
            "Incluye su consumo nominal y si son candidatos a excedente solar. "
            "Usala cuando la pregunta sea sobre un aparato concreto de la casa "
            "en vez de sobre un entity_id. Para actuar, casa_accion."
        ),
        esquema=esquema(
            {
                "categoria": {
                    "type": "string",
                    "description": (
                        "Filtro opcional: cargador_vehiculo, spa, riego, "
                        "electrodomestico, climatizacion, audio, acceso..."
                    ),
                }
            }
        ),
        riesgo=Riesgo.LECTURA,
        para_ninos=True,
        handler=_listar,
        requiere="ha",
    ),
    Herramienta(
        nombre="excedente_solar",
        descripcion=(
            "Cuanta energia solar esta sobrando ahora mismo y en que se podria "
            "gastar. Cruza la produccion, la bateria y el flujo con la red con "
            "los aparatos grandes de la casa, y recomienda cuales encender o "
            "apagar. Usala cuando el usuario pregunte si puede poner la lavadora, "
            "cargar el coche, calentar el spa o el agua, o diga algo como "
            "'aprovecha el sol que sobra'. Devuelve recomendaciones: para "
            "ejecutarlas hay que usar casa_accion."
        ),
        esquema=esquema({}),
        riesgo=Riesgo.LECTURA,
        para_ninos=True,
        handler=_excedente,
        # `requiere` significa "basta cualquiera de estos", pero esta
        # herramienta necesita LOS DOS: el inversor para saber cuanto sobra y
        # Home Assistant para saber el estado de los aparatos. De ahi el
        # disponible_si, que es una condicion Y.
        # Los dos: hace falta ver el inversor Y los consumos a la vez. Es lo
        # unico que ninguna app suelta de la casa puede calcular.
        requiere=Todos("energia", "ha"),
    ),
]
