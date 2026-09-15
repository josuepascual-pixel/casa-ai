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
    assert "Casa" in r.text
    # Y no filtra nada que no deba estar en un HTML publico.
    assert TOKEN not in r.text

    assert cliente.get("/panel.js").status_code == 200
    # La raiz es el panel: es lo que abre el ingress de Home Assistant.
    assert cliente.get("/").status_code == 200 and "Casa" in cliente.get("/").text


def test_los_datos_del_panel_si_exigen_token(cliente: TestClient) -> None:
    assert cliente.get("/api/panel").status_code == 401
    assert cliente.get("/api/panel/camara/puerta").status_code == 401


def test_datos_del_panel_con_token(cliente: TestClient) -> None:
    r = cliente.get("/api/panel", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    datos = r.json()
    assert "momento" in datos
    assert datos["camaras"] == []  # sin inventario configurado


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
    assert "\nentrar();" in js
