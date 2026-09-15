"""Energia leida via Home Assistant, que es lo que permite ejecutar en la nube."""

from __future__ import annotations

import httpx
import pytest
import respx

from casa_ai.adapters.base import AdapterError, NoConfigurado
from casa_ai.adapters.energia_ha import EnergiaHA
from casa_ai.adapters.homeassistant import HomeAssistant
from casa_ai.settings import EnergiaHAConfig, Settings

from .conftest import HA_URL

BASE = f"{HA_URL}/api/states"


def _sensor(entity_id: str, estado: str):
    return respx.get(f"{BASE}/{entity_id}").mock(
        return_value=httpx.Response(200, json={"entity_id": entity_id, "state": estado,
                                               "attributes": {}})
    )


CONFIG_LECTURA = EnergiaHAConfig(
    solar="sensor.solar",
    bateria_potencia="sensor.bat_w",
    bateria_soc="sensor.bat_soc",
    red="sensor.red",
)

CONFIG_CONTROL = CONFIG_LECTURA.model_copy(
    update={
        "control_modo_ems": "select.ems",
        "control_comando": "select.cmd",
        "control_potencia": "number.potencia",
    }
)


def _adaptador(settings: Settings, config: EnergiaHAConfig) -> tuple[EnergiaHA, HomeAssistant]:
    ha = HomeAssistant(settings)
    return EnergiaHA(settings, ha, config), ha


@respx.mock
async def test_lee_los_sensores_y_deriva_el_consumo(settings: Settings) -> None:
    energia, ha = _adaptador(settings, CONFIG_LECTURA)
    _sensor("sensor.solar", "4200")
    _sensor("sensor.bat_w", "1200")     # cargando
    _sensor("sensor.bat_soc", "64.5")
    _sensor("sensor.red", "1000")       # exportando

    estado = await energia.estado()
    r = estado.resumen()

    assert r["solar_w"] == 4200
    assert r["bateria_estado"] == "cargando"
    assert r["bateria_soc_pct"] == pytest.approx(64.5)
    assert r["red_estado"] == "exportando"
    # 4200 solares - 1200 a bateria - 1000 a red = 2000 en casa
    assert r["consumo_casa_w"] == 2000
    assert r["origen_de_los_datos"] == "home_assistant"
    await ha.cerrar()


@respx.mock
async def test_no_inventa_lo_que_no_ha_leido(settings: Settings) -> None:
    """Devolver 0 % de salud de bateria por no poder leerla seria mentir."""
    energia, ha = _adaptador(settings, CONFIG_LECTURA)
    _sensor("sensor.solar", "1000")
    _sensor("sensor.bat_w", "0")
    _sensor("sensor.bat_soc", "50")
    _sensor("sensor.red", "-500")

    r = (await energia.estado()).resumen()

    assert "bateria_salud_pct" not in r
    assert "bateria_temp_c" not in r
    assert "modo_ems" not in r
    assert "acumulados_kwh" not in r
    await ha.cerrar()


@respx.mock
async def test_sensor_no_disponible_no_rompe_la_lectura(settings: Settings) -> None:
    energia, ha = _adaptador(settings, CONFIG_LECTURA)
    _sensor("sensor.solar", "3000")
    _sensor("sensor.bat_w", "unavailable")
    _sensor("sensor.bat_soc", "70")
    _sensor("sensor.red", "unknown")

    estado = await energia.estado()

    assert estado.pv_w == 3000
    assert estado.bateria_w == 0
    assert "bateria_potencia" in estado.crudo["sin_lectura"]
    await ha.cerrar()


@respx.mock
async def test_sin_produccion_solar_legible_falla_claro(settings: Settings) -> None:
    energia, ha = _adaptador(settings, CONFIG_LECTURA)
    _sensor("sensor.solar", "unavailable")
    _sensor("sensor.bat_w", "0")
    _sensor("sensor.bat_soc", "50")
    _sensor("sensor.red", "0")

    with pytest.raises(AdapterError, match="produccion solar"):
        await energia.estado()
    await ha.cerrar()


@respx.mock
async def test_los_factores_convierten_unidades_y_signos(settings: Settings) -> None:
    """Un sensor en kW con el signo de red al contrario tiene arreglo por config."""
    config = CONFIG_LECTURA.model_copy(update={"factor_solar": 1000.0, "factor_red": -1.0})
    energia, ha = _adaptador(settings, config)
    _sensor("sensor.solar", "4.2")      # kW
    _sensor("sensor.bat_w", "0")
    _sensor("sensor.bat_soc", "80")
    _sensor("sensor.red", "1500")       # el sensor dice +1500 importando

    estado = await energia.estado()

    assert estado.pv_w == 4200
    assert estado.red_w == -1500
    assert estado.resumen()["red_estado"] == "importando"
    await ha.cerrar()


@respx.mock
async def test_sensor_propio_de_consumo_gana_al_balance(settings: Settings) -> None:
    config = CONFIG_LECTURA.model_copy(update={"consumo": "sensor.casa"})
    energia, ha = _adaptador(settings, config)
    _sensor("sensor.solar", "4000")
    _sensor("sensor.bat_w", "0")
    _sensor("sensor.bat_soc", "50")
    _sensor("sensor.red", "1000")
    _sensor("sensor.casa", "2750")

    assert (await energia.estado()).consumo_casa_w == 2750
    await ha.cerrar()


@respx.mock
async def test_modo_ems_se_traduce_del_texto_del_select(settings: Settings) -> None:
    energia, ha = _adaptador(settings, CONFIG_CONTROL)
    _sensor("sensor.solar", "100")
    _sensor("sensor.bat_w", "0")
    _sensor("sensor.bat_soc", "50")
    _sensor("sensor.red", "0")
    _sensor("select.ems", "Self-consumption mode (default)")

    assert (await energia.estado()).resumen()["modo_ems"] == "autoconsumo"
    await ha.cerrar()


async def test_sin_configurar_lo_dice(settings: Settings) -> None:
    energia, ha = _adaptador(settings, EnergiaHAConfig())
    assert energia.configurado is False
    with pytest.raises(NoConfigurado, match="energia_ha"):
        await energia.estado()
    await ha.cerrar()


# --- Control ----------------------------------------------------------------


async def test_sin_entidades_de_control_no_finge_poder_controlar(
    settings: Settings,
) -> None:
    energia, ha = _adaptador(settings, CONFIG_LECTURA)
    assert energia.puede_controlar is False

    with pytest.raises(AdapterError, match="Solo puedo LEER"):
        await energia.fijar_modo_bateria("cargar", 3000)
    await ha.cerrar()


@respx.mock
async def test_cargar_llama_a_los_servicios_correctos(settings: Settings) -> None:
    energia, ha = _adaptador(settings, CONFIG_CONTROL)
    select = respx.post(f"{HA_URL}/api/services/select/select_option").mock(
        return_value=httpx.Response(200, json=[])
    )
    number = respx.post(f"{HA_URL}/api/services/number/set_value").mock(
        return_value=httpx.Response(200, json=[])
    )

    resultado = await energia.fijar_modo_bateria("cargar", 2500)

    assert resultado["potencia_w"] == 2500
    assert resultado["via"] == "home_assistant"
    assert number.call_count == 1
    assert select.call_count == 2  # modo forzado + comando de carga
    await ha.cerrar()


@respx.mock
async def test_autoconsumo_devuelve_el_control(settings: Settings) -> None:
    energia, ha = _adaptador(settings, CONFIG_CONTROL)
    select = respx.post(f"{HA_URL}/api/services/select/select_option").mock(
        return_value=httpx.Response(200, json=[])
    )

    resultado = await energia.fijar_modo_bateria("autoconsumo")

    assert resultado["modo"] == "autoconsumo"
    assert select.call_count == 2
    await ha.cerrar()


async def test_el_tope_de_potencia_tambien_rige_via_ha(settings: Settings) -> None:
    """El limite de seguridad no puede depender del camino que se use."""
    energia, ha = _adaptador(settings, CONFIG_CONTROL)
    with pytest.raises(AdapterError, match="tope de seguridad"):
        await energia.fijar_modo_bateria("cargar", 99000)
    await ha.cerrar()


@respx.mock
async def test_entidad_de_control_de_tipo_inesperado(settings: Settings) -> None:
    config = CONFIG_CONTROL.model_copy(update={"control_potencia": "sensor.no_escribible"})
    energia, ha = _adaptador(settings, config)
    respx.post(f"{HA_URL}/api/services/select/select_option").mock(
        return_value=httpx.Response(200, json=[])
    )
    with pytest.raises(AdapterError, match="esperaba una entidad"):
        await energia.fijar_modo_bateria("descargar", 1000)
    await ha.cerrar()


# --- Seleccion del adaptador ------------------------------------------------


def test_auto_prefiere_modbus_cuando_hay_inversor(settings: Settings) -> None:
    from casa_ai.adapters.sungrow import Sungrow
    from casa_ai.app import Aplicacion

    app = Aplicacion(settings)  # el fixture trae SUNGROW_HOST
    assert isinstance(app.ctx.energia, Sungrow)


def test_auto_cae_a_home_assistant_sin_inversor(settings: Settings, tmp_path) -> None:
    import yaml

    from casa_ai.app import Aplicacion
    from casa_ai.settings import Settings as S

    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"energia_ha": {"solar": "sensor.solar"}}), "utf-8")
    sin_inversor = S(
        _env_file=None,
        anthropic_api_key="x",
        db_path=tmp_path / "db.sqlite3",
        config_path=config,
        ha_token="tok",
        ha_url=HA_URL,
        sungrow_host=None,
    )

    app = Aplicacion(sin_inversor)
    assert isinstance(app.ctx.energia, EnergiaHA)


def test_se_puede_forzar_home_assistant_habiendo_inversor(
    settings: Settings, tmp_path
) -> None:
    """El caso del WiNet-S: HA ya tiene la unica conexion Modbus ocupada."""
    import yaml

    from casa_ai.app import Aplicacion

    config = tmp_path / "config2.yaml"
    config.write_text(yaml.safe_dump({"energia_ha": {"solar": "sensor.solar"}}), "utf-8")
    forzado = settings.model_copy(
        update={"energia_origen": "homeassistant", "config_path": config}
    )

    app = Aplicacion(forzado)
    assert isinstance(app.ctx.energia, EnergiaHA)


def test_el_control_de_bateria_no_se_ofrece_si_solo_se_puede_leer(
    settings: Settings, tmp_path
) -> None:
    """Ofrecer una herramienta que siempre falla es peor que no tenerla."""
    import yaml

    from casa_ai.app import Aplicacion

    config = tmp_path / "solo_lectura.yaml"
    config.write_text(yaml.safe_dump({"energia_ha": {"solar": "sensor.solar"}}), "utf-8")
    solo_lectura = settings.model_copy(
        update={"energia_origen": "homeassistant", "config_path": config}
    )

    app = Aplicacion(solo_lectura)
    nombres = {h.nombre for h in app.registro.disponibles(app.ctx)}

    assert "energia_estado" in nombres       # leer si
    assert "energia_modo_bateria" not in nombres  # controlar no


def test_con_entidades_de_control_si_se_ofrece(settings: Settings, tmp_path) -> None:
    import yaml

    from casa_ai.app import Aplicacion

    config = tmp_path / "con_control.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "energia_ha": {
                    "solar": "sensor.solar",
                    "control_comando": "select.cmd",
                    "control_potencia": "number.pot",
                }
            }
        ),
        "utf-8",
    )
    con_control = settings.model_copy(
        update={"energia_origen": "homeassistant", "config_path": config}
    )

    app = Aplicacion(con_control)
    nombres = {h.nombre for h in app.registro.disponibles(app.ctx)}

    assert "energia_modo_bateria" in nombres


def test_por_modbus_el_control_siempre_se_ofrece(settings: Settings) -> None:
    from casa_ai.app import Aplicacion

    app = Aplicacion(settings)  # el fixture trae SUNGROW_HOST
    nombres = {h.nombre for h in app.registro.disponibles(app.ctx)}
    assert "energia_modo_bateria" in nombres


@respx.mock
async def test_un_soc_no_leido_no_es_cero(settings: Settings) -> None:
    """Publicarlo como 0 hacia que la vigilancia avisara de bateria vacia cada
    media hora y el panel pintara «0 %, muy baja»."""
    energia, ha = _adaptador(settings, CONFIG_LECTURA)
    _sensor("sensor.solar", "2000")
    _sensor("sensor.bat_w", "0")
    _sensor("sensor.bat_soc", "unavailable")
    _sensor("sensor.red", "0")

    estado = await energia.estado()

    assert estado.bateria_soc is None
    r = estado.resumen()
    assert r["bateria_soc_pct"] is None
    assert "No la des por vacia" in r["aviso"]
    await ha.cerrar()


@respx.mock
async def test_sin_soc_no_se_cuenta_el_margen_de_bateria(settings: Settings) -> None:
    """Sin saber si la bateria esta llena, no se le quita el sitio a ciegas."""

    from casa_ai.settings import Inventario
    from casa_ai.store import Store
    from casa_ai.tools.dispositivos import HERRAMIENTAS

    from .dobles import contexto

    energia, ha = _adaptador(settings, CONFIG_LECTURA)
    _sensor("sensor.solar", "4000")
    _sensor("sensor.bat_w", "3000")          # cargando
    _sensor("sensor.bat_soc", "unavailable") # pero no sabemos cuanto lleva
    _sensor("sensor.red", "0")

    ctx = contexto(
        settings, Inventario(), Store(settings.db_path), ha=ha, energia=energia
    )
    herramienta = next(h for h in HERRAMIENTAS if h.nombre == "excedente_solar")
    r = await herramienta.handler(ctx)

    assert r["excedente"]["margen_de_bateria_w"] == 0
    await ha.cerrar()


@respx.mock
async def test_la_cache_evita_leer_los_sensores_dos_veces(settings: Settings) -> None:
    """Aceptaba `usar_cache` y no lo usaba, asi que el panel disparaba 12-16
    GET cada 10 s donde bastaban 6-8."""
    energia, ha = _adaptador(settings, CONFIG_LECTURA)
    rutas = [_sensor(s, "1000") for s in ("sensor.solar", "sensor.bat_w",
                                          "sensor.bat_soc", "sensor.red")]

    await energia.estado()
    await energia.estado()

    assert all(r.call_count == 1 for r in rutas)
    # Y quien necesita la lectura de verdad la pide sin cache.
    await energia.estado(usar_cache=False)
    assert all(r.call_count == 2 for r in rutas)
    await ha.cerrar()
