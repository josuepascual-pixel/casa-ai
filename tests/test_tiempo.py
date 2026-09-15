"""Las marcas de tiempo van en la hora de la casa, no en la del contenedor.

En Docker la hora del sistema es UTC. Si la auditoria se renderizase asi, el
agente la leeria creyendo que es hora local e informaria de horas erroneas.
"""

from __future__ import annotations

from casa_ai.settings import Settings
from casa_ai.tiempo import ahora, formatear, zona

# 2026-09-14 12:00:00 UTC
TS = 1789041600.0


def test_formatea_en_la_zona_de_la_casa(settings: Settings) -> None:
    madrid = settings.model_copy(update={"zona_horaria": "Europe/Madrid"})
    utc = settings.model_copy(update={"zona_horaria": "UTC"})

    # Madrid en septiembre va a UTC+2
    assert formatear(TS, madrid, "%H:%M") == "14:00"
    assert formatear(TS, utc, "%H:%M") == "12:00"


def test_otra_zona_da_otra_hora(settings: Settings) -> None:
    canarias = settings.model_copy(update={"zona_horaria": "Atlantic/Canary"})
    assert formatear(TS, canarias, "%H:%M") == "13:00"


def test_zona_mal_escrita_cae_a_utc_sin_reventar(settings: Settings) -> None:
    roto = settings.model_copy(update={"zona_horaria": "Marte/Olympus"})
    assert str(zona(roto)) == "UTC"
    assert formatear(TS, roto, "%H:%M") == "12:00"


def test_ahora_es_consciente_de_la_zona(settings: Settings) -> None:
    momento = ahora(settings)
    assert momento.tzinfo is not None


async def test_la_auditoria_se_muestra_en_hora_de_la_casa(
    settings: Settings, store, inventario
) -> None:
    """Comprobado a traves de la herramienta, que es lo que ve el agente."""
    from casa_ai.tools.sistema import HERRAMIENTAS

    from .dobles import contexto

    madrid = settings.model_copy(update={"zona_horaria": "Europe/Madrid"})
    ctx = contexto(madrid, inventario, store)
    store.registrar(
        canal="telegram", usuario="123", herramienta="energia_modo_bateria",
        argumentos={}, riesgo="alto", resultado="ok",
    )

    auditoria = next(h for h in HERRAMIENTAS if h.nombre == "auditoria_acciones")
    resultado = await auditoria.handler(ctx)

    cuando = resultado["acciones"][0]["cuando"]
    esperado = formatear(store.auditoria(1)[0]["ts"], madrid)
    assert cuando == esperado
