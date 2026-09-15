"""Autenticacion de los canales de entrada.

Estos tests cubren los agujeros que encontro la revision de seguridad, y
existen para que no vuelvan: cada uno falla si se quita el control.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from casa_ai.settings import Settings

from .conftest import app_de_prueba

TOKEN = "un-token-de-api-suficientemente-largo"
SECRETO_WA = "secreto-de-la-app-de-meta"


@pytest.fixture
def cliente(monkeypatch, tmp_path: Path):
    """App real, con token de API y WhatsApp configurados.

    El agente se sustituye por un doble: estos tests prueban quien puede
    entrar, no lo que responde el modelo, y no deben salir a la red.
    """
    from casa_ai.app import Aplicacion

    turnos: list[dict[str, Any]] = []

    async def responder_falso(self: Any, **kwargs: Any) -> str:
        turnos.append(kwargs)
        return "ok"

    monkeypatch.setattr(Aplicacion, "responder", responder_falso)

    # Tampoco se sale a la Cloud API de Meta a entregar la respuesta.
    from casa_ai.channels.whatsapp import CanalWhatsApp

    enviados: list[tuple[str, str]] = []

    async def enviar_falso(self: Any, numero: str, texto: str) -> None:
        enviados.append((numero, texto))

    monkeypatch.setattr(CanalWhatsApp, "_enviar", enviar_falso)

    with app_de_prueba(
        monkeypatch, tmp_path,
        API_TOKEN=TOKEN,
        ANTHROPIC_API_KEY="sk-test",
        WHATSAPP_TOKEN="tok",
        WHATSAPP_PHONE_NUMBER_ID="123",
        WHATSAPP_APP_SECRET=SECRETO_WA,
        WHATSAPP_VERIFY_TOKEN="verifica",
        WHATSAPP_NUMEROS_AUTORIZADOS="34600111222",
    ) as c:
        c.turnos = turnos  # type: ignore[attr-defined]
        c.enviados = enviados  # type: ignore[attr-defined]
        yield c


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


# --- Canal HTTP -------------------------------------------------------------


def test_sin_token_no_se_puede_hacer_nada(cliente: TestClient) -> None:
    """Antes /chat daba el control de la casa a cualquiera que alcanzara el puerto."""
    for metodo, ruta in (
        ("get", "/salud"),
        ("get", "/auditoria"),
        ("get", "/verificar"),
        ("post", "/descubrir"),
        ("post", "/planta/sondear"),
        ("post", "/recargar-inventario"),
    ):
        r = getattr(cliente, metodo)(ruta)
        assert r.status_code == 401, ruta

    r = cliente.post("/chat", json={"mensaje": "apaga el wifi"})
    assert r.status_code == 401


def test_token_incorrecto_se_rechaza(cliente: TestClient) -> None:
    r = cliente.get("/salud", headers={"Authorization": "Bearer otra-cosa"})
    assert r.status_code == 401


def test_con_token_correcto_funciona(cliente: TestClient) -> None:
    r = cliente.get("/salud", headers=_auth())
    assert r.status_code == 200
    assert r.json()["estado"] == "ok"


def test_el_llamante_no_puede_elegir_su_identidad(cliente: TestClient) -> None:
    """El agujero de fondo: si `usuario` viniera en el cuerpo, un llamante
    podria hacerse pasar por un chat de Telegram y consumir sus acciones
    pendientes de confirmacion."""
    r = cliente.post(
        "/chat",
        headers=_auth(),
        json={
            "mensaje": "hola",
            "usuario": "telegram-de-la-victima",
            "conversacion": "telegram:123",
        },
    )
    assert r.status_code == 200

    turno = cliente.turnos[-1]  # type: ignore[attr-defined]
    assert turno["canal"] == "http"
    assert turno["usuario"] == "api"                 # no lo que pidio el cuerpo
    assert turno["conversacion"] == "http:default"   # ni la conversacion

    from casa_ai.main import PeticionChat

    assert "usuario" not in PeticionChat.model_fields
    assert "conversacion" not in PeticionChat.model_fields


def test_el_hilo_siempre_queda_bajo_el_prefijo_http(cliente: TestClient) -> None:
    """Sin el prefijo, el canal HTTP leeria el historial de un chat de Telegram."""
    cliente.post(
        "/chat", headers=_auth(), json={"mensaje": "hola", "hilo": "telegram:123"}
    )
    turno = cliente.turnos[-1]  # type: ignore[attr-defined]
    assert turno["conversacion"] == "http:telegram:123"
    assert turno["conversacion"].startswith("http:")


def test_voz_exige_token_y_va_bajo_su_prefijo(cliente: TestClient) -> None:
    """El satelite de voz habla por Home Assistant; el aparato da la persona,
    y la conversacion no puede ser la de un chat."""
    assert cliente.post("/voz", json={"dispositivo": "leo", "texto": "hola"}).status_code == 401

    r = cliente.post(
        "/voz", headers=_auth(), json={"dispositivo": "satelite-leo", "texto": "luz"}
    )
    assert r.status_code == 200
    turno = cliente.turnos[-1]  # type: ignore[attr-defined]
    assert turno["canal"] == "voz" and turno["usuario"] == "satelite-leo"
    assert turno["conversacion"] == "voz:satelite-leo"

    # Un identificador con cualquier cosa dentro no entra: es una clave de
    # conversacion y de persona, no texto libre.
    r = cliente.post(
        "/voz", headers=_auth(), json={"dispositivo": "telegram:123 x", "texto": "luz"}
    )
    assert r.status_code == 422


def test_sin_api_token_el_api_no_se_sirve(monkeypatch, tmp_path: Path) -> None:
    """Falla cerrado: mejor 503 que un API abierto."""
    monkeypatch.delenv("API_TOKEN", raising=False)

    with app_de_prueba(monkeypatch, tmp_path) as c:
        assert c.get("/salud").status_code == 503


# --- Webhook de WhatsApp ----------------------------------------------------


def _firma(cuerpo: bytes, secreto: str = SECRETO_WA) -> str:
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def _sobre(numero: str = "34600111222", texto: str = "apaga el wifi") -> bytes:
    return json.dumps(
        {
            "entry": [
                {"changes": [{"value": {"messages": [
                    {"from": numero, "type": "text", "text": {"body": texto}}
                ]}}]}
            ]
        }
    ).encode()


def test_webhook_sin_firma_se_rechaza(cliente: TestClient) -> None:
    """El agujero grave: el numero del remitente viaja en el cuerpo, asi que
    sin verificar la firma cualquiera podia hacerse pasar por el dueno."""
    r = cliente.post("/webhooks/whatsapp", content=_sobre())
    assert r.status_code == 401


def test_webhook_con_firma_de_otro_secreto_se_rechaza(cliente: TestClient) -> None:
    cuerpo = _sobre()
    r = cliente.post(
        "/webhooks/whatsapp",
        content=cuerpo,
        headers={"X-Hub-Signature-256": _firma(cuerpo, "secreto-del-atacante")},
    )
    assert r.status_code == 401


def test_webhook_con_cuerpo_manipulado_se_rechaza(cliente: TestClient) -> None:
    """Firma valida para un cuerpo, pero se envia otro."""
    firmado = _sobre(texto="pon musica")
    r = cliente.post(
        "/webhooks/whatsapp",
        content=_sobre(texto="abre la puerta y apaga las camaras"),
        headers={"X-Hub-Signature-256": _firma(firmado)},
    )
    assert r.status_code == 401


def test_webhook_con_firma_valida_se_acepta(cliente: TestClient) -> None:
    cuerpo = _sobre()
    r = cliente.post(
        "/webhooks/whatsapp",
        content=cuerpo,
        headers={"X-Hub-Signature-256": _firma(cuerpo)},
    )
    assert r.status_code == 200

    turno = cliente.turnos[-1]  # type: ignore[attr-defined]
    assert turno["canal"] == "whatsapp"
    assert turno["usuario"] == "34600111222"


def test_numero_no_autorizado_se_descarta_aun_con_firma_valida(
    cliente: TestClient,
) -> None:
    """Segunda capa: la firma prueba que viene de Meta, no quien escribe."""
    cuerpo = _sobre(numero="34999999999")
    r = cliente.post(
        "/webhooks/whatsapp",
        content=cuerpo,
        headers={"X-Hub-Signature-256": _firma(cuerpo)},
    )
    assert r.status_code == 200  # se acepta la entrega de Meta...
    # ...pero no se procesa ningun turno: el numero no esta autorizado.
    assert not any(
        t.get("usuario") == "34999999999"
        for t in cliente.turnos  # type: ignore[attr-defined]
    )


def test_handshake_de_verificacion(cliente: TestClient) -> None:
    r = cliente.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "verifica",
                "hub.challenge": "12345"},
    )
    assert r.status_code == 200
    assert r.text == "12345"

    r = cliente.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "mal", "hub.challenge": "x"},
    )
    assert r.status_code == 403


# --- Avisos de configuracion insegura ---------------------------------------


def test_avisos_de_seguridad(settings: Settings) -> None:
    inseguro = settings.model_copy(
        update={
            "unifi_verificar_tls": False,
            "ha_url": "http://homeassistant.local:8123",
            "api_host": "0.0.0.0",  # noqa: S104
            "api_token": None,
            "exigir_confirmacion": False,
        }
    )
    avisos = " ".join(inseguro.avisos_de_seguridad())

    assert "UNIFI_VERIFICAR_TLS" in avisos
    assert "http sin cifrar" in avisos
    assert "API_TOKEN" in avisos
    assert "EXIGIR_CONFIRMACION" in avisos


def test_configuracion_correcta_no_avisa(settings: Settings) -> None:
    seguro = settings.model_copy(
        update={
            "unifi_verificar_tls": True,
            "ha_url": "https://ha.casa.local",
            "api_host": "127.0.0.1",
            "api_token": TOKEN,
        }
    )
    assert seguro.avisos_de_seguridad() == []


def test_el_ca_bundle_cuenta_como_verificacion(settings: Settings) -> None:
    con_ca: Any = settings.model_copy(
        update={"unifi_verificar_tls": False, "unifi_ca_bundle": "/etc/unifi.pem"}
    )
    assert con_ca.verificacion_tls_unifi == "/etc/unifi.pem"
    assert not any("UNIFI" in a for a in con_ca.avisos_de_seguridad())


# --- Comparaciones en tiempo constante con entradas raras -------------------


def test_un_token_con_caracteres_no_ascii_da_401_no_500(cliente: TestClient) -> None:
    """`secrets.compare_digest` sobre `str` lanza TypeError en cuanto llega un
    caracter no ASCII, y eso convertia un token invalido en un error 500.

    Las cabeceras HTTP van en latin-1 por el cable, asi que el byte llega y
    Starlette lo decodifica a un `str` no ASCII. Se envia como bytes porque
    httpx se niega a construir la peticion con un `str` no ASCII.
    """
    r = cliente.get("/salud", headers={"Authorization": "Bearer ñoño".encode("latin-1")})
    assert r.status_code == 401


def test_un_verify_token_no_ascii_da_403_no_500(cliente: TestClient) -> None:
    r = cliente.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "ñ", "hub.challenge": "x"},
    )
    assert r.status_code == 403


def test_una_firma_no_ascii_da_401_no_500(cliente: TestClient) -> None:
    r = cliente.post(
        "/webhooks/whatsapp",
        content=_sobre(),
        headers={"X-Hub-Signature-256": "sha256=ñ".encode("latin-1")},
    )
    assert r.status_code == 401
