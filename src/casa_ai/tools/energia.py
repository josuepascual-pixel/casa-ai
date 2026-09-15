"""Herramientas de energia: solar, bateria e inversor Sungrow."""

from __future__ import annotations

from typing import Any

from ..agent.registry import Contexto, Herramienta, Riesgo, esquema


async def _estado(ctx: Contexto) -> dict[str, Any]:
    estado = await ctx.energia.estado()
    return estado.resumen()


async def _historico(ctx: Contexto, entidad: str | None = None, horas: int | None = None) -> Any:
    """El historico vive en Home Assistant, que es quien registra series."""
    if not ctx.ha.configurado:
        return (
            "El historico necesita Home Assistant configurado (es quien guarda las "
            "series temporales). El inversor por Modbus solo da el instante actual "
            "y los acumulados totales."
        )
    if not entidad:
        candidatas = await ctx.ha.buscar_entidades(dominio="sensor", texto="battery")
        candidatas += await ctx.ha.buscar_entidades(dominio="sensor", texto="pv")
        return {
            "aviso": "Indica que entidad quieres. Candidatas encontradas:",
            "entidades": candidatas[:20],
        }
    return {
        "entidad": entidad,
        "horas": horas or 24,
        "puntos": await ctx.ha.historico(entidad, horas=horas or 24),
    }


async def _modo_bateria(ctx: Contexto, modo: str, potencia_w: int | None = None) -> dict[str, Any]:
    return await ctx.energia.fijar_modo_bateria(modo, potencia_w)


def _resumen_bateria(args: dict[str, Any]) -> str:
    modo = args.get("modo")
    if modo == "autoconsumo":
        return "Devolver la bateria a modo autoconsumo (control automatico del inversor)"
    if modo == "parar":
        return "Forzar la bateria a reposo: ni carga ni descarga"
    return f"Forzar la bateria a {modo} a {args.get('potencia_w')} W"


HERRAMIENTAS = [
    Herramienta(
        nombre="energia_estado",
        descripcion=(
            "Estado actual del sistema fotovoltaico: produccion solar en W, consumo "
            "de la casa, potencia y estado de la bateria (cargando/descargando), "
            "porcentaje de carga, salud, temperatura, flujo con la red electrica "
            "(importando o exportando) y contadores acumulados en kWh. "
            "Usala siempre antes de proponer cualquier cambio en la bateria."
        ),
        esquema=esquema({}),
        riesgo=Riesgo.LECTURA,
        para_ninos=True,
        handler=_estado,
        requiere="energia",
    ),
    Herramienta(
        nombre="energia_historico",
        descripcion=(
            "Serie temporal de una entidad energetica de Home Assistant (produccion, "
            "carga de bateria, consumo...). Sin argumento 'entidad' devuelve las "
            "entidades candidatas para que elijas."
        ),
        esquema=esquema(
            {
                "entidad": {
                    "type": "string",
                    "description": "entity_id de Home Assistant, p.ej. sensor.bateria_soc",
                },
                "horas": {"type": "integer", "description": "Ventana hacia atras, por defecto 24"},
            }
        ),
        riesgo=Riesgo.LECTURA,
        handler=_historico,
        requiere="ha",
    ),
    Herramienta(
        nombre="energia_modo_bateria",
        descripcion=(
            "Cambia como trabaja la bateria. Modos: 'autoconsumo' (el inversor "
            "decide, es el estado normal y al que hay que volver siempre), "
            "'cargar' (fuerza carga a la potencia indicada, tirando de red si hace "
            "falta), 'descargar' (fuerza descarga), 'parar' (bateria en reposo). "
            "Los modos forzados PERSISTEN hasta que se vuelva a 'autoconsumo', y "
            "tienen coste economico real. Consulta energia_estado antes de usarla y "
            "explica al usuario el efecto esperado."
        ),
        esquema=esquema(
            {
                "modo": {
                    "type": "string",
                    "enum": ["autoconsumo", "cargar", "descargar", "parar"],
                },
                "potencia_w": {
                    "type": "integer",
                    "description": "Vatios para 'cargar' o 'descargar'. Obligatorio en esos modos.",
                },
            },
            obligatorias=["modo"],
        ),
        riesgo=Riesgo.ALTO,
        handler=_modo_bateria,
        resumen_confirmacion=_resumen_bateria,
        requiere="energia",
        # Leyendo via Home Assistant sin entidades de control declaradas solo
        # se puede leer, asi que la herramienta no se ofrece.
        disponible_si=lambda ctx: ctx.energia.puede_controlar,
    ),
]
