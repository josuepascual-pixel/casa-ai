"""Adaptador BluOS (Bluesound / NAD / Blue Note).

Cada reproductor BluOS sirve la "Custom Integration API" en el puerto 11000 de
la red local: peticiones HTTP GET, respuestas XML, sin autenticacion y sin
cuenta en la nube. Eso lo hace ideal para un agente local: latencia minima y
funciona aunque se caiga internet.

Endpoints usados: /Status, /SyncStatus, /Play, /Pause, /Stop, /Skip, /Back,
/Volume, /Presets, /Preset, /AddSlave, /RemoveSlave.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import xmltodict

from ..settings import BluOSPlayer, Inventario
from .base import AdapterError, contacto

ACCIONES_SIMPLES = {
    "play": "/Play",
    "pausa": "/Pause",
    "pause": "/Pause",
    "stop": "/Stop",
    "parar": "/Stop",
    "siguiente": "/Skip",
    "skip": "/Skip",
    "anterior": "/Back",
    "back": "/Back",
}


class BluOS:
    def __init__(self, inventario: Inventario) -> None:
        self._inv = inventario
        # Perezoso: construir un AsyncClient cuesta ~66 ms porque monta un
        # contexto TLS, y con estos equipos solo se habla HTTP en claro. Una
        # casa sin BluOS no deberia pagarlo en cada arranque.
        self._http: httpx.AsyncClient | None = None

    @property
    def configurado(self) -> bool:
        return bool(self._inv.bluos)

    def _cliente(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))
        return self._http

    async def cerrar(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _resolver(self, nombre: str | None) -> BluOSPlayer:
        if not self._inv.bluos:
            raise AdapterError(
                "No hay reproductores BluOS en el inventario. Anadelos en la "
                "seccion `bluos:` de config/config.yaml."
            )
        if not nombre:
            return self._inv.bluos[0]
        player = self._inv.bluos_por_nombre(nombre)
        if player is None:
            disponibles = ", ".join(p.nombre for p in self._inv.bluos)
            raise AdapterError(
                f"No conozco el reproductor '{nombre}'. Disponibles: {disponibles}."
            )
        return player

    async def _get(self, player: BluOSPlayer, ruta: str, **params: Any) -> dict[str, Any]:
        url = f"http://{player.host}:{player.puerto}{ruta}"
        limpios = {k: v for k, v in params.items() if v is not None}
        with contacto(f"el reproductor '{player.nombre}' ({player.host}:{player.puerto})"):
            try:
                r = await self._cliente().get(url, params=limpios)
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise AdapterError(
                    f"{player.nombre} respondio HTTP {e.response.status_code} a {ruta}"
                ) from e
        if not r.text.strip():
            return {}
        try:
            return xmltodict.parse(r.text) or {}
        except Exception as e:
            raise AdapterError(f"Respuesta XML ilegible de {player.nombre}: {e}") from e

    # --- Lectura ---------------------------------------------------------
    async def estado(self, nombre: str | None = None) -> dict[str, Any]:
        player = self._resolver(nombre)
        datos = (await self._get(player, "/Status")).get("status", {}) or {}

        def txt(clave: str) -> str | None:
            v = datos.get(clave)
            return v if isinstance(v, str) else None

        volumen = datos.get("volume")
        return {
            "reproductor": player.nombre,
            "zona": player.zona,
            "estado": txt("state"),
            "volumen": int(volumen) if str(volumen).lstrip("-").isdigit() else None,
            "silenciado": str(datos.get("mute", "0")) == "1",
            "titulo": txt("title1"),
            "artista": txt("title2") or txt("artist"),
            "album": txt("title3") or txt("album"),
            "servicio": txt("service"),
            "segundo_actual": datos.get("secs"),
            "duracion_s": datos.get("totlen"),
        }

    async def estado_todos(self) -> list[dict[str, Any]]:
        """Estado de todos los reproductores en paralelo.

        Un reproductor apagado o desenchufado no debe tumbar la respuesta:
        se reporta su error y se sigue con el resto.
        """
        if not self._inv.bluos:
            return []
        resultados = await asyncio.gather(
            *(self.estado(p.nombre) for p in self._inv.bluos), return_exceptions=True
        )
        salida: list[dict[str, Any]] = []
        for player, res in zip(self._inv.bluos, resultados, strict=True):
            if isinstance(res, Exception):
                salida.append(
                    {"reproductor": player.nombre, "zona": player.zona, "error": str(res)}
                )
            else:
                salida.append(res)
        return salida

    async def presets(self, nombre: str | None = None) -> list[dict[str, Any]]:
        player = self._resolver(nombre)
        datos = (await self._get(player, "/Presets")).get("presets", {}) or {}
        crudos = datos.get("preset", [])
        if isinstance(crudos, dict):
            crudos = [crudos]
        return [
            {"id": p.get("@id"), "nombre": p.get("@name"), "url": p.get("@url")}
            for p in crudos
        ]

    async def grupo(self, nombre: str | None = None) -> dict[str, Any]:
        player = self._resolver(nombre)
        datos = (await self._get(player, "/SyncStatus")).get("SyncStatus", {}) or {}
        esclavos = datos.get("slave", [])
        if isinstance(esclavos, dict):
            esclavos = [esclavos]
        return {
            "reproductor": player.nombre,
            "modelo": datos.get("@model"),
            "es_maestro_de": [s.get("@id") for s in esclavos],
            "maestro": (datos.get("master") or {}).get("#text")
            if isinstance(datos.get("master"), dict)
            else datos.get("master"),
        }

    # --- Control ---------------------------------------------------------
    async def control(
        self, accion: str, nombre: str | None = None, valor: int | None = None
    ) -> dict[str, Any]:
        player = self._resolver(nombre)
        accion = accion.strip().lower()

        if accion in ACCIONES_SIMPLES:
            await self._get(player, ACCIONES_SIMPLES[accion])
        elif accion in ("volumen", "volume"):
            if valor is None:
                raise AdapterError("Para cambiar el volumen hace falta un valor de 0 a 100.")
            nivel = max(0, min(100, int(valor)))
            # tell_slaves=1 aplica el cambio a todo el grupo si esta agrupado.
            await self._get(player, "/Volume", level=nivel, tell_slaves=1)
        elif accion in ("silenciar", "mute"):
            await self._get(player, "/Volume", mute=1, tell_slaves=1)
        elif accion in ("desilenciar", "unmute"):
            await self._get(player, "/Volume", mute=0, tell_slaves=1)
        elif accion == "preset":
            if valor is None:
                raise AdapterError("Indica el numero de preset a cargar.")
            await self._get(player, "/Preset", id=int(valor))
        else:
            raise AdapterError(
                f"Accion '{accion}' no reconocida. Validas: play, pausa, stop, "
                "siguiente, anterior, volumen, silenciar, desilenciar, preset."
            )

        # Damos un margen minimo: el reproductor tarda un instante en reflejar
        # el cambio en /Status y devolver el estado viejo confunde al agente.
        await asyncio.sleep(0.4)
        return await self.estado(player.nombre)

    async def _esclavos(
        self, maestro: str, esclavos: list[str], ruta: str, clave: str
    ) -> dict[str, Any]:
        m = self._resolver(maestro)
        tocados: list[str] = []
        for nombre_esclavo in esclavos:
            e = self._resolver(nombre_esclavo)
            if e.host == m.host:  # agruparse consigo mismo no es un error, es nada
                continue
            await self._get(m, ruta, slave=e.host, port=e.puerto)
            tocados.append(e.nombre)
        return {"maestro": m.nombre, clave: tocados}

    async def agrupar(self, maestro: str, esclavos: list[str]) -> dict[str, Any]:
        """Agrupa reproductores para que suenen sincronizados (multiroom)."""
        return await self._esclavos(maestro, esclavos, "/AddSlave", "agrupados")

    async def desagrupar(self, maestro: str, esclavos: list[str] | None = None) -> dict[str, Any]:
        """Sin lista explicita, deshace el grupo entero."""
        m = self._resolver(maestro)
        objetivo = esclavos or [p.nombre for p in self._inv.bluos if p.host != m.host]
        return await self._esclavos(maestro, objetivo, "/RemoveSlave", "desagrupados")
