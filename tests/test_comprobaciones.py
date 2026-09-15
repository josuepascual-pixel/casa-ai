"""La verificacion como datos, para el movil y el API, no solo para la terminal."""

from __future__ import annotations

from casa_ai.app import Aplicacion
from casa_ai.comprobaciones import comprobar_subsistemas, texto
from casa_ai.settings import Inventario, Settings

from .dobles import adaptador, contexto


def _app(settings: Settings, store, **partes):
    app = Aplicacion.__new__(Aplicacion)
    app.settings = settings
    app.inventario = Inventario()
    app.ctx = contexto(settings, app.inventario, store, **partes)
    return app


async def test_cada_subsistema_dice_ok_fallo_o_sin_configurar(settings, store) -> None:
    async def estados(_self):
        return [{"entity_id": "light.a"}, {"entity_id": "sensor.knx_b"}]

    async def estado_todos(_self):
        raise RuntimeError("nadie responde en el puerto 11000")

    app = _app(
        settings, store,
        ha=adaptador(True, estados=estados),
        musica=adaptador(True, estado_todos=estado_todos),
        unifi=False, energia=False, knx=False,
    )
    por_nombre = {c.nombre: c for c in await comprobar_subsistemas(app)}

    assert por_nombre["Home Assistant"].estado == "ok"
    assert "1 parecen de KNX" in por_nombre["Home Assistant"].detalle
    assert por_nombre["Musica BluOS"].estado == "fallo"
    assert "11000" in por_nombre["Musica BluOS"].detalle
    assert por_nombre["Red UniFi"].estado == "sin_configurar"
    assert "Camaras UniFi Protect" not in por_nombre  # sin UniFi no se intenta

    salida = texto(list(por_nombre.values()))
    assert "✅ Home Assistant" in salida and "❌ Musica BluOS" in salida
    assert "/descubrir" in salida  # la pista de BluOS


async def test_una_planta_sin_bateria_no_revienta_la_comprobacion(settings, store) -> None:
    from casa_ai.adapters.energia_base import EstadoEnergia

    async def estado(_self, *, usar_cache=True):
        return EstadoEnergia(pv_w=41_200, bateria_w=0, bateria_soc=None, red_w=6_400,
                             consumo_casa_w=34_800)

    app = _app(settings, store, energia=adaptador(True, estado=estado, puede_controlar=False),
               ha=False, musica=False, unifi=False, knx=False)
    energia = next(c for c in await comprobar_subsistemas(app) if c.nombre.startswith("Energia"))
    assert energia.estado == "ok" and "sin dato de bateria" in energia.detalle
    assert "(solo lectura)" in energia.detalle
