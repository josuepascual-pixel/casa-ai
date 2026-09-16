"""Ordenes para mas tarde: «sube la persiana a las 8», «recuerdame el viernes».

Es lo que le faltaba al asistente para ser uno: sin esto, «a las ocho» solo
podia responderse con un «ahora no puedo». La herramienta apunta la orden; el
planificador (automations.rutinas) la lanza a su hora como un turno del agente
con la identidad de quien la pidio (mismos permisos: un nino programa lo que
un nino puede hacer) y el resultado vuelve por su mismo chat.

Sin humano delante no hay confirmacion posible, asi que una orden programada
NUNCA ejecuta una accion de riesgo alto (bateria, wifi, bus KNX): se lo dice
al usuario en su momento. Es la misma regla que para las rutinas.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from ..adapters.base import AdapterError
from ..agent.registry import Contexto, Herramienta, Riesgo, esquema
from ..tiempo import ahora, zona

# Canales por los que se puede entregar el resultado sin que nadie pregunte.
CANALES_CON_AVISO = frozenset({"telegram", "whatsapp"})
MAX_ACTIVAS = 50
MAX_ORDEN = 500
MAX_ANTELACION = timedelta(days=366)

DIAS = ("lun", "mar", "mie", "jue", "vie", "sab", "dom")
GRUPOS = {
    "diario": set(range(7)),
    "laborables": set(range(5)),
    "fin de semana": {5, 6},
}
_HORA = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def dias_de(texto: str) -> set[int]:
    """«diario», «laborables», «fin de semana» o «lun,mie,vie» a numeros de dia."""
    clave = texto.strip().lower()
    if clave in GRUPOS:
        return set(GRUPOS[clave])
    salida: set[int] = set()
    for parte in clave.split(","):
        nombre = parte.strip()[:3]
        if nombre not in DIAS:
            raise AdapterError(
                f"No entiendo el dia «{parte.strip()}». Vale: diario, laborables, "
                "fin de semana, o dias separados por comas (lun,mar,mie,jue,vie,sab,dom)."
            )
        salida.add(DIAS.index(nombre))
    return salida


def siguiente_repeticion(desde: datetime, hora: str, dias: str) -> datetime:
    """El proximo instante posterior a `desde` que cae en esa hora y esos dias."""
    m = _HORA.match(hora.strip())
    if m is None:
        raise AdapterError(f"La hora «{hora}» no es HH:MM.")
    permitidos = dias_de(dias)
    candidato = desde.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
    if candidato <= desde:
        candidato += timedelta(days=1)
    while candidato.weekday() not in permitidos:
        candidato += timedelta(days=1)
    return candidato


def instante_de(texto: str, ctx: Contexto) -> datetime:
    """Una fecha y hora en formato ISO, en la hora de la casa si no trae zona."""
    try:
        momento = datetime.fromisoformat(texto.strip())
    except ValueError as e:
        raise AdapterError(f"No entiendo la fecha «{texto}»: usa AAAA-MM-DDTHH:MM.") from e
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=zona(ctx.settings))
    return momento


def _resumen(p: dict[str, Any], ctx: Contexto) -> dict[str, Any]:
    cuando = datetime.fromtimestamp(p["siguiente"], zona(ctx.settings))
    salida = {
        "id": p["id"],
        "orden": p["orden"],
        "proxima_vez": cuando.strftime("%A %d/%m/%Y %H:%M"),
    }
    if p.get("hora"):
        salida["repite"] = f"{p['dias']} a las {p['hora']}"
    return salida


async def _programar(
    ctx: Contexto, orden: str, cuando: str | None, hora: str | None, dias: str | None
) -> dict[str, Any]:
    orden = orden.strip()
    if not orden:
        raise AdapterError("La orden esta vacia.")
    if len(orden) > MAX_ORDEN:
        raise AdapterError(f"La orden es demasiado larga (maximo {MAX_ORDEN} caracteres).")
    if len(ctx.store.programaciones(canal=ctx.canal, usuario=ctx.usuario)) >= MAX_ACTIVAS:
        raise AdapterError(f"Ya hay {MAX_ACTIVAS} programaciones activas; cancela alguna.")

    momento = ahora(ctx.settings)
    if hora:
        siguiente = siguiente_repeticion(momento, hora, dias or "diario")
        hora, dias = siguiente.strftime("%H:%M"), (dias or "diario").strip().lower()
    elif cuando:
        siguiente = instante_de(cuando, ctx)
        if siguiente <= momento:
            raise AdapterError("Esa fecha ya ha pasado.")
        if siguiente - momento > MAX_ANTELACION:
            raise AdapterError("Como mucho con un ano de antelacion.")
        hora = dias = None
    else:
        raise AdapterError("Di cuando: una fecha y hora (cuando) o una hora y dias (hora, dias).")

    id_ = ctx.store.programar(
        canal=ctx.canal,
        usuario=ctx.usuario,
        conversacion=ctx.conversacion,
        orden=orden,
        siguiente=siguiente.timestamp(),
        hora=hora,
        dias=dias,
    )
    fila = {"id": id_, "orden": orden, "siguiente": siguiente.timestamp(), "hora": hora,
            "dias": dias}
    return {"programada": _resumen(fila, ctx)}


async def _listar(ctx: Contexto) -> dict[str, Any]:
    filas = ctx.store.programaciones(canal=ctx.canal, usuario=ctx.usuario)
    return {"programaciones": [_resumen(p, ctx) for p in filas]}


async def _cancelar(ctx: Contexto, id: int) -> dict[str, Any]:  # noqa: A002 - nombre del esquema
    if not ctx.store.cancelar_programacion(id, canal=ctx.canal, usuario=ctx.usuario):
        raise AdapterError(f"No hay ninguna programacion tuya con id {id} activa.")
    return {"cancelada": id}


def _se_puede_avisar(ctx: Contexto) -> bool:
    return ctx.canal in CANALES_CON_AVISO


def _lo_pide_una_persona(ctx: Contexto) -> bool:
    """Solo desde un turno con alguien delante.

    Una orden programada corre con `confirmacion="imposible"`; si desde ahi
    pudiera programar otra, el agente se encadenaria a si mismo sin que nadie
    se lo pidiera. Listar si puede: es solo lectura.
    """
    return _se_puede_avisar(ctx) and ctx.confirmacion != "imposible"


HERRAMIENTAS = [
    Herramienta(
        nombre="programar",
        descripcion=(
            "Deja una orden o un recordatorio para mas tarde: «sube las persianas a "
            "las 8», «recuerdame el viernes a las 17:00 que llame al dentista», «pon "
            "musica en la cocina los laborables a las 7:30». A su hora la ejecutas tu "
            "mismo y el resultado llega por este chat. Para una sola vez, da `cuando` "
            "en ISO (AAAA-MM-DDTHH:MM, hora local; calcula la fecha con el contexto); "
            "para algo que se repite, `hora` (HH:MM) y `dias` (diario, laborables, fin "
            "de semana, o lun,mar,mie,jue,vie,sab,dom). La orden escribela completa y "
            "en imperativo, como si te la dijeran en ese momento. Una orden programada "
            "no puede hacer acciones de riesgo alto (bateria, wifi, KNX): avisalo."
        ),
        esquema=esquema(
            {
                "orden": {"type": "string", "description": "Que hacer o que recordar"},
                "cuando": {
                    "type": "string",
                    "description": "Una sola vez: fecha y hora ISO (2026-09-17T08:00)",
                },
                "hora": {"type": "string", "description": "Repetida: HH:MM"},
                "dias": {
                    "type": "string",
                    "description": "Repetida: diario | laborables | fin de semana | lun,mie",
                },
            },
            obligatorias=["orden"],
        ),
        riesgo=Riesgo.MEDIO,
        handler=_programar,
        resumen_confirmacion=lambda a: f"Programar: {a.get('orden', '')}",
        para_ninos=True,
        disponible_si=_lo_pide_una_persona,
    ),
    Herramienta(
        nombre="programaciones_listar",
        descripcion="Lista las ordenes y recordatorios programados por quien habla.",
        esquema=esquema({}),
        riesgo=Riesgo.LECTURA,
        handler=_listar,
        para_ninos=True,
        disponible_si=_se_puede_avisar,
    ),
    Herramienta(
        nombre="programacion_cancelar",
        descripcion="Cancela una programacion por su id (solo las de quien habla).",
        esquema=esquema({"id": {"type": "integer"}}, obligatorias=["id"]),
        riesgo=Riesgo.MEDIO,
        handler=_cancelar,
        para_ninos=True,
        disponible_si=_lo_pide_una_persona,
    ),
]
