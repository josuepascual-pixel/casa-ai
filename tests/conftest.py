from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from casa_ai.settings import Inventario, Settings  # noqa: E402
from casa_ai.store import Store  # noqa: E402

# La instancia de Home Assistant de los tests. Vive aqui porque la fixture
# `settings` es la que la declara; tenerla suelta en cada fichero permitia que
# una se quedara atras.
HA_URL = "http://ha.test:8123"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        anthropic_api_key="test",
        db_path=tmp_path / "test.sqlite3",
        config_path=tmp_path / "no-existe.yaml",
        sungrow_host="10.0.0.5",
        sungrow_max_potencia_w=5000,
        ha_token="tok",
        ha_url=HA_URL,
        unifi_host="10.0.0.1",
        unifi_usuario="u",
        unifi_password="p",
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "store.sqlite3")


@pytest.fixture
def inventario() -> Inventario:
    return Inventario.model_validate(
        {
            "zonas": ["salon", "cocina"],
            "bluos": [
                {"nombre": "Salon", "host": "10.0.0.50", "zona": "salon"},
                {"nombre": "Cocina", "host": "10.0.0.51", "zona": "cocina"},
            ],
            "camaras": [{"nombre": "Puerta", "id_protect": "cam1", "zona": "entrada"}],
            "knx": [
                {"nombre": "riego", "direccion": "2/1/10", "tipo_valor": "binary"},
                {
                    "nombre": "temp exterior",
                    "direccion": "3/2/1",
                    "tipo_valor": "temperature",
                    "solo_lectura": True,
                },
            ],
            "alias_entidades": {"luz salon": "light.salon"},
        }
    )


@contextmanager
def app_de_prueba(
    monkeypatch, tmp_path: Path, cliente_ip: str | None = None, **entorno: str
) -> Iterator:
    """La app real con un entorno controlado, para los tests de rutas HTTP.

    Las dos `cache_clear()` no son adorno: `get_settings` y `get_inventario`
    cachean por proceso, asi que sin limpiarlas antes y despues un test
    heredaria la configuracion del anterior.
    """
    from fastapi.testclient import TestClient

    from casa_ai.settings import get_inventario, get_settings

    base = {
        "DB_PATH": str(tmp_path / "db.sqlite3"),
        "CONFIG_PATH": str(tmp_path / "no-existe.yaml"),
        "TELEGRAM_TOKEN": "",
    }
    for clave, valor in {**base, **entorno}.items():
        monkeypatch.setenv(clave, valor)
    get_settings.cache_clear()
    get_inventario.cache_clear()

    from casa_ai.main import crear_app

    try:
        extra = {"client": (cliente_ip, 40000)} if cliente_ip else {}
        with TestClient(crear_app(), **extra) as cliente:
            yield cliente
    finally:
        get_settings.cache_clear()
        get_inventario.cache_clear()
