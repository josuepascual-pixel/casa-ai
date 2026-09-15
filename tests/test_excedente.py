"""Gestion del excedente solar.

Es la unica capacidad que ninguna de las apps del movil puede dar por
separado, porque hace falta ver el inversor y los consumos a la vez. Y lleva
aritmetica, asi que conviene probarla.
"""

from __future__ import annotations

import pytest
import respx

from casa_ai.adapters.energia_base import EstadoEnergia
from casa_ai.adapters.homeassistant import HomeAssistant
from casa_ai.agent.registry import Contexto
from casa_ai.settings import Inventario, Settings
from casa_ai.store import Store
from casa_ai.tools.dispositivos import HERRAMIENTAS

from .dobles import contexto, entidad, estado_ha, estados_ha

APARATOS = {
    "dispositivos": [
        {"nombre": "termo", "categoria": "agua_caliente", "entidad": "switch.termo",
         "consumo_w": 2000, "excedente": True, "prioridad": 1},
        {"nombre": "coche", "categoria": "cargador_vehiculo", "entidad": "switch.coche",
         "consumo_w": 7400, "excedente": True, "prioridad": 2},
        {"nombre": "spa", "categoria": "spa", "entidad": "switch.spa",
         "consumo_w": 3000, "excedente": True, "prioridad": 3},
        {"nombre": "lavadora", "categoria": "electrodomestico", "entidad": "switch.lavadora",
         "consumo_w": 2200, "excedente": True, "prioridad": 4},
        {"nombre": "riego", "categoria": "riego", "entidad": "switch.riego",
         "consumo_w": 50},
    ]
}

EXCEDENTE = next(h for h in HERRAMIENTAS if h.nombre == "excedente_solar")
LISTAR = next(h for h in HERRAMIENTAS if h.nombre == "dispositivos_estado")


class EnergiaFalsa:
    configurado = True
    puede_controlar = True

    def __init__(self, estado: EstadoEnergia) -> None:
        self._estado = estado

    async def estado(self, *, usar_cache: bool = True) -> EstadoEnergia:
        return self._estado

    async def cerrar(self) -> None:
        return None


def _ctx(
    settings: Settings, store: Store, inventario: Inventario, estado: EstadoEnergia
) -> Contexto:
    return contexto(
        settings, inventario, store,
        ha=HomeAssistant(settings), energia=EnergiaFalsa(estado),
    )


def _estados_ha(encendidos: set[str]) -> None:
    """Los cinco interruptores del inventario de prueba.

    Se simula la lista entera porque es lo que pide el codigo: antes de leer
    varios aparatos precalienta `/api/states` y las lecturas sueltas salen de
    ahi. Las rutas por entidad quedan por si alguna se pide fuera de ese
    camino.
    """
    entidades = ("switch.termo", "switch.coche", "switch.spa",
                 "switch.lavadora", "switch.riego")
    estados_ha(*(entidad(e, "on" if e in encendidos else "off") for e in entidades))
    for e in entidades:
        estado_ha(e, "on" if e in encendidos else "off")


def _energia(*, red_w: int, bateria_w: int = 0, soc: float = 50.0) -> EstadoEnergia:
    return EstadoEnergia(
        pv_w=max(0, red_w) + 1000, bateria_w=bateria_w, bateria_soc=soc,
        red_w=red_w, consumo_casa_w=1000,
    )


@pytest.fixture
def inventario_aparatos() -> Inventario:
    return Inventario.model_validate(APARATOS)


@respx.mock
async def test_reparte_el_excedente_por_prioridad(
    settings: Settings, store: Store, inventario_aparatos: Inventario
) -> None:
    """Exportando 5 kW, con 300 W de margen, quedan 4700 para repartir."""
    _estados_ha(encendidos=set())
    ctx = _ctx(settings, store, inventario_aparatos, _energia(red_w=5000))

    r = await EXCEDENTE.handler(ctx)

    assert r["excedente"]["disponible_para_gastar_w"] == 4700
    encender = [x["nombre"] for x in r["recomendacion"]["encender"]]
    # termo (2000, p1) entra; coche (7400) no cabe; spa (3000) tampoco en los
    # 2700 restantes; lavadora (2200) si.
    assert encender == ["termo", "lavadora"]
    assert r["recomendacion"]["sobraria_sin_usar_w"] == 500
    await ctx.ha.cerrar()


@respx.mock
async def test_no_recomienda_lo_que_ya_esta_encendido(
    settings: Settings, store: Store, inventario_aparatos: Inventario
) -> None:
    _estados_ha(encendidos={"switch.termo"})
    ctx = _ctx(settings, store, inventario_aparatos, _energia(red_w=5000))

    r = await EXCEDENTE.handler(ctx)

    encender = [x["nombre"] for x in r["recomendacion"]["encender"]]
    assert "termo" not in encender
    assert "spa" in encender  # ahora caben los 3000 del spa
    await ctx.ha.cerrar()


@respx.mock
async def test_la_bateria_a_medias_no_cuenta_como_excedente(
    settings: Settings, store: Store, inventario_aparatos: Inventario
) -> None:
    """Con la bateria al 60 %, cargarla es mejor uso que encender algo."""
    _estados_ha(encendidos=set())
    ctx = _ctx(
        settings, store, inventario_aparatos,
        _energia(red_w=0, bateria_w=4000, soc=60.0),
    )

    r = await EXCEDENTE.handler(ctx)

    assert r["excedente"]["margen_de_bateria_w"] == 0
    assert r["excedente"]["disponible_para_gastar_w"] == 0
    assert r["recomendacion"]["encender"] == []
    await ctx.ha.cerrar()


@respx.mock
async def test_la_bateria_llena_si_cuenta(
    settings: Settings, store: Store, inventario_aparatos: Inventario
) -> None:
    """Al 95 % la potencia que entra en la bateria se puede redirigir."""
    _estados_ha(encendidos=set())
    ctx = _ctx(
        settings, store, inventario_aparatos,
        _energia(red_w=0, bateria_w=4000, soc=95.0),
    )

    r = await EXCEDENTE.handler(ctx)

    assert r["excedente"]["margen_de_bateria_w"] == 4000
    assert r["excedente"]["disponible_para_gastar_w"] == 3700
    assert [x["nombre"] for x in r["recomendacion"]["encender"]] == ["termo"]
    await ctx.ha.cerrar()


@respx.mock
async def test_importando_recomienda_apagar_el_menos_prioritario(
    settings: Settings, store: Store, inventario_aparatos: Inventario
) -> None:
    _estados_ha(encendidos={"switch.termo", "switch.lavadora"})
    ctx = _ctx(settings, store, inventario_aparatos, _energia(red_w=-1500))

    r = await EXCEDENTE.handler(ctx)

    assert r["recomendacion"]["encender"] == []
    apagar = [x["nombre"] for x in r["recomendacion"]["apagar"]]
    # Se empieza por el de menor prioridad: la lavadora antes que el termo.
    assert apagar[0] == "lavadora"
    await ctx.ha.cerrar()


@respx.mock
async def test_el_umbral_y_el_margen_son_configurables(
    settings: Settings, store: Store, inventario_aparatos: Inventario
) -> None:
    sin_margen = settings.model_copy(
        update={"excedente_margen_w": 0, "excedente_soc_minimo": 50}
    )
    _estados_ha(encendidos=set())
    ctx = _ctx(
        sin_margen, store, inventario_aparatos,
        _energia(red_w=1000, bateria_w=2000, soc=60.0),
    )

    r = await EXCEDENTE.handler(ctx)

    assert r["excedente"]["disponible_para_gastar_w"] == 3000
    await ctx.ha.cerrar()


@respx.mock
async def test_los_no_gestionables_no_entran_en_el_reparto(
    settings: Settings, store: Store, inventario_aparatos: Inventario
) -> None:
    _estados_ha(encendidos=set())
    ctx = _ctx(settings, store, inventario_aparatos, _energia(red_w=5000))

    r = await EXCEDENTE.handler(ctx)

    candidatos = [c["nombre"] for c in r["candidatos"]]
    assert "riego" not in candidatos  # declarado sin excedente: true
    await ctx.ha.cerrar()


async def test_sin_aparatos_declarados_lo_explica(
    settings: Settings, store: Store
) -> None:
    ctx = _ctx(settings, store, Inventario(), _energia(red_w=0))
    r = await LISTAR.handler(ctx)
    assert "config/config.yaml" in r["aviso"]
    await ctx.ha.cerrar()


@respx.mock
async def test_filtro_por_categoria(
    settings: Settings, store: Store, inventario_aparatos: Inventario
) -> None:
    _estados_ha(encendidos=set())
    ctx = _ctx(settings, store, inventario_aparatos, _energia(red_w=0))

    r = await LISTAR.handler(ctx, categoria="cargador_vehiculo")

    assert [d["nombre"] for d in r["dispositivos"]] == ["coche"]
    await ctx.ha.cerrar()


@respx.mock
async def test_categoria_inexistente_lista_las_que_hay(
    settings: Settings, store: Store, inventario_aparatos: Inventario
) -> None:
    from casa_ai.adapters.base import AdapterError

    ctx = _ctx(settings, store, inventario_aparatos, _energia(red_w=0))
    with pytest.raises(AdapterError, match="cargador_vehiculo"):
        await LISTAR.handler(ctx, categoria="nave_espacial")
    await ctx.ha.cerrar()


@respx.mock
async def test_no_propone_apagar_lo_que_no_declara_su_consumo(
    settings: Settings, store: Store
) -> None:
    """Contarlos como 0 W hacia que el deficit nunca se cubriera y el bucle
    acabara proponiendo apagar la casa entera por 50 W de importacion."""
    inventario = Inventario.model_validate({
        "dispositivos": [
            {"nombre": "sin consumo A", "entidad": "switch.a", "excedente": True,
             "prioridad": 1},
            {"nombre": "sin consumo B", "entidad": "switch.b", "excedente": True,
             "prioridad": 2},
            {"nombre": "con consumo", "entidad": "switch.c", "consumo_w": 2000,
             "excedente": True, "prioridad": 3},
        ]
    })
    estados_ha(*(entidad(e, "on") for e in ("switch.a", "switch.b", "switch.c")))
    ctx = _ctx(settings, store, inventario, _energia(red_w=-50))

    r = await EXCEDENTE.handler(ctx)

    apagar = [x["nombre"] for x in r["recomendacion"]["apagar"]]
    assert apagar == ["con consumo"]  # solo el que se puede razonar
    await ctx.ha.cerrar()


def test_excedente_solar_exige_inversor_y_home_assistant(
    settings: Settings, store: Store, inventario_aparatos: Inventario
) -> None:
    """`requiere` significa "cualquiera de estos", pero esta herramienta
    necesita los dos: se ofrecia con solo HA y fallaba siempre."""

    from casa_ai.tools import construir_registro

    def ctx_con(energia: bool, ha: bool) -> Contexto:
        return contexto(settings, inventario_aparatos, store, ha=ha, energia=energia)

    registro = construir_registro()
    nombres = lambda c: {h.nombre for h in registro.disponibles(c)}  # noqa: E731

    assert "excedente_solar" in nombres(ctx_con(energia=True, ha=True))
    assert "excedente_solar" not in nombres(ctx_con(energia=False, ha=True))
    assert "excedente_solar" not in nombres(ctx_con(energia=True, ha=False))
