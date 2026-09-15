"""Hora local de la casa.

Existe porque `datetime.now()` devuelve la hora del sistema, y en un
contenedor eso es UTC. El agente, en cambio, tiene en su prompt que la casa
esta en `ZONA_HORARIA`. Si la auditoria se renderiza en UTC y el agente la
lee creyendo que es hora local, informa de horas equivocadas: "apagaste el
termo a las 16:05" cuando fueron las 18:05.

Toda marca de tiempo que vaya a ver una persona o el modelo pasa por aqui.
Los instantes se siguen guardando como epoch UTC en la base de datos, que es
lo correcto; esto solo afecta a como se muestran.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .settings import Settings

RESPALDO = "UTC"


def zona(settings: Settings) -> ZoneInfo:
    try:
        return ZoneInfo(settings.zona_horaria)
    except (ZoneInfoNotFoundError, ValueError):
        # Una zona mal escrita no debe tumbar el sistema, pero tampoco
        # fingirse correcta: se cae a UTC y se nota en la salida.
        return ZoneInfo(RESPALDO)


def ahora(settings: Settings) -> datetime:
    return datetime.now(zona(settings))


def formatear(ts: float, settings: Settings, patron: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Convierte un epoch UTC a texto en la hora de la casa."""
    return datetime.fromtimestamp(ts, zona(settings)).strftime(patron)
