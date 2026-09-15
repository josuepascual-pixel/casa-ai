"""Ciclo de vida de la aplicacion: recarga de inventario y arranque robusto.

Los casos de este fichero salieron de una revision de codigo. Cada uno falla
si se deshace el arreglo.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from casa_ai.app import Aplicacion
from casa_ai.settings import Settings


def _escribir(config: Path, datos: dict) -> None:
    config.write_text(yaml.safe_dump(datos), "utf-8")


async def test_recargar_inventario_llega_a_los_adaptadores(
    settings: Settings, tmp_path: Path
) -> None:
    """Antes solo se limpiaba una cache global que nadie usaba en ejecucion:
    el agente y el panel seguian con el inventario del arranque."""
    config = tmp_path / "config.yaml"
    _escribir(config, {"bluos": [{"nombre": "Salon", "host": "10.0.0.50"}]})
    app = Aplicacion(settings.model_copy(update={"config_path": config}))

    assert [p.nombre for p in app.ctx.musica._inv.bluos] == ["Salon"]

    _escribir(config, {
        "bluos": [
            {"nombre": "Salon", "host": "10.0.0.50"},
            {"nombre": "Terraza", "host": "10.0.0.52"},
        ],
        "dispositivos": [
            {"nombre": "termo", "entidad": "switch.termo", "consumo_w": 2000}
        ],
    })
    await app.recargar_inventario()

    # El inventario nuevo tiene que estar en el adaptador, no solo en el global.
    assert [p.nombre for p in app.ctx.musica._inv.bluos] == ["Salon", "Terraza"]
    assert app.ctx.inventario.dispositivos[0].nombre == "termo"
    await app.cerrar()


async def test_la_recarga_rehace_el_prompt_del_sistema(
    settings: Settings, tmp_path: Path
) -> None:
    """El inventario de la casa va dentro del prompt: si la recarga no llega
    al contexto, el modelo sigue sin saber que existe el aparato nuevo.

    Se comprueba sobre el prompt que construiria el Agente del siguiente
    turno, que es el que se le manda al modelo de verdad.
    """
    from casa_ai.agent.prompts import construir_system

    def prompt(app: Aplicacion) -> str:
        return construir_system(app.settings, app.ctx.inventario)

    config = tmp_path / "config2.yaml"
    _escribir(config, {})
    app = Aplicacion(settings.model_copy(update={"config_path": config}))
    assert "riego jardin" not in prompt(app)

    _escribir(config, {
        "dispositivos": [
            {"nombre": "riego jardin", "entidad": "switch.riego", "consumo_w": 50}
        ]
    })
    await app.recargar_inventario()

    assert "riego jardin" in prompt(app)
    await app.cerrar()


async def test_la_recarga_tambien_rehace_la_lista_de_scripts(
    settings: Settings, tmp_path: Path
) -> None:
    config = tmp_path / "config3.yaml"
    _escribir(config, {})
    app = Aplicacion(settings.model_copy(update={"config_path": config}))
    assert app.ctx.ha._scripts == set()

    _escribir(config, {"scripts_permitidos": ["script.buenas_noches"]})
    await app.recargar_inventario()

    assert app.ctx.ha._scripts == {"script.buenas_noches"}
    await app.cerrar()


def test_una_zona_horaria_mal_escrita_no_tumba_el_arranque(
    settings: Settings, tmp_path: Path
) -> None:
    """El resto del sistema degrada a UTC; el planificador lanzaba dentro del
    lifespan y con eso no arrancaba el backend entero."""
    from casa_ai.automations.rutinas import Rutinas

    config = tmp_path / "config4.yaml"
    _escribir(config, {})
    roto = settings.model_copy(
        update={"zona_horaria": "Marte/Olympus", "config_path": config}
    )
    app = Aplicacion(roto)

    rutinas = Rutinas(app, None)  # no debe lanzar
    assert str(rutinas.scheduler.timezone) == "UTC"


# --- Confirmacion con boton: historial y auditoria --------------------------


async def _app_con_pendiente(settings: Settings, tmp_path: Path, nombre: str):
    """Aplicacion con una accion de riesgo ya propuesta y esperando el boton."""
    from casa_ai.agent.registry import Herramienta, Riesgo, esquema

    config = tmp_path / f"{nombre}.yaml"
    _escribir(config, {})
    app = Aplicacion(settings.model_copy(update={"config_path": config}))

    ejecutadas: list[str] = []

    async def peligrosa(_ctx, potencia_w: int):
        ejecutadas.append(f"forzar:{potencia_w}")
        return {"modo": "cargar", "potencia_w": potencia_w,
                "detalle": f"Bateria cargando a {potencia_w} W."}

    async def sin_detalle(_ctx, ssid: str, activar: bool):
        ejecutadas.append("wifi")
        return {"ssid": ssid, "activa": activar}

    app.registro.anadir(
        Herramienta(
            nombre="prueba_forzar", descripcion="d" * 50,
            esquema=esquema({"potencia_w": {"type": "integer"}},
                            obligatorias=["potencia_w"]),
            riesgo=Riesgo.ALTO, handler=peligrosa,
            resumen_confirmacion=lambda a: f"Forzar carga a {a['potencia_w']} W",
        ),
        Herramienta(
            nombre="prueba_wifi", descripcion="d" * 50,
            esquema=esquema({"ssid": {"type": "string"},
                             "activar": {"type": "boolean"}},
                            obligatorias=["ssid", "activar"]),
            riesgo=Riesgo.ALTO, handler=sin_detalle,
            resumen_confirmacion=lambda a: f"Apagar el wifi {a['ssid']}",
        ),
    )
    return app, ejecutadas


async def _proponer(app, herramienta: str, argumentos: dict) -> str:
    from casa_ai.agent.safety import Ejecutor

    conversacion = "telegram:555"
    ctx = app.contexto_para(
        "telegram", "555", conversacion, confirmacion="boton"
    )
    app.store.nuevo_turno(conversacion)
    await Ejecutor(app.registro, ctx).ejecutar(herramienta, argumentos)
    return app.store.pendientes_de(conversacion)[0]["token"]


async def test_confirmar_con_boton_queda_en_el_historial(
    settings: Settings, tmp_path: Path
) -> None:
    """Sin esto el hilo se quedaba con el "ACCION NO EJECUTADA" y el modelo
    luego negaba que se hubiera hecho, o volvia a proponerlo."""
    app, ejecutadas = await _app_con_pendiente(settings, tmp_path, "hist1")
    token = await _proponer(app, "prueba_forzar", {"potencia_w": 3000})

    detalle, es_error = await app.confirmar_pendiente(
        canal="telegram", usuario="555", conversacion="telegram:555", token=token
    )

    assert es_error is False
    assert ejecutadas == ["forzar:3000"]
    assert "3000 W" in detalle

    textos = " ".join(
        b["text"]
        for m in app.store.historial("telegram:555")
        for b in m["content"]
        if b.get("type") == "text"
    )
    assert "confirmo con el boton" in textos
    assert "Forzar carga a 3000 W" in textos
    await app.cerrar()


async def test_cancelar_con_boton_queda_en_el_historial_y_en_la_auditoria(
    settings: Settings, tmp_path: Path
) -> None:
    app, ejecutadas = await _app_con_pendiente(settings, tmp_path, "hist2")
    token = await _proponer(app, "prueba_forzar", {"potencia_w": 5000})

    assert app.cancelar_pendiente(
        canal="telegram", usuario="555", conversacion="telegram:555", token=token
    ) is True
    assert ejecutadas == []

    # La auditoria tiene que decir QUE se cancelo, no solo que algo se cancelo.
    registro = app.store.auditoria(5)[0]
    assert registro["herramienta"] == "prueba_forzar"
    assert "5000" in registro["argumentos"]

    textos = " ".join(
        b["text"]
        for m in app.store.historial("telegram:555")
        for b in m["content"]
        if b.get("type") == "text"
    )
    assert "cancelo con el boton" in textos
    await app.cerrar()


async def test_el_detalle_no_se_muestra_como_un_dict_de_python(
    settings: Settings, tmp_path: Path
) -> None:
    """Las herramientas que no devuelven `detalle` acababan pintando un repr."""
    app, _ = await _app_con_pendiente(settings, tmp_path, "hist3")
    token = await _proponer(app, "prueba_wifi", {"ssid": "Invitados", "activar": False})

    detalle, es_error = await app.confirmar_pendiente(
        canal="telegram", usuario="555", conversacion="telegram:555", token=token
    )

    assert es_error is False
    assert "{" not in detalle and "'" not in detalle
    assert "Invitados" in detalle
    await app.cerrar()


async def test_cancelar_algo_que_no_existe_no_audita_nada(
    settings: Settings, tmp_path: Path
) -> None:
    app, _ = await _app_con_pendiente(settings, tmp_path, "hist4")

    assert app.cancelar_pendiente(
        canal="telegram", usuario="555", conversacion="telegram:555", token="inventado"
    ) is False
    assert app.store.auditoria(5) == []
    await app.cerrar()
