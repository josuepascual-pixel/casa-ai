"""Adaptador UniFi: sesion, doble ruta de login, reintento y operaciones.

Era el modulo con menos cobertura del proyecto (24 %) y el que mas logica de
sesion tiene: dos rutas de login segun la consola, cookie compartida entre
Network y Protect, y reintento cuando la sesion caduca.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from casa_ai.adapters.base import AdapterError, NoConfigurado
from casa_ai.adapters.unifi import UniFi
from casa_ai.settings import Settings

BASE = "https://10.0.0.1:443"
# httpx quita el puerto por defecto de la URL, asi que los patrones de
# regex (a diferencia de las URL literales, que respx normaliza) van sin el.
BASE_RE = "https://10.0.0.1"
RED = f"{BASE}/proxy/network/api/s/default"


@pytest.fixture
def unifi(settings: Settings) -> UniFi:
    return UniFi(settings)


def _login_os() -> None:
    respx.post(f"{BASE}/api/auth/login").mock(
        return_value=httpx.Response(200, json={}, headers={"x-csrf-token": "csrf123"})
    )


def _datos(ruta: str, datos: list) -> None:
    respx.get(f"{RED}{ruta}").mock(return_value=httpx.Response(200, json={"data": datos}))


# --- Configuracion y sesion -------------------------------------------------


async def test_sin_credenciales_lo_dice(settings: Settings) -> None:
    sin = UniFi(settings.model_copy(update={"unifi_usuario": None}))
    assert sin.configurado is False
    with pytest.raises(NoConfigurado, match="cuenta local"):
        await sin.salud()


@respx.mock
async def test_login_de_unifi_os_y_token_csrf(unifi: UniFi) -> None:
    _login_os()
    _datos("/stat/health", [])
    await unifi.salud()

    cliente = await unifi._sesion()
    assert cliente.headers["X-CSRF-Token"] == "csrf123"
    await unifi.cerrar()


@respx.mock
async def test_cae_al_login_del_controlador_clasico(unifi: UniFi) -> None:
    """Una consola sin UniFi OS devuelve 404 en /api/auth/login."""
    respx.post(f"{BASE}/api/auth/login").mock(return_value=httpx.Response(404))
    respx.post(f"{BASE}/api/login").mock(return_value=httpx.Response(200, json={}))
    # Sin UniFi OS las rutas van sin el prefijo /proxy/network
    respx.get(f"{BASE}/api/s/default/stat/health").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    await unifi.salud()

    assert unifi._unifi_os is False
    await unifi.cerrar()


@respx.mock
async def test_credenciales_rechazadas(unifi: UniFi) -> None:
    respx.post(f"{BASE}/api/auth/login").mock(return_value=httpx.Response(401))
    with pytest.raises(AdapterError, match="rechazo las credenciales"):
        await unifi.salud()


@respx.mock
async def test_ninguna_ruta_de_login_conocida(unifi: UniFi) -> None:
    respx.post(f"{BASE}/api/auth/login").mock(return_value=httpx.Response(404))
    respx.post(f"{BASE}/api/login").mock(return_value=httpx.Response(404))
    with pytest.raises(AdapterError, match="ningun endpoint de login"):
        await unifi.salud()


@respx.mock
async def test_un_fallo_de_tls_explica_que_hacer(unifi: UniFi) -> None:
    """No basta con decir que fallo: hay que decir que NO se desactive la
    verificacion, porque por ahi van las credenciales de administrador."""
    respx.post(f"{BASE}/api/auth/login").mock(
        side_effect=httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] self signed")
    )
    with pytest.raises(AdapterError, match="UNIFI_CA_BUNDLE"):
        await unifi.salud()


@respx.mock
async def test_una_sesion_caducada_se_reintenta(unifi: UniFi) -> None:
    _login_os()
    respuestas = [
        httpx.Response(401),
        httpx.Response(200, json={"data": [{"subsystem": "wan", "status": "ok"}]}),
    ]
    respx.get(f"{RED}/stat/health").mock(side_effect=respuestas)

    salud = await unifi.salud()

    assert salud["wan"]["estado"] == "ok"
    await unifi.cerrar()


# --- Lectura ----------------------------------------------------------------


@respx.mock
async def test_salud_resume_la_wan(unifi: UniFi) -> None:
    _login_os()
    _datos("/stat/health", [
        {"subsystem": "wan", "status": "ok", "wan_ip": "88.1.2.3",
         "latency": 12, "xput_down": 940.5, "xput_up": 320.1},
        {"subsystem": "wlan", "status": "ok", "num_user": 24, "num_adopted": 3},
    ])

    salud = await unifi.salud()

    assert salud["wan"]["ip"] == "88.1.2.3"
    assert salud["wan"]["latencia_ms"] == 12
    assert salud["wan"]["bajada_mbps"] == 940.5
    assert salud["wlan"]["usuarios"] == 24
    await unifi.cerrar()


@respx.mock
async def test_dispositivos(unifi: UniFi) -> None:
    _login_os()
    _datos("/stat/device", [
        {"name": "AP Salon", "model": "U6LR", "mac": "aa:bb", "ip": "10.0.0.5",
         "type": "uap", "state": 1, "num_sta": 7, "uptime": 7200, "version": "6.5"},
        {"model": "USW", "mac": "cc:dd", "state": 0, "uptime": 0},
    ])

    d = await unifi.dispositivos()

    assert d[0]["nombre"] == "AP Salon"
    assert d[0]["estado"] == "conectado"
    assert d[0]["uptime_h"] == 2.0
    assert d[1]["nombre"] == "USW"       # sin nombre, cae al modelo
    assert d[1]["estado"] == "desconectado"
    await unifi.cerrar()


@respx.mock
async def test_clientes_con_filtro(unifi: UniFi) -> None:
    _login_os()
    _datos("/stat/sta", [
        {"name": "Movil Josue", "mac": "11:22", "ip": "10.0.0.20",
         "is_wired": False, "essid": "Casa", "signal": -52},
        {"hostname": "nas", "mac": "33:44", "ip": "10.0.0.10", "is_wired": True},
    ])

    todos = await unifi.clientes()
    assert len(todos) == 2
    assert todos[0]["conexion"] == "wifi"
    assert todos[1]["conexion"] == "cable"

    filtrado = await unifi.clientes("nas")
    assert [c["nombre"] for c in filtrado] == ["nas"]

    por_mac = await unifi.clientes("11:22")
    assert [c["nombre"] for c in por_mac] == ["Movil Josue"]
    await unifi.cerrar()


@respx.mock
async def test_el_limite_de_clientes_se_respeta(unifi: UniFi) -> None:
    _login_os()
    _datos("/stat/sta", [{"mac": f"aa:{i}", "is_wired": True} for i in range(80)])
    assert len(await unifi.clientes(limite=10)) == 10
    await unifi.cerrar()


@respx.mock
async def test_wifis(unifi: UniFi) -> None:
    _login_os()
    _datos("/rest/wlanconf", [
        {"_id": "w1", "name": "Casa", "enabled": True},
        {"_id": "w2", "name": "Invitados", "enabled": False},
    ])

    w = await unifi.wifis()

    assert w == [
        {"id": "w1", "ssid": "Casa", "activa": True},
        {"id": "w2", "ssid": "Invitados", "activa": False},
    ]
    await unifi.cerrar()


# --- Escritura --------------------------------------------------------------


@respx.mock
async def test_cambiar_wifi(unifi: UniFi) -> None:
    _login_os()
    _datos("/rest/wlanconf", [{"_id": "w2", "name": "Invitados", "enabled": True}])
    ruta = respx.put(f"{RED}/rest/wlanconf/w2").mock(
        return_value=httpx.Response(200, json={})
    )

    r = await unifi.cambiar_wifi("invitados", False)  # sin importar mayusculas

    assert r == {"ssid": "Invitados", "activa": False}
    import json
    assert json.loads(ruta.calls.last.request.content) == {"enabled": False}
    await unifi.cerrar()


@respx.mock
async def test_un_ssid_inexistente_lista_los_que_hay(unifi: UniFi) -> None:
    _login_os()
    _datos("/rest/wlanconf", [{"_id": "w1", "name": "Casa", "enabled": True}])
    with pytest.raises(AdapterError, match="Disponibles: Casa"):
        await unifi.cambiar_wifi("Oficina", False)
    await unifi.cerrar()


@respx.mock
async def test_bloquear_y_desbloquear_cliente(unifi: UniFi) -> None:
    _login_os()
    ruta = respx.post(f"{RED}/cmd/stamgr").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    assert await unifi.bloquear_cliente("AA:BB:CC", True) == {
        "mac": "aa:bb:cc", "bloqueado": True
    }
    import json
    assert json.loads(ruta.calls.last.request.content) == {
        "cmd": "block-sta", "mac": "aa:bb:cc"
    }

    await unifi.bloquear_cliente("AA:BB:CC", False)
    assert json.loads(ruta.calls.last.request.content)["cmd"] == "unblock-sta"
    await unifi.cerrar()


@respx.mock
async def test_reiniciar_dispositivo(unifi: UniFi) -> None:
    _login_os()
    respx.post(f"{RED}/cmd/devmgr").mock(return_value=httpx.Response(200, json={"data": []}))
    r = await unifi.reiniciar_dispositivo("AA:BB")
    assert "1-2 minutos" in r["detalle"]
    await unifi.cerrar()


@respx.mock
async def test_un_rechazo_de_unifi_se_explica(unifi: UniFi) -> None:
    _login_os()
    respx.post(f"{RED}/cmd/devmgr").mock(
        return_value=httpx.Response(400, text="api.err.NoSiteContext")
    )
    with pytest.raises(AdapterError, match="rechazo la operacion"):
        await unifi.reiniciar_dispositivo("AA:BB")
    await unifi.cerrar()


# --- Protect ----------------------------------------------------------------


@respx.mock
async def test_camaras(unifi: UniFi) -> None:
    _login_os()
    respx.get(f"{BASE}/proxy/protect/api/bootstrap").mock(
        return_value=httpx.Response(200, json={"cameras": [
            {"id": "cam1", "name": "Puerta", "type": "G4 Doorbell",
             "isConnected": True, "isRecording": True, "isMotionDetected": False,
             "ledSettings": {"isEnabled": True}},
        ]})
    )

    c = await unifi.camaras()

    assert c[0] == {
        "id": "cam1", "nombre": "Puerta", "modelo": "G4 Doorbell",
        "conectada": True, "grabando": True, "movimiento_detectado": False,
        "estado_luz": True,
    }
    await unifi.cerrar()


@respx.mock
async def test_sin_protect_instalado_se_dice(unifi: UniFi) -> None:
    _login_os()
    respx.get(f"{BASE}/proxy/protect/api/bootstrap").mock(
        return_value=httpx.Response(404)
    )
    with pytest.raises(AdapterError, match="Protect no responde"):
        await unifi.camaras()
    await unifi.cerrar()


@respx.mock
async def test_snapshot(unifi: UniFi) -> None:
    _login_os()
    jpeg = b"\xff\xd8\xff\xe0" + b"x" * 100
    respx.get(url__regex=rf"{BASE_RE}/proxy/protect/api/cameras/cam1/snapshot.*").mock(
        return_value=httpx.Response(200, content=jpeg)
    )
    assert await unifi.snapshot("cam1") == jpeg
    await unifi.cerrar()


@respx.mock
async def test_un_snapshot_vacio_se_detecta(unifi: UniFi) -> None:
    _login_os()
    respx.get(url__regex=rf"{BASE_RE}/proxy/protect/api/cameras/cam1/snapshot.*").mock(
        return_value=httpx.Response(200, content=b"")
    )
    with pytest.raises(AdapterError, match="imagen vacia"):
        await unifi.snapshot("cam1")
    await unifi.cerrar()


@respx.mock
async def test_eventos(unifi: UniFi) -> None:
    _login_os()
    respx.get(url__regex=rf"{BASE_RE}/proxy/protect/api/events.*").mock(
        return_value=httpx.Response(200, json=[
            {"type": "smartDetectZone", "camera": "cam1", "start": 1, "end": 2,
             "score": 95, "smartDetectTypes": ["person"]},
        ])
    )
    e = await unifi.eventos(horas=1)

    assert e[0]["tipo"] == "smartDetectZone"
    assert e[0]["detecciones"] == ["person"]
    assert e[0]["puntuacion"] == 95
    await unifi.cerrar()


# --- Postura de seguridad ---------------------------------------------------


def _red_cerrada() -> None:
    _datos("/rest/portforward", [])
    _datos("/rest/setting", [{"key": "upnp", "enabled": False}, {"key": "mgmt"}])
    _datos("/rest/wlanconf", [
        {"name": "Casa", "enabled": True, "security": "wpapsk", "wpa_mode": "wpa2"},
        {"name": "Invitados", "enabled": True, "security": "wpapsk", "wpa_mode": "wpa2",
         "is_guest": True},
    ])
    _datos("/rest/networkconf", [
        {"name": "Default", "purpose": "corporate", "enabled": True},
        {"name": "IoT", "purpose": "corporate", "vlan": 20, "enabled": True},
        {"name": "WAN", "purpose": "wan", "enabled": True},
    ])
    _datos("/stat/device", [{"type": "udm"}, {"type": "usw"}])


@respx.mock
async def test_una_red_bien_hecha_no_tiene_avisos(unifi: UniFi) -> None:
    _login_os()
    _red_cerrada()
    assert await unifi.postura_seguridad() == []


@respx.mock
async def test_la_postura_dice_cada_cosa_abierta(unifi: UniFi) -> None:
    """La red de hoy: router de Vodafone, sin gateway UniFi, una sola red."""
    _login_os()
    _datos("/rest/portforward", [
        {"name": "camaras", "dst_port": "8443", "fwd": "192.168.0.20", "enabled": True},
        {"name": "vieja", "dst_port": "22", "fwd": "192.168.0.9", "enabled": False},
    ])
    _datos("/rest/setting", [{"key": "upnp", "enabled": True}])
    _datos("/rest/wlanconf", [
        {"name": "Casa", "enabled": True, "security": "wpapsk", "wpa_mode": "wpa"},
        {"name": "Casa-Invitados", "enabled": True, "security": "open"},
        {"name": "Vieja", "enabled": False, "security": "open"},
    ])
    _datos("/rest/networkconf", [{"name": "Default", "purpose": "corporate", "enabled": True}])
    _datos("/stat/device", [{"type": "uap"}, {"type": "usw"}])

    avisos = await unifi.postura_seguridad()

    texto = "\n".join(avisos)
    assert "1 puerto(s) abiertos hacia internet: camaras (8443→192.168.0.20)" in texto
    assert "vieja" not in texto  # la regla desactivada no cuenta
    assert "UPnP encendido" in texto
    assert "'Casa' usa WPA antiguo" in texto
    assert "'Casa-Invitados' no tiene contrasena" in texto
    assert "'Casa-Invitados' no esta marcada como red de invitados" in texto
    assert "'Vieja'" not in texto  # apagada
    assert "no hay ninguna wifi de invitados aislada" in texto
    assert "una sola red para todo" in texto
    assert "sin gateway UniFi" in texto
