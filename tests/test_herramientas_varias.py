"""Herramientas de sistema, musica y red, y el agregador del panel.

Lo que se prueba aqui, sobre todo, es que un subsistema caido no tumba la
respuesta entera: el informe y el panel tienen que seguir sirviendo lo que si
han podido leer.
"""

from __future__ import annotations

from typing import Any

import pytest

from casa_ai.adapters.base import AdapterError
from casa_ai.adapters.energia_base import EstadoEnergia
from casa_ai.agent.registry import Contexto
from casa_ai.panel.datos import recopilar
from casa_ai.settings import Inventario, Settings
from casa_ai.store import Store
from casa_ai.tools.musica import HERRAMIENTAS as MUSICA
from casa_ai.tools.red import HERRAMIENTAS as RED
from casa_ai.tools.sistema import HERRAMIENTAS as SISTEMA

from .dobles import contexto

INFORME = next(h for h in SISTEMA if h.nombre == "informe_casa")
AUDITORIA = next(h for h in SISTEMA if h.nombre == "auditoria_acciones")


class Falso:
    configurado = False

    async def cerrar(self) -> None:
        return None


class EnergiaOk(Falso):
    configurado = True

    async def estado(self, *, usar_cache: bool = True) -> EstadoEnergia:
        return EstadoEnergia(
            pv_w=2500, bateria_w=800, bateria_soc=66.0, red_w=300,
            consumo_casa_w=1400,
        )


class EnergiaRota(Falso):
    configurado = True

    async def estado(self, *, usar_cache: bool = True) -> EstadoEnergia:
        raise AdapterError("el inversor no responde")


class MusicaFalsa(Falso):
    configurado = True

    def __init__(self) -> None:
        self.llamadas: list[tuple[str, Any, Any]] = []

    async def estado(self, nombre: str | None = None) -> dict[str, Any]:
        return {"reproductor": nombre or "Salon", "estado": "play"}

    async def estado_todos(self) -> list[dict[str, Any]]:
        return [{"reproductor": "Salon", "estado": "play"},
                {"reproductor": "Cocina", "error": "sin respuesta"}]

    async def control(self, accion: str, nombre: Any = None, valor: Any = None):
        self.llamadas.append((accion, nombre, valor))
        return {"reproductor": nombre or "Salon", "estado": "play"}

    async def presets(self, nombre: str | None = None) -> list[dict[str, Any]]:
        return [{"id": "1", "nombre": "Radio 3"}]

    async def agrupar(self, maestro: str, esclavos: list[str]) -> dict[str, Any]:
        self.llamadas.append(("agrupar", maestro, esclavos))
        return {"maestro": maestro, "agrupados": esclavos}

    async def desagrupar(self, maestro: str, esclavos: Any = None) -> dict[str, Any]:
        self.llamadas.append(("desagrupar", maestro, esclavos))
        return {"maestro": maestro, "desagrupados": esclavos or []}


class UniFiFalso(Falso):
    configurado = True

    def __init__(self) -> None:
        self.llamadas: list[tuple[str, Any]] = []

    async def salud(self) -> dict[str, Any]:
        return {"wan": {"estado": "ok", "ip": "88.1.2.3"}}

    async def dispositivos(self) -> list[dict[str, Any]]:
        return [{"nombre": "AP Salon", "estado": "conectado"}]

    async def wifis(self) -> list[dict[str, Any]]:
        return [{"id": "w1", "ssid": "Casa", "activa": True}]

    async def clientes(self, texto: str | None = None) -> list[dict[str, Any]]:
        self.llamadas.append(("clientes", texto))
        return [{"nombre": "nas", "mac": "aa:bb"}]

    async def cambiar_wifi(self, ssid: str, activar: bool) -> dict[str, Any]:
        self.llamadas.append(("wifi", (ssid, activar)))
        return {"ssid": ssid, "activa": activar}

    async def bloquear_cliente(self, mac: str, bloquear: bool) -> dict[str, Any]:
        self.llamadas.append(("bloquear", (mac, bloquear)))
        return {"mac": mac, "bloqueado": bloquear}

    async def reiniciar_dispositivo(self, mac: str) -> dict[str, Any]:
        self.llamadas.append(("reiniciar", mac))
        return {"mac": mac}

    async def camaras(self) -> list[dict[str, Any]]:
        return [{"id": "cam1", "nombre": "Puerta"}]


def _ctx(settings: Settings, store: Store, **partes: Any) -> Contexto:
    inventario = partes.pop("inventario", Inventario())
    return contexto(settings, inventario, store, **partes)


# --- Informe global ---------------------------------------------------------


async def test_el_informe_junta_los_subsistemas(settings: Settings, store: Store) -> None:
    ctx = _ctx(settings, store, energia=EnergiaOk(), unifi=UniFiFalso(),
               musica=MusicaFalsa())

    r = await INFORME.handler(ctx)

    assert r["energia"]["solar_w"] == 2500
    assert r["red"]["wan"]["estado"] == "ok"
    assert r["camaras"][0]["nombre"] == "Puerta"
    assert len(r["musica"]) == 2
    assert "momento" in r


async def test_un_subsistema_caido_no_tumba_el_informe(
    settings: Settings, store: Store
) -> None:
    """Es la razon de ser del informe: una pregunta amplia no puede fallar
    entera porque el inversor este ocupado."""
    ctx = _ctx(settings, store, energia=EnergiaRota(), unifi=UniFiFalso())

    r = await INFORME.handler(ctx)

    assert "no responde" in r["energia"]["no_disponible"]
    assert r["red"]["wan"]["estado"] == "ok"  # el resto sigue


async def test_el_informe_sin_nada_configurado(settings: Settings, store: Store) -> None:
    r = await INFORME.handler(_ctx(settings, store))
    assert set(r) >= {"momento"}


async def test_el_informe_incluye_las_notas_de_la_casa(
    settings: Settings, store: Store
) -> None:
    inv = Inventario.model_validate({"notas_casa": "La piscina no antes de las 11."})
    r = await INFORME.handler(_ctx(settings, store, inventario=inv))
    assert "antes de las 11" in r["notas_de_la_casa"]


async def test_la_auditoria_marca_los_fallos(settings: Settings, store: Store) -> None:
    store.registrar(canal="telegram", usuario="555", herramienta="red_wifi_activar",
                    argumentos={"ssid": "Casa"}, riesgo="alto",
                    resultado="ERROR: no existe", error=True)
    r = await AUDITORIA.handler(_ctx(settings, store))

    accion = r["acciones"][0]
    assert accion["fallo"] is True
    assert accion["quien"] == "telegram:555"


# --- Musica -----------------------------------------------------------------


async def test_musica_estado_de_todas_las_zonas(settings: Settings, store: Store) -> None:
    herramienta = next(h for h in MUSICA if h.nombre == "musica_estado")
    ctx = _ctx(settings, store, musica=MusicaFalsa())

    r = await herramienta.handler(ctx)

    assert len(r["reproductores"]) == 2
    assert "error" in r["reproductores"][1]  # el caido se reporta, no se oculta


async def test_musica_estado_de_una_zona(settings: Settings, store: Store) -> None:
    herramienta = next(h for h in MUSICA if h.nombre == "musica_estado")
    r = await herramienta.handler(_ctx(settings, store, musica=MusicaFalsa()),
                                  reproductor="Cocina")
    assert r["reproductor"] == "Cocina"


async def test_musica_control(settings: Settings, store: Store) -> None:
    herramienta = next(h for h in MUSICA if h.nombre == "musica_control")
    musica = MusicaFalsa()
    ctx = _ctx(settings, store, musica=musica)

    await herramienta.handler(ctx, accion="volumen", reproductor="Salon", valor=25)

    assert musica.llamadas == [("volumen", "Salon", 25)]


async def test_musica_presets(settings: Settings, store: Store) -> None:
    herramienta = next(h for h in MUSICA if h.nombre == "musica_presets")
    r = await herramienta.handler(_ctx(settings, store, musica=MusicaFalsa()))
    assert r["presets"][0]["nombre"] == "Radio 3"


async def test_agrupar_y_desagrupar(settings: Settings, store: Store) -> None:
    herramienta = next(h for h in MUSICA if h.nombre == "musica_agrupar")
    musica = MusicaFalsa()
    ctx = _ctx(settings, store, musica=musica)

    await herramienta.handler(ctx, maestro="Salon", zonas=["Cocina"])
    await herramienta.handler(ctx, maestro="Salon", zonas=["Cocina"], desagrupar=True)

    assert musica.llamadas[0][0] == "agrupar"
    assert musica.llamadas[1][0] == "desagrupar"


# --- Red --------------------------------------------------------------------


async def test_red_estado_junta_las_tres_cosas(settings: Settings, store: Store) -> None:
    herramienta = next(h for h in RED if h.nombre == "red_estado")
    r = await herramienta.handler(_ctx(settings, store, unifi=UniFiFalso()))

    assert r["salud"]["wan"]["ip"] == "88.1.2.3"
    assert r["dispositivos"][0]["nombre"] == "AP Salon"
    assert r["wifis"][0]["ssid"] == "Casa"


async def test_red_clientes_con_filtro(settings: Settings, store: Store) -> None:
    herramienta = next(h for h in RED if h.nombre == "red_clientes")
    unifi = UniFiFalso()

    r = await herramienta.handler(_ctx(settings, store, unifi=unifi), texto="nas")

    assert r["conectados"] == 1
    assert unifi.llamadas == [("clientes", "nas")]


async def test_las_acciones_de_red_llegan_al_adaptador(
    settings: Settings, store: Store
) -> None:
    unifi = UniFiFalso()
    ctx = _ctx(settings, store, unifi=unifi)
    por_nombre = {h.nombre: h for h in RED}

    await por_nombre["red_wifi_activar"].handler(ctx, ssid="Casa", activar=False)
    await por_nombre["red_bloquear_cliente"].handler(ctx, mac="aa:bb", bloquear=True)
    await por_nombre["red_reiniciar_dispositivo"].handler(ctx, mac="cc:dd")

    assert unifi.llamadas == [
        ("wifi", ("Casa", False)),
        ("bloquear", ("aa:bb", True)),
        ("reiniciar", "cc:dd"),
    ]


@pytest.mark.parametrize(
    "nombre",
    ["red_wifi_activar", "red_bloquear_cliente", "red_reiniciar_dispositivo"],
)
def test_los_resumenes_de_confirmacion_dicen_la_consecuencia(nombre: str) -> None:
    herramienta = next(h for h in RED if h.nombre == nombre)
    assert herramienta.resumen_confirmacion is not None
    texto = herramienta.resumir(
        {"ssid": "Casa", "activar": False, "mac": "aa:bb", "bloquear": True}
    )
    assert len(texto) > 20


# --- Agregador del panel ----------------------------------------------------


async def test_el_panel_recopila_lo_que_hay(settings: Settings, store: Store) -> None:
    inv = Inventario.model_validate({
        "camaras": [{"nombre": "Puerta", "id_protect": "cam1", "zona": "entrada"}]
    })
    ctx = _ctx(settings, store, energia=EnergiaOk(), unifi=UniFiFalso(),
               musica=MusicaFalsa(), inventario=inv)

    datos = await recopilar(ctx)

    assert datos["energia"]["resumen"]["solar_w"] == 2500
    assert datos["energia"]["mezcla"]["consumo_w"] == 1400
    assert datos["red"]["wan"]["estado"] == "ok"
    assert datos["camaras"] == [{"nombre": "Puerta", "zona": "entrada"}]
    assert "momento" in datos


async def test_el_panel_aguanta_un_subsistema_caido(
    settings: Settings, store: Store
) -> None:
    ctx = _ctx(settings, store, energia=EnergiaRota(), unifi=UniFiFalso())

    datos = await recopilar(ctx)

    assert "no_disponible" in datos["energia"]
    assert datos["red"]["wan"]["estado"] == "ok"


async def test_el_panel_sin_nada_configurado(settings: Settings, store: Store) -> None:
    datos = await recopilar(_ctx(settings, store))
    assert datos["camaras"] == []
    assert "momento" in datos
