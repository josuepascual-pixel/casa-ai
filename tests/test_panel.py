"""Panel web: agregacion de datos y rutas."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from casa_ai.panel.datos import mezcla_de_suministro

from .conftest import app_de_prueba

TOKEN = "token-del-panel-para-los-tests"


# --- Mezcla de suministro ---------------------------------------------------


def test_todo_del_sol() -> None:
    m = mezcla_de_suministro(
        {"consumo_casa_w": 2000, "bateria_w": 1000, "red_w": 500}
    )
    assert m == {"consumo_w": 2000, "solar_w": 2000, "bateria_w": 0, "red_w": 0}


def test_bateria_descargando_aporta_a_la_casa() -> None:
    m = mezcla_de_suministro(
        {"consumo_casa_w": 1500, "bateria_w": -900, "red_w": 0}
    )
    assert m["bateria_w"] == 900
    assert m["solar_w"] == 600
    assert m["red_w"] == 0


def test_importando_de_red_de_noche() -> None:
    """Sin sol y sin bateria, todo viene de la red."""
    m = mezcla_de_suministro({"consumo_casa_w": 800, "bateria_w": 0, "red_w": -800})
    assert m == {"consumo_w": 800, "solar_w": 0, "bateria_w": 0, "red_w": 800}


def test_los_tres_a_la_vez() -> None:
    m = mezcla_de_suministro(
        {"consumo_casa_w": 4000, "bateria_w": -1500, "red_w": -500}
    )
    assert m["bateria_w"] == 1500
    assert m["red_w"] == 500
    assert m["solar_w"] == 2000
    assert sum(m[k] for k in ("solar_w", "bateria_w", "red_w")) == m["consumo_w"]


def test_las_partes_nunca_superan_el_consumo() -> None:
    """Un desfase de lecturas no debe producir una barra que pase del 100 %."""
    m = mezcla_de_suministro(
        {"consumo_casa_w": 1000, "bateria_w": -3000, "red_w": -2000}
    )
    assert m["bateria_w"] <= m["consumo_w"]
    assert m["red_w"] <= m["consumo_w"]


def test_sin_consumo_no_hay_mezcla() -> None:
    m = mezcla_de_suministro({"consumo_casa_w": 0, "bateria_w": 0, "red_w": 3000})
    assert m["consumo_w"] == 0
    assert m["solar_w"] == 0


# --- Rutas ------------------------------------------------------------------


@pytest.fixture
def cliente(monkeypatch, tmp_path: Path):
    with app_de_prueba(monkeypatch, tmp_path, API_TOKEN=TOKEN) as c:
        yield c


def test_la_pagina_se_sirve_sin_token(cliente: TestClient) -> None:
    """No lleva ningun dato: pide el token y llama al API con el."""
    r = cliente.get("/panel")
    assert r.status_code == 200
    assert "Jarvis" in r.text
    # Y no filtra nada que no deba estar en un HTML publico.
    assert TOKEN not in r.text

    assert cliente.get("/panel.js").status_code == 200
    # La raiz es el panel: es lo que abre el ingress de Home Assistant.
    assert cliente.get("/").status_code == 200 and "Jarvis" in cliente.get("/").text


def test_los_datos_del_panel_si_exigen_token(cliente: TestClient) -> None:
    assert cliente.get("/api/panel").status_code == 401
    assert cliente.get("/api/panel/camara/puerta").status_code == 401


def test_datos_del_panel_con_token(cliente: TestClient) -> None:
    r = cliente.get("/api/panel", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    datos = r.json()
    assert "momento" in datos
    assert datos["camaras"] == []  # sin inventario configurado
    assert datos["quien"] is None  # `api` no es una persona declarada


def test_una_camara_inexistente_da_error_claro(cliente: TestClient) -> None:
    r = cliente.get(
        "/api/panel/camara/fantasma", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert r.status_code == 502


def test_el_panel_no_ejecuta_acciones() -> None:
    """El panel es un lector. Las acciones van por /chat, que pasa por la capa
    de seguridad con su confirmacion."""
    js = (Path("src/casa_ai/panel/panel.js")).read_text("utf-8")
    assert "casa_accion" not in js
    assert "ejecutar_accion_pendiente" not in js
    # Los nombres que vienen del backend son datos, no marcado: nada de
    # asignar innerHTML ni de insertAdjacentHTML (la mencion en el comentario
    # de cabecera del fichero no cuenta, de ahi que se busque el patron).
    codigo = "\n".join(
        linea for linea in js.splitlines() if not linea.strip().startswith("//")
    )
    assert "innerHTML" not in codigo
    assert "insertAdjacentHTML" not in codigo
    assert "document.write" not in codigo


def test_las_partes_suman_exactamente_el_consumo() -> None:
    """Recortando cada aporte por separado, la suma podia superar el consumo y
    la barra contradecia al indicador de la misma pantalla."""
    casos = [
        {"consumo_casa_w": 1000, "bateria_w": -3000, "red_w": -2000},
        {"consumo_casa_w": 2400, "bateria_w": -900, "red_w": -1500},
        {"consumo_casa_w": 500, "bateria_w": -400, "red_w": -400},
        {"consumo_casa_w": 3000, "bateria_w": 500, "red_w": 200},
    ]
    for caso in casos:
        m = mezcla_de_suministro(caso)
        partes = m["solar_w"] + m["bateria_w"] + m["red_w"]
        assert partes == m["consumo_w"], caso
        assert all(m[k] >= 0 for k in ("solar_w", "bateria_w", "red_w")), caso


def test_el_temporizador_del_panel_se_limpia_antes_de_armarse() -> None:
    """Dos clics en Entrar dejaban dos temporizadores refrescando a la vez.

    Armar el temporizador esta en un solo sitio a proposito: el `entrar` y el
    cambio de visibilidad de la pestana pasan los dos por ahi.
    """
    js = Path("src/casa_ai/panel/panel.js").read_text("utf-8")
    armar = js[js.index("function armarRefresco()"):]
    cuerpo = armar[: armar.index("\n}")]
    assert cuerpo.index("clearInterval") < cuerpo.index("setInterval")
    # Y nadie mas llama a setInterval por su cuenta.
    assert js.count("setInterval") == 1


def test_el_panel_no_refresca_en_segundo_plano() -> None:
    """Seis pasadas por minuto contra Home Assistant y el inversor por un panel
    que alguien dejo abierto en otra ventana."""
    js = Path("src/casa_ai/panel/panel.js").read_text("utf-8")
    assert "visibilitychange" in js
    armar = js[js.index("function armarRefresco()"):]
    assert "document.hidden" in armar[: armar.index("\n}")]


def test_el_panel_usa_rutas_relativas_para_el_ingress() -> None:
    """Por el ingress de Home Assistant la pagina vive bajo un prefijo: una
    ruta absoluta se saldria de el y el panel quedaria en blanco."""
    from pathlib import Path

    carpeta = Path(__file__).resolve().parents[1] / "src" / "casa_ai" / "panel"
    html = (carpeta / "index.html").read_text("utf-8")
    js = (carpeta / "panel.js").read_text("utf-8")
    assert 'src="panel.js"' in html and 'src="/panel.js"' not in html
    assert 'pedir("api/panel")' in js and '"/api/panel' not in js and '`/api/panel' not in js


# --- Por el ingress de Home Assistant ---------------------------------------

INGRESS = {"X-Ingress-Path": "/api/hassio_ingress/abc"}


def _config_con_personas(tmp_path: Path) -> str:
    ruta = tmp_path / "config.yaml"
    ruta.write_text(
        "camaras:\n  - nombre: Cine\n    id_protect: c1\n"
        "personas:\n"
        "  - {nombre: Papa, nivel: dueno, dispositivos: ['usuario-papa']}\n"
        "  - {nombre: Peque, nivel: nino, dispositivos: ['usuario-peque']}\n",
        "utf-8",
    )
    return str(ruta)


def test_por_el_ingress_no_hace_falta_token(monkeypatch, tmp_path: Path) -> None:
    """Home Assistant ya ha hecho el login (y su segundo factor): el
    Supervisor reenvia con X-Ingress-Path y el usuario, y con eso basta."""
    with app_de_prueba(
        monkeypatch, tmp_path, cliente_ip="172.30.32.2",
        API_TOKEN=TOKEN, API_CONFIAR_EN_INGRESS="true", CONFIG_PATH=_config_con_personas(tmp_path),
    ) as c:
        assert c.get("/api/panel").status_code == 401           # sin cabecera de ingress
        r = c.get("/api/panel", headers={**INGRESS, "X-Remote-User-Id": "papa"})
        assert r.status_code == 200
        assert [x["nombre"] for x in r.json()["camaras"]] == ["Cine"]
        assert r.json()["quien"] == "Papa"

        # La tablet del nino: mismo panel, sin camaras, y la captura ni con enganos.
        r = c.get("/api/panel", headers={**INGRESS, "X-Remote-User-Id": "peque"})
        assert r.status_code == 200 and r.json()["camaras"] == []
        r = c.get("/api/panel/camara/Cine", headers={**INGRESS, "X-Remote-User-Id": "peque"})
        assert r.status_code == 403
        # Un usuario de HA que no esta declarado es nino: falla cerrado.
        r = c.get("/api/panel", headers={**INGRESS, "X-Remote-User-Id": "invitado"})
        assert r.status_code == 200 and r.json()["camaras"] == []


def test_el_ingress_solo_se_cree_si_viene_del_supervisor(monkeypatch, tmp_path: Path) -> None:
    """Las cabeceras las puede poner cualquiera; la IP de origen, no."""
    with app_de_prueba(
        monkeypatch, tmp_path, cliente_ip="192.168.0.50",
        API_TOKEN=TOKEN, API_CONFIAR_EN_INGRESS="true",
    ) as c:
        r = c.get("/api/panel", headers={**INGRESS, "X-Remote-User-Id": "papa"})
        assert r.status_code == 401
    # Y fuera del complemento (sin la opcion), tampoco desde esa red.
    with app_de_prueba(
        monkeypatch, tmp_path, cliente_ip="172.30.32.2",
        API_TOKEN=TOKEN, API_CONFIAR_EN_INGRESS="false",
    ) as c:
        assert c.get("/api/panel", headers=INGRESS).status_code == 401


def test_el_panel_entra_solo_si_el_backend_no_pide_token() -> None:
    carpeta = Path(__file__).resolve().parents[1] / "src" / "casa_ai" / "panel"
    js = (carpeta / "panel.js").read_text("utf-8")
    assert "if (token()) cabeceras.Authorization" in js
    assert 'pedir("salud")' in js and 'pedir("/salud")' not in js
    assert 'pedir("chat"' in js and 'pedir("/chat"' not in js  # relativo: ingress
    assert "\nentrar();" in js


def test_por_el_ingress_solo_se_sirve_el_panel(monkeypatch, tmp_path: Path) -> None:
    """El Supervisor reenvia por el ingress CUALQUIER ruta a cualquier sesion
    de Home Assistant. Desde la tablet del nino, /chat entraria como dueno y
    /voz como el aparato que el quisiera nombrar."""
    cabeceras = {**INGRESS, "X-Remote-User-Id": "peque"}
    with app_de_prueba(
        monkeypatch, tmp_path, cliente_ip="172.30.32.2",
        API_TOKEN=TOKEN, API_CONFIAR_EN_INGRESS="true", CONFIG_PATH=_config_con_personas(tmp_path),
    ) as c:
        assert c.get("/api/panel", headers=cabeceras).status_code == 200
        r = c.post("/voz", json={"dispositivo": "usuario-papa", "texto": "abre"},
                   headers=cabeceras)
        assert r.status_code == 403
        for ruta in ("/auditoria", "/verificar", "/salud", "/avisos-seguridad"):
            assert c.get(ruta, headers=cabeceras).status_code == 403, ruta
        for ruta in ("/recargar-inventario", "/descubrir", "/planta/sondear"):
            assert c.post(ruta, headers=cabeceras).status_code == 403, ruta
        # Con el token, como siempre.
        con_token = {"Authorization": f"Bearer {TOKEN}"}
        assert c.get("/auditoria", headers=con_token).status_code == 200


def test_otro_complemento_no_puede_forjar_el_ingress(monkeypatch, tmp_path: Path) -> None:
    """La red interna 172.30.32.0/23 la comparten todos los complementos; solo
    el Supervisor (172.30.32.2) reenvia el ingress."""
    with app_de_prueba(
        monkeypatch, tmp_path, cliente_ip="172.30.33.7",
        API_TOKEN=TOKEN, API_CONFIAR_EN_INGRESS="true",
    ) as c:
        r = c.get("/api/panel", headers={**INGRESS, "X-Remote-User-Id": "papa"})
        assert r.status_code == 401


def test_uvicorn_no_se_cree_x_forwarded_for(monkeypatch) -> None:
    """Con proxy_headers (el defecto) uvicorn reescribe la IP de origen con lo
    que diga X-Forwarded-For si la conexion viene de localhost: cualquier
    proceso del equipo se haria pasar por el Supervisor."""
    import uvicorn

    from casa_ai import main
    from casa_ai.settings import get_settings

    llamadas: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: llamadas.append(kw))
    monkeypatch.setenv("API_TOKEN", TOKEN)
    get_settings.cache_clear()
    try:
        main.run()
    finally:
        get_settings.cache_clear()
    assert llamadas[0]["proxy_headers"] is False


def test_el_chat_del_panel_habla_como_el_usuario_de_home_assistant(
    monkeypatch, tmp_path: Path
) -> None:
    """Por el ingress /chat no es `api` (dueno): es quien hizo login en HA,
    con su nivel, y su hilo es suyo aunque el cuerpo diga otro."""
    from casa_ai.app import Aplicacion

    turnos: list[dict] = []

    async def responder_falso(self, **kwargs):
        turnos.append(kwargs)
        return "ok"

    monkeypatch.setattr(Aplicacion, "responder", responder_falso)
    with app_de_prueba(
        monkeypatch, tmp_path, cliente_ip="172.30.32.2",
        API_TOKEN=TOKEN, API_CONFIAR_EN_INGRESS="true", ANTHROPIC_API_KEY="sk-test",
        CONFIG_PATH=_config_con_personas(tmp_path),
    ) as c:
        r = c.post("/chat", json={"mensaje": "hola", "hilo": "telegram:555"},
                   headers={**INGRESS, "X-Remote-User-Id": "peque"})
        assert r.status_code == 200
        assert turnos[-1]["canal"] == "panel" and turnos[-1]["usuario"] == "usuario-peque"
        assert turnos[-1]["conversacion"] == "panel:usuario-peque"

        r = c.post("/chat", json={"mensaje": "hola"},
                   headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200
        assert turnos[-1]["canal"] == "http" and turnos[-1]["conversacion"] == "http:default"


# --- La casa segun Home Assistant --------------------------------------------


def _estados_de_una_casa() -> list[dict]:
    from .dobles import entidad

    return [
        entidad("light.salon", "on", friendly_name="Salón"),
        entidad("light.cocina", "off", friendly_name="Cocina"),
        entidad("cover.persiana_suite", "open", friendly_name="Persiana suite",
                current_position=60),
        entidad("cover.garaje", "closed", friendly_name="Garaje", device_class="garage"),
        entidad("lock.puerta_principal", "unlocked", friendly_name="Puerta principal"),
        entidad("binary_sensor.ventana_lavadero", "on", device_class="window",
                friendly_name="Ventana lavadero"),
        entidad("climate.salon", "heat", friendly_name="Clima salón", current_temperature=21.5,
                temperature=22),
        entidad("person.josue", "home", friendly_name="Josué"),
        entidad("person.ana", "not_home", friendly_name="Ana"),
        entidad("sensor.wallbox_potencia", "7.2", friendly_name="Potencia",
                unit_of_measurement="kW"),
        entidad("sensor.ecowater_sal", "34", unit_of_measurement="%"),
        entidad("alarm_control_panel.casa", "armed_home", friendly_name="Seguridad"),
    ]


async def test_el_panel_ensena_la_casa_que_hay_en_home_assistant(settings, store) -> None:
    from casa_ai.panel.datos import _casa
    from casa_ai.settings import Inventario

    from .dobles import adaptador, contexto

    async def estados(_self):
        return _estados_de_una_casa()

    inv = Inventario.model_validate({
        "alias_entidades": {"sal": "sensor.ecowater_sal"},
        "panel": [
            {"titulo": "Coche", "entidades": {"Cargando": "sensor.wallbox_potencia",
                                              "Batería": "sensor.no_existe"}},
            {"titulo": "Agua", "entidades": {"Sal": "sal"}},
        ],
    })
    ctx = contexto(settings, inv, store, ha=adaptador(True, estados=estados))
    casa = await _casa(ctx)

    assert casa["luces"] == {"total": 2, "encendidas": ["Salón"]}
    assert casa["persianas"] == [{"nombre": "Persiana suite", "estado": "abierta", "posicion": 60}]
    assert casa["accesos"] == [
        {"nombre": "Garaje", "estado": "cerrada", "abierto": False},
        {"nombre": "Puerta principal", "estado": "abierta", "abierto": True},
        {"nombre": "Ventana lavadero", "estado": "abierta", "abierto": True, "ventana": True},
    ]
    assert casa["clima"] == [{"nombre": "Clima salón", "actual": 21.5, "objetivo": 22,
                              "modo": "calor"}]
    assert casa["presencia"] == [{"nombre": "Josué", "en_casa": True},
                                 {"nombre": "Ana", "en_casa": False}]
    assert casa["alarma"] == [
        {"nombre": "Seguridad", "estado": "armada en casa", "armada": True, "saltando": False}
    ]
    assert casa["sistemas"] == [
        {"titulo": "Coche", "lineas": [{"nombre": "Cargando", "valor": "7.2 kW"},
                                       {"nombre": "Batería", "valor": "sin dato"}]},
        {"titulo": "Agua", "lineas": [{"nombre": "Sal", "valor": "34 %"}]},
    ]


async def test_a_un_nino_el_panel_no_le_dice_quien_esta_en_casa(settings, store) -> None:
    from casa_ai.panel.datos import _casa
    from casa_ai.settings import Inventario

    from .dobles import adaptador, contexto

    async def estados(_self):
        return _estados_de_una_casa()

    inv = Inventario.model_validate(
        {"personas": [{"nombre": "Leo", "nivel": "nino", "dispositivos": ["tablet"]}]}
    )
    ctx = contexto(settings, inv, store, ha=adaptador(True, estados=estados),
                   persona=inv.persona_de("voz", "tablet"))
    casa = await _casa(ctx)
    assert "presencia" not in casa
    assert casa["luces"]["total"] == 2


async def test_el_plano_reparte_las_entidades_por_estancia(settings, store) -> None:
    """La estancia se deduce del nombre; entre «suite» y «bano suite» gana la
    mas larga; los alias y las entidades fijas mandan sobre el nombre."""
    from casa_ai.panel.datos import _habitaciones, plano_de
    from casa_ai.settings import Inventario

    from .dobles import adaptador, contexto, entidad

    inv = Inventario.model_validate({
        "camaras": [{"nombre": "Cine", "zona": "cine"}],
        "plano": [
            {"zona": "salon", "x": 0, "y": 0, "ancho": 3, "alto": 2},
            {"zona": "suite", "x": 3, "y": 0},
            {"zona": "bano suite", "x": 5, "y": 0},
            {"zona": "dormitorio leo", "alias": ["eros"]},
            {"zona": "cine", "entidades": ["light.proyector"]},
            {"zona": "piscina", "exterior": True},
        ],
    })
    estados = [
        entidad("light.salon_techo", "on", friendly_name="Techo salón"),
        entidad("light.salon_lampara", "off"),
        entidad("climate.salon", "cool", current_temperature=24.5, temperature=23),
        entidad("cover.persiana_suite", "open", current_position=40),
        entidad("light.bano_suite", "on", friendly_name="Baño suite"),
        entidad("light.dormitorio_eros", "on"),
        entidad("light.proyector", "on", friendly_name="Proyector"),
        entidad("sensor.temperatura_piscina", "27.5", device_class="temperature",
                unit_of_measurement="°C"),
        entidad("lock.puerta_principal", "unlocked"),
        entidad("binary_sensor.ventana_salon", "on", device_class="window",
                friendly_name="Ventana salón"),
        entidad("binary_sensor.ventanal_suite", "off", device_class="window"),
        entidad("cover.ventana_izquierda_salon", "open", friendly_name="Ventana izquierda salón",
                current_position=100),
    ]
    ctx = contexto(settings, inv, store, ha=adaptador(True))
    musica = [{"reproductor": "Salon", "zona": "salon", "estado": "play",
               "titulo": "So What", "artista": "Miles Davis"}]
    por_zona = {h["zona"]: h for h in _habitaciones(ctx, estados, musica)}

    salon = por_zona["salon"]
    assert salon["luces"] == 2 and salon["luces_encendidas"] == 1
    assert salon["temperatura"] == 24.5 and salon["clima"] == "frio"
    assert salon["musica"] == "So What — Miles Davis"
    # Una ventana motorizada (cover) abierta cuenta como ventana, no como persiana.
    assert salon["ventanas"] == 2
    assert salon["ventanas_abiertas"] == ["Ventana salón", "Ventana izquierda salón"]
    assert salon["persianas"] == 0
    assert por_zona["suite"]["ventanas_abiertas"] == []
    assert {(e["nombre"], e["dominio"]) for e in salon["entidades"]} == {
        ("Techo salón", "light"), ("light.salon_lampara", "light"), ("climate.salon", "climate"),
        ("Ventana salón", "binary_sensor"), ("Ventana izquierda salón", "cover"),
    }
    assert por_zona["suite"]["persianas_abiertas"] == 1
    assert por_zona["bano suite"]["luces_encendidas"] == 1  # no se la lleva «suite»
    assert por_zona["dormitorio leo"]["luces_encendidas"] == 1  # por el alias
    assert por_zona["cine"]["luces_encendidas"] == 1 and por_zona["cine"]["camara"]
    assert por_zona["piscina"]["temperatura"] == 27.5 and por_zona["piscina"]["exterior"]
    # La cerradura no es de ninguna estancia: no se pierde nada, pero no se inventa.
    assert not any("puerta" in e["nombre"] for h in por_zona.values() for e in h["entidades"])

    plano = plano_de(ctx)
    assert plano[0] == {"zona": "salon", "planta": "", "x": 0, "y": 0, "ancho": 3, "alto": 2,
                        "exterior": False}
    # Sin plano, las zonas se colocan solas.
    ctx.inventario = Inventario.model_validate({"zonas": ["a", "b", "c", "d", "e"]})
    auto = plano_de(ctx)
    assert [p["zona"] for p in auto] == ["a", "b", "c", "d", "e"] and auto[4]["y"] == 2
