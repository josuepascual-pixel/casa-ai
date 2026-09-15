"""Adaptador UniFi: red (UniFi Network) y camaras (UniFi Protect).

Ambos productos viven detras de la misma consola UniFi OS (UDM, UDM-Pro, Cloud
Key Gen2+, UNVR), asi que comparten la sesion: un solo login sirve para los dos
y se reutiliza la cookie. Las rutas van por el proxy de UniFi OS
(`/proxy/network/...`, `/proxy/protect/...`).

Se detecta tambien el caso de controlador clasico (sin UniFi OS), que usa
`/api/login` y rutas sin el prefijo `/proxy/network`.

Nota sobre TLS: las consolas UniFi traen certificado autofirmado, pero por
este canal viajan las credenciales del administrador local, asi que la
verificacion esta ACTIVADA por defecto. La forma correcta de resolverlo es
exportar el certificado de la consola y apuntarlo con UNIFI_CA_BUNDLE, no
desactivar la verificacion.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from ..settings import Settings
from .base import AdapterError, NoConfigurado

TTL_SESION_S = 3600.0


class UniFi:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._host = settings.unifi_host
        self._base = f"https://{settings.unifi_host}:{settings.unifi_puerto}"
        self._site = settings.unifi_site
        self._cliente: httpx.AsyncClient | None = None
        self._login_ts: float = 0.0
        self._unifi_os = True
        # `informe_casa` pide salud y camaras en el mismo gather: sin el lock,
        # con la sesion fria entran los dos a `_sesion()` y el segundo cierra
        # el cliente que el primero acaba de usar.
        self._lock = asyncio.Lock()

    @property
    def configurado(self) -> bool:
        return bool(self._host and self._s.unifi_usuario and self._s.unifi_password)

    async def cerrar(self) -> None:
        if self._cliente is not None:
            await self._cliente.aclose()
            self._cliente = None

    # --- Sesion ----------------------------------------------------------
    def _viva(self) -> httpx.AsyncClient | None:
        if self._cliente is not None and time.monotonic() - self._login_ts < TTL_SESION_S:
            return self._cliente
        return None

    async def _sesion(self) -> httpx.AsyncClient:
        if not self.configurado:
            raise NoConfigurado(
                "UniFi sin configurar: hacen falta UNIFI_HOST, UNIFI_USUARIO y "
                "UNIFI_PASSWORD. Usa una cuenta local del controlador (no la "
                "cuenta de ui.com) y, si puedes, con permisos de solo lo necesario."
            )
        viva = self._viva()
        if viva is not None:
            return viva
        async with self._lock:
            # Otro pudo haber hecho login mientras esperabamos el lock.
            viva = self._viva()
            if viva is not None:
                return viva
            return await self._login()

    async def _login(self) -> httpx.AsyncClient:
        await self.cerrar()
        self._cliente = httpx.AsyncClient(
            base_url=self._base,
            verify=self._s.verificacion_tls_unifi,
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=True,
        )
        credenciales = {
            "username": self._s.unifi_usuario,
            "password": self._s.unifi_password,
        }
        # Primero UniFi OS; si devuelve 404 probamos controlador clasico.
        for ruta, es_os in (("/api/auth/login", True), ("/api/login", False)):
            try:
                r = await self._cliente.post(ruta, json=credenciales)
            except httpx.ConnectError as e:
                await self.cerrar()
                if "CERTIFICATE_VERIFY_FAILED" in str(e) or "SSL" in str(e).upper():
                    raise AdapterError(
                        "La consola UniFi presenta un certificado que no se puede "
                        "verificar, que es lo normal en una consola con certificado "
                        "autofirmado. No desactives la verificacion sin mas: por ese "
                        "canal viajan las credenciales del administrador local. "
                        "Exporta el certificado de la consola y apuntalo con "
                        "UNIFI_CA_BUNDLE=/ruta/al/unifi.pem. Si aceptas el riesgo en "
                        "una red de confianza, UNIFI_VERIFICAR_TLS=false."
                    ) from e
                raise AdapterError(f"No se pudo contactar con la consola UniFi: {e}") from e
            except httpx.HTTPError as e:
                await self.cerrar()
                raise AdapterError(f"No se pudo contactar con la consola UniFi: {e}") from e
            if r.status_code == 404:
                continue
            if r.status_code in (400, 401, 403):
                await self.cerrar()
                raise AdapterError(
                    "UniFi rechazo las credenciales (HTTP "
                    f"{r.status_code}). Revisa usuario y contrasena locales."
                )
            r.raise_for_status()
            self._unifi_os = es_os
            csrf = r.headers.get("x-csrf-token") or r.headers.get("X-CSRF-Token")
            if csrf:
                self._cliente.headers["X-CSRF-Token"] = csrf
            self._login_ts = time.monotonic()
            return self._cliente

        await self.cerrar()
        raise AdapterError("La consola UniFi no expuso ningun endpoint de login conocido.")

    def _ruta_red(self, sufijo: str) -> str:
        prefijo = "/proxy/network" if self._unifi_os else ""
        return f"{prefijo}/api/s/{self._site}{sufijo}"

    async def _peticion(self, metodo: str, ruta: str, **kw: Any) -> httpx.Response:
        """Una peticion con la sesion, reintentando una vez si caduco.

        El 401 aqui no es "credenciales malas" —eso lo detecta el login— sino
        "la cookie ya no vale": la consola las invalida al reiniciarse.
        """
        cliente = await self._sesion()
        try:
            r = await cliente.request(metodo, ruta, **kw)
        except httpx.HTTPError as e:
            raise AdapterError(f"Fallo la peticion a UniFi ({metodo} {ruta}): {e}") from e
        if r.status_code != 401:
            return r
        self._login_ts = 0.0
        cliente = await self._sesion()
        try:
            return await cliente.request(metodo, ruta, **kw)
        except httpx.HTTPError as e:
            raise AdapterError(f"Fallo la peticion a UniFi ({metodo} {ruta}): {e}") from e

    async def _red(self, metodo: str, sufijo: str, **kw: Any) -> httpx.Response:
        """Peticion a UniFi Network.

        El login es lo que decide si la consola lleva UniFi OS, y de eso
        depende el prefijo de la ruta: hay que tener sesion ANTES de
        construirla.
        """
        await self._sesion()
        r = await self._peticion(metodo, self._ruta_red(sufijo), **kw)
        if r.is_error:
            raise AdapterError(f"UniFi rechazo la operacion: HTTP {r.status_code} {r.text[:200]}")
        return r

    async def _red_get(self, sufijo: str) -> list[dict[str, Any]]:
        r = await self._red("GET", sufijo)
        return (r.json() or {}).get("data", [])

    async def _red_post(self, sufijo: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        r = await self._red("POST", sufijo, json=payload)
        return (r.json() or {}).get("data", [])

    # --- Red: lectura ----------------------------------------------------
    async def salud(self) -> dict[str, Any]:
        datos = await self._red_get("/stat/health")
        resumen: dict[str, Any] = {}
        for sub in datos:
            nombre = sub.get("subsystem", "?")
            resumen[nombre] = {
                "estado": sub.get("status"),
                "usuarios": sub.get("num_user"),
                "adoptados": sub.get("num_adopted"),
                "caidos": sub.get("num_disconnected"),
            }
            if nombre == "wan":
                resumen[nombre].update(
                    {
                        "ip": sub.get("wan_ip"),
                        "latencia_ms": sub.get("latency"),
                        "bajada_mbps": sub.get("xput_down"),
                        "subida_mbps": sub.get("xput_up"),
                    }
                )
        return resumen

    async def dispositivos(self) -> list[dict[str, Any]]:
        """Puntos de acceso, switches, gateway: la infraestructura propia."""
        datos = await self._red_get("/stat/device")
        return [
            {
                "nombre": d.get("name") or d.get("model"),
                "modelo": d.get("model"),
                "mac": d.get("mac"),
                "ip": d.get("ip"),
                "tipo": d.get("type"),
                "estado": "conectado" if d.get("state") == 1 else "desconectado",
                "clientes": d.get("num_sta"),
                "uptime_h": round((d.get("uptime") or 0) / 3600, 1),
                "version": d.get("version"),
            }
            for d in datos
        ]

    async def clientes(self, texto: str | None = None, limite: int = 50) -> list[dict[str, Any]]:
        datos = await self._red_get("/stat/sta")
        texto_low = (texto or "").lower()
        salida: list[dict[str, Any]] = []
        for c in datos:
            nombre = c.get("name") or c.get("hostname") or c.get("mac", "")
            mac = str(c.get("mac", "")).lower()
            if texto_low and texto_low not in str(nombre).lower() and texto_low not in mac:
                continue
            salida.append(
                {
                    "nombre": nombre,
                    "mac": c.get("mac"),
                    "ip": c.get("ip"),
                    "conexion": "cable" if c.get("is_wired") else "wifi",
                    "ssid": c.get("essid"),
                    "senal_dbm": c.get("signal"),
                    "bloqueado": bool(c.get("blocked")),
                }
            )
            if len(salida) >= limite:
                break
        return salida

    async def wifis(self) -> list[dict[str, Any]]:
        datos = await self._red_get("/rest/wlanconf")
        return [
            {"id": w.get("_id"), "ssid": w.get("name"), "activa": bool(w.get("enabled"))}
            for w in datos
        ]

    # --- Red: escritura --------------------------------------------------
    async def cambiar_wifi(self, ssid: str, activar: bool) -> dict[str, Any]:
        objetivo = None
        for w in await self.wifis():
            if str(w["ssid"]).lower() == ssid.strip().lower():
                objetivo = w
                break
        if objetivo is None:
            disponibles = ", ".join(str(w["ssid"]) for w in await self.wifis())
            raise AdapterError(f"No existe la red wifi '{ssid}'. Disponibles: {disponibles}.")

        await self._red(
            "PUT", f"/rest/wlanconf/{objetivo['id']}", json={"enabled": activar}
        )
        return {"ssid": objetivo["ssid"], "activa": activar}

    async def bloquear_cliente(self, mac: str, bloquear: bool) -> dict[str, Any]:
        cmd = "block-sta" if bloquear else "unblock-sta"
        await self._red_post("/cmd/stamgr", {"cmd": cmd, "mac": mac.lower()})
        return {"mac": mac.lower(), "bloqueado": bloquear}

    async def reiniciar_dispositivo(self, mac: str) -> dict[str, Any]:
        await self._red_post("/cmd/devmgr", {"cmd": "restart", "mac": mac.lower()})
        return {"mac": mac.lower(), "detalle": "Reinicio ordenado. Tardara 1-2 minutos en volver."}

    # --- Protect: camaras ------------------------------------------------
    async def _protect_get(self, ruta: str) -> httpx.Response:
        r = await self._peticion("GET", f"/proxy/protect/api{ruta}")
        if r.status_code == 404:
            raise AdapterError(
                "UniFi Protect no responde en esta consola. Solo esta disponible "
                "en consolas con Protect instalado (UDM-Pro, UNVR, Cloud Key Gen2+)."
            )
        if r.is_error:
            raise AdapterError(f"UniFi Protect fallo: HTTP {r.status_code} {r.text[:200]}")
        return r

    async def camaras(self) -> list[dict[str, Any]]:
        datos = (await self._protect_get("/bootstrap")).json()
        return [
            {
                "id": c.get("id"),
                "nombre": c.get("name"),
                "modelo": c.get("type"),
                "conectada": bool(c.get("isConnected")),
                "grabando": bool(c.get("isRecording")),
                "movimiento_detectado": bool(c.get("isMotionDetected")),
                "estado_luz": (c.get("ledSettings") or {}).get("isEnabled"),
            }
            for c in datos.get("cameras", [])
        ]

    async def snapshot(self, id_camara: str, *, alta_calidad: bool = True) -> bytes:
        """Devuelve un JPEG. Se le pasa al modelo como imagen para que MIRE."""
        r = await self._protect_get(
            f"/cameras/{id_camara}/snapshot?ts={int(time.time() * 1000)}"
            f"&highQuality={'true' if alta_calidad else 'false'}"
        )
        if not r.content:
            raise AdapterError(f"La camara {id_camara} devolvio una imagen vacia.")
        return r.content

    async def eventos(self, *, horas: int = 2, limite: int = 20) -> list[dict[str, Any]]:
        desde = int((time.time() - horas * 3600) * 1000)
        hasta = int(time.time() * 1000)
        r = await self._protect_get(f"/events?start={desde}&end={hasta}&limit={limite}")
        eventos = r.json() or []
        return [
            {
                "tipo": e.get("type"),
                "camara": e.get("camera"),
                "inicio": e.get("start"),
                "fin": e.get("end"),
                "puntuacion": e.get("score"),
                "detecciones": e.get("smartDetectTypes"),
            }
            for e in eventos
        ]
