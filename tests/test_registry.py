"""Registro de herramientas: esquemas validos y filtrado por configuracion."""

from __future__ import annotations

from casa_ai.agent.registry import Contexto, Cualquiera, Riesgo, Todos, esquema
from casa_ai.agent.safety import TOOL_CONFIRMAR
from casa_ai.settings import Inventario, Settings
from casa_ai.store import Store
from casa_ai.tools import construir_registro

from .dobles import contexto


def _ctx(
    settings: Settings,
    store: Store,
    inventario: Inventario,
    **activos: bool,
) -> Contexto:
    return contexto(settings, inventario, store, por_defecto=True, **activos)


def test_esquema_cumple_los_requisitos_de_strict() -> None:
    e = esquema(
        {"obligatorio": {"type": "string"}, "opcional": {"type": "integer"}},
        obligatorias=["obligatorio"],
    )
    # strict:true exige additionalProperties false y required con todo dentro.
    assert e["additionalProperties"] is False
    assert set(e["required"]) == {"obligatorio", "opcional"}
    # Las opcionales admiten null; las obligatorias mantienen su tipo simple.
    assert e["properties"]["opcional"]["type"] == ["integer", "null"]
    assert e["properties"]["obligatorio"]["type"] == "string"


def test_todas_las_herramientas_tienen_definicion_valida() -> None:
    registro = construir_registro()
    assert len(registro.herramientas) >= 15

    for nombre, h in registro.herramientas.items():
        definicion = h.definicion_api()
        assert definicion["name"] == nombre
        assert definicion["strict"] is True
        assert len(h.descripcion) > 40, f"{nombre} necesita mejor descripcion"
        assert definicion["input_schema"]["additionalProperties"] is False
        assert set(definicion["input_schema"]["required"]) == set(
            definicion["input_schema"]["properties"]
        )


def test_toda_herramienta_de_riesgo_alto_explica_que_va_a_hacer() -> None:
    """Sin resumen no se puede pedir una confirmacion util al usuario."""
    registro = construir_registro()
    for nombre, h in registro.herramientas.items():
        if h.riesgo is Riesgo.ALTO:
            assert h.resumen_confirmacion is not None, f"{nombre} sin resumen_confirmacion"


def test_las_acciones_peligrosas_estan_clasificadas_como_tal() -> None:
    registro = construir_registro()
    for nombre in (
        "energia_modo_bateria",
        "knx_escribir",
        "red_wifi_activar",
        "red_bloquear_cliente",
        "red_reiniciar_dispositivo",
    ):
        herramienta = registro.get(nombre)
        assert herramienta is not None, f"falta {nombre}"
        assert herramienta.riesgo is Riesgo.ALTO, f"{nombre} deberia ser riesgo ALTO"


def test_las_lecturas_no_piden_confirmacion() -> None:
    registro = construir_registro()
    for nombre in ("energia_estado", "casa_estado", "camara_ver", "red_clientes"):
        herramienta = registro.get(nombre)
        assert herramienta is not None
        assert herramienta.riesgo is Riesgo.LECTURA


def test_orden_estable_para_no_romper_la_cache_de_prompt(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    registro = construir_registro()
    ctx = _ctx(settings, store, inventario)
    primera = [d["name"] for d in registro.definiciones_api(ctx)]
    segunda = [d["name"] for d in registro.definiciones_api(ctx)]

    assert primera == segunda
    assert primera == sorted(primera)


def test_lo_no_configurado_no_se_ofrece_al_modelo(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    registro = construir_registro()
    ctx = _ctx(settings, store, inventario, energia=False, unifi=False, knx=False)
    nombres = {h.nombre for h in registro.disponibles(ctx)}

    assert "energia_estado" not in nombres
    assert "knx_escribir" not in nombres
    # Las camaras valen con UniFi O con Home Assistant: aqui HA sigue activo,
    # asi que la vision se mantiene (es el caso del backend en la nube).
    assert "camara_ver" in nombres
    # Los eventos, en cambio, solo existen con UniFi Protect directo.
    assert "camaras_eventos" not in nombres
    # Las de casa y musica siguen, y las de sistema no dependen de nada.
    assert "casa_accion" in nombres
    assert "musica_control" in nombres
    assert "informe_casa" in nombres
    assert TOOL_CONFIRMAR in nombres


def test_las_camaras_desaparecen_sin_unifi_ni_ha(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    registro = construir_registro()
    ctx = _ctx(settings, store, inventario, unifi=False, ha=False)
    nombres = {h.nombre for h in registro.disponibles(ctx)}

    assert "camara_ver" not in nombres
    assert "camaras_listar" not in nombres


def test_el_combinador_de_requisitos_es_explicito(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    """Dos requisitos con la misma forma significaban cosas contrarias.

    `("unifi", "ha")` en camaras era "cualquiera" y en excedente era "los dos",
    y solo se distinguian por si habia un `disponible_si` mas abajo en el mismo
    constructor que repetia los nombres.
    """
    registro = construir_registro()

    # El excedente necesita ver el inversor Y los consumos a la vez.
    excedente = registro.get("excedente_solar")
    assert excedente is not None
    assert isinstance(excedente.requiere, Todos)

    # Y se comporta como dice. Todo apagado salvo lo que se nombre.
    def nombres(**activos: bool) -> set[str]:
        ctx = contexto(settings, inventario, store, por_defecto=False, **activos)
        return {h.nombre for h in registro.disponibles(ctx)}

    assert "excedente_solar" in nombres(energia=True, ha=True)
    assert "excedente_solar" not in nombres(energia=True)
    assert "excedente_solar" not in nombres(ha=True)

    # Una cadena suelta significa "cualquiera", y con un solo nombre las dos
    # formas coinciden. Con varios, no: es lo que antes no se podia distinguir.
    ctx_solo_ha = contexto(settings, inventario, store, ha=True)
    adaptadores = {"ha": ctx_solo_ha.ha, "energia": ctx_solo_ha.energia}
    assert Cualquiera("energia", "ha").cumple(adaptadores) is True
    assert Todos("energia", "ha").cumple(adaptadores) is False


def test_las_ausentes_dicen_por_que_faltan(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    """La pregunta de quien monta la casa es por que no puede pedir algo."""
    registro = construir_registro()
    ausentes = registro.ausentes(contexto(settings, inventario, store))

    assert "unifi" in ausentes["red_estado"]
    assert "energia" in ausentes["excedente_solar"]
    assert "ha" in ausentes["excedente_solar"]
    # Lo que si esta disponible no aparece.
    assert "informe_casa" not in ausentes


def test_no_hay_nombres_duplicados() -> None:
    registro = construir_registro()
    assert len(registro.herramientas) == len({h.nombre for h in registro.herramientas.values()})


def test_construir_registro_dos_veces_no_choca() -> None:
    construir_registro()
    construir_registro()  # no debe lanzar por duplicados
