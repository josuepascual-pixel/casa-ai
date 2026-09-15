"""Canal de WhatsApp: botones de confirmacion y procesado de mensajes.

La verificacion de firma esta cubierta en test_seguridad_canales.py. Aqui se
prueba el flujo: que las acciones de riesgo se confirman con botones y que la
pulsacion no pasa por el modelo.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from casa_ai.channels.whatsapp import CANCELAR, CONFIRMAR, CanalWhatsApp, _extraer
from casa_ai.settings import Settings

from .dobles import AplicacionFalsa, StoreFalso


@pytest.fixture
def canal(settings: Settings, monkeypatch) -> CanalWhatsApp:
    configurado = settings.model_copy(update={
        "whatsapp_token": "tok",
        "whatsapp_phone_number_id": "123",
        "whatsapp_app_secret": "secreto",
        "whatsapp_numeros_autorizados": "34600111222",
    })
    app = AplicacionFalsa(
        configurado, detalle="Bateria cargando a 3000 W.", store=StoreFalso(turno=2)
    )
    canal = CanalWhatsApp(app)  # type: ignore[arg-type]

    enviados: list[dict[str, Any]] = []

    async def falsa_peticion(self: Any, cuerpo: dict[str, Any]) -> None:
        enviados.append(cuerpo)

    monkeypatch.setattr(CanalWhatsApp, "_peticion", falsa_peticion)
    canal.enviados = enviados  # type: ignore[attr-defined]
    return canal


def _textos(canal: CanalWhatsApp) -> list[str]:
    return [
        c["text"]["body"]
        for c in canal.enviados  # type: ignore[attr-defined]
        if c.get("type") == "text"
    ]


def _interactivos(canal: CanalWhatsApp) -> list[dict[str, Any]]:
    return [
        c for c in canal.enviados  # type: ignore[attr-defined]
        if c.get("type") == "interactive"
    ]


# --- Turno normal -----------------------------------------------------------


async def test_un_mensaje_de_texto_da_un_turno(canal: CanalWhatsApp) -> None:
    await canal._procesar(
        {"type": "text", "text": {"body": "enciende el salon"}}, "34600111222"
    )

    turno = canal.app.turnos[0]  # type: ignore[attr-defined]
    assert turno["canal"] == "whatsapp"
    assert turno["usuario"] == "34600111222"
    assert turno["entrada"] == "enciende el salon"
    assert turno["confirmacion"] == "boton"
    assert _textos(canal) == ["hecho"]


async def test_una_respuesta_larga_se_trocea(canal: CanalWhatsApp) -> None:
    canal.app.respuesta = "x" * 9000  # type: ignore[attr-defined]
    await canal._procesar({"type": "text", "text": {"body": "hola"}}, "34600111222")

    trozos = _textos(canal)
    assert len(trozos) == 3
    assert all(len(x) <= 4000 for x in trozos)
    assert "".join(trozos) == "x" * 9000


async def test_un_tipo_no_soportado_se_explica(canal: CanalWhatsApp) -> None:
    await canal._procesar({"type": "sticker"}, "34600111222")

    assert "sticker" in _textos(canal)[0]
    assert canal.app.turnos == []  # type: ignore[attr-defined]


async def test_un_fallo_no_deja_al_usuario_sin_respuesta(
    canal: CanalWhatsApp,
) -> None:
    async def revienta(**kwargs: Any) -> str:
        raise RuntimeError("el inversor no responde")

    canal.app.responder = revienta  # type: ignore[attr-defined]
    await canal._procesar({"type": "text", "text": {"body": "hola"}}, "34600111222")

    assert "Algo ha fallado" in _textos(canal)[0]


# --- Botones ----------------------------------------------------------------


async def test_una_accion_pendiente_saca_botones(canal: CanalWhatsApp) -> None:
    canal.app.store.pendientes = [  # type: ignore[attr-defined]
        {"token": "abc123", "resumen": "Forzar carga a 3000 W", "turno": 2}
    ]

    await canal._procesar({"type": "text", "text": {"body": "carga"}}, "34600111222")

    interactivo = _interactivos(canal)[0]["interactive"]
    assert "Forzar carga a 3000 W" in interactivo["body"]["text"]
    ids = [b["reply"]["id"] for b in interactivo["action"]["buttons"]]
    assert ids == ["ok:abc123", "no:abc123"]
    # La Cloud API limita el titulo a 20 caracteres.
    assert all(
        len(b["reply"]["title"]) <= 20 for b in interactivo["action"]["buttons"]
    )


async def test_una_propuesta_de_otro_turno_no_reaparece(
    canal: CanalWhatsApp,
) -> None:
    canal.app.store.pendientes = [  # type: ignore[attr-defined]
        {"token": "viejo", "resumen": "Apagar el wifi", "turno": 1}
    ]
    canal.app.store.turno = 5  # type: ignore[attr-defined]

    await canal._procesar({"type": "text", "text": {"body": "hola"}}, "34600111222")

    assert _interactivos(canal) == []
    assert canal.app.store.turnos_pedidos == [5]  # type: ignore[attr-defined]


async def test_pulsar_confirmar_no_pasa_por_el_modelo(canal: CanalWhatsApp) -> None:
    await canal._procesar(
        {"type": "interactive",
         "interactive": {"type": "button_reply",
                         "button_reply": {"id": CONFIRMAR + "abc123",
                                          "title": "Confirmar"}}},
        "34600111222",
    )

    assert canal.app.confirmadas == [{  # type: ignore[attr-defined]
        "canal": "whatsapp", "usuario": "34600111222",
        "conversacion": "whatsapp:34600111222", "token": "abc123",
    }]
    assert canal.app.turnos == []  # type: ignore[attr-defined]
    assert "Bateria cargando a 3000 W" in _textos(canal)[0]


async def test_pulsar_cancelar_descarta(canal: CanalWhatsApp) -> None:
    await canal._procesar(
        {"type": "interactive",
         "interactive": {"button_reply": {"id": CANCELAR + "abc123"}}},
        "34600111222",
    )

    assert canal.app.canceladas[0]["token"] == "abc123"  # type: ignore[attr-defined]
    assert "Cancelado" in _textos(canal)[0]
    assert canal.app.confirmadas == []  # type: ignore[attr-defined]


async def test_un_fallo_al_confirmar_se_cuenta(canal: CanalWhatsApp) -> None:
    async def falla(**kwargs: Any) -> tuple[str, bool]:
        return ("ha caducado", True)

    canal.app.confirmar_pendiente = falla  # type: ignore[attr-defined]
    await canal._procesar(
        {"type": "interactive",
         "interactive": {"button_reply": {"id": CONFIRMAR + "viejo"}}},
        "34600111222",
    )

    assert "No se pudo" in _textos(canal)[0]
    assert "caducado" in _textos(canal)[0]


async def test_un_id_de_boton_desconocido_se_ignora(canal: CanalWhatsApp) -> None:
    await canal._procesar(
        {"type": "interactive", "interactive": {"button_reply": {"id": "raro:x"}}},
        "34600111222",
    )

    assert canal.app.confirmadas == []  # type: ignore[attr-defined]
    assert canal.app.canceladas == []  # type: ignore[attr-defined]
    assert canal.enviados == []  # type: ignore[attr-defined]


async def test_un_interactivo_sin_datos_no_revienta(canal: CanalWhatsApp) -> None:
    await canal._procesar({"type": "interactive"}, "34600111222")
    assert canal.app.confirmadas == []  # type: ignore[attr-defined]


# --- Sobre de Meta ----------------------------------------------------------


def test_extraer_varios_mensajes() -> None:
    cuerpo = json.loads(json.dumps({
        "entry": [
            {"changes": [{"value": {"messages": [
                {"from": "111", "type": "text", "text": {"body": "a"}},
                {"from": "222", "type": "text", "text": {"body": "b"}},
            ]}}]},
            {"changes": [{"value": {"messages": [
                {"from": "333", "type": "text", "text": {"body": "c"}},
            ]}}]},
        ]
    }))
    assert [n for _, n in _extraer(cuerpo)] == ["111", "222", "333"]


def test_extraer_aguanta_un_sobre_raro() -> None:
    """Meta manda tambien avisos de estado sin `messages`."""
    assert _extraer({}) == []
    assert _extraer({"entry": [{"changes": [{"value": {"statuses": []}}]}]}) == []
    assert _extraer({"entry": [{"changes": [{"value": {"messages": [{}]}}]}]}) == []


def test_configurado_exige_el_secreto(settings: Settings) -> None:
    """Sin el secreto no se puede verificar la firma, asi que el canal no
    cuenta como configurado."""
    sin_secreto = settings.model_copy(update={
        "whatsapp_token": "tok", "whatsapp_phone_number_id": "123",
    })
    canal = CanalWhatsApp(AplicacionFalsa(sin_secreto))  # type: ignore[arg-type]
    assert canal.configurado is False
