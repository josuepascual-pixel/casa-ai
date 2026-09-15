"""El complemento de Home Assistant no puede desviarse de Settings.

`run.sh` exporta cada opcion del formulario en mayusculas como variable de
entorno, sin tabla intermedia. Eso solo funciona si las claves de config.yaml
son exactamente campos de Settings; una errata ahi es una opcion que el usuario
rellena y nadie lee.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

from casa_ai.settings import Settings

RAIZ = Path(__file__).resolve().parents[1]
ADDON = RAIZ / "addon" / "casa_ai"


def _config() -> dict:
    return yaml.safe_load((ADDON / "config.yaml").read_text("utf-8"))


def test_cada_opcion_es_un_campo_de_settings() -> None:
    campos = set(Settings.model_fields)
    opciones = set(_config()["options"])
    assert opciones <= campos, f"opciones que Settings no conoce: {opciones - campos}"


def test_lo_que_resuelve_el_complemento_no_esta_en_el_formulario() -> None:
    """run.sh los fija: si aparecieran en el formulario, el usuario podria
    pisarlos y romper el acceso a Home Assistant o la persistencia."""
    opciones = set(_config()["options"])
    fijados = {"ha_url", "ha_token", "db_path", "config_path", "api_host", "knx_route_back"}
    assert not (opciones & fijados)
    run = (ADDON / "run.sh").read_text("utf-8")
    for clave in fijados:
        assert f"export {clave.upper()}=" in run, f"run.sh no fija {clave}"


def test_el_esquema_cubre_todas_las_opciones() -> None:
    cfg = _config()
    assert set(cfg["options"]) == set(cfg["schema"])


def test_las_traducciones_cubren_todas_las_opciones() -> None:
    traducciones = yaml.safe_load(
        (ADDON / "translations" / "es.yaml").read_text("utf-8")
    )["configuration"]
    assert set(_config()["options"]) == set(traducciones)


def test_la_version_del_complemento_es_la_del_paquete() -> None:
    """El Dockerfile instala la etiqueta git `v<version>`: si divergen, el
    complemento instalaria otro codigo del que dice."""
    pyproject = tomllib.loads((RAIZ / "pyproject.toml").read_text("utf-8"))
    assert _config()["version"] == pyproject["project"]["version"]
    dockerfile = (ADDON / "Dockerfile").read_text("utf-8")
    assert re.search(r"@v\$\{BUILD_VERSION\}", dockerfile)


def test_los_secretos_van_como_password() -> None:
    """Para que el formulario de HA los oculte al escribirlos."""
    esquema = _config()["schema"]
    for clave in ("anthropic_api_key", "telegram_token", "api_token", "unifi_password",
                  "whatsapp_token", "whatsapp_app_secret", "elevenlabs_api_key"):
        assert esquema[clave].startswith("password"), clave


def test_el_ejemplo_de_inventario_viaja_en_el_paquete() -> None:
    """run.sh lo copia a /config la primera vez; si no esta en la rueda, el
    complemento arranca sin inventario y sin decir por que."""
    pyproject = tomllib.loads((RAIZ / "pyproject.toml").read_text("utf-8"))
    incluidos = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert incluidos["config/config.example.yaml"] == "casa_ai/config.example.yaml"
    assert (RAIZ / "config" / "config.example.yaml").exists()
