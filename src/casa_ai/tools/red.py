"""Herramientas de red: UniFi Network (wifi, clientes, puntos de acceso)."""

from __future__ import annotations

import asyncio
from typing import Any

from ..agent.registry import Contexto, Herramienta, Riesgo, esquema


async def _estado(ctx: Contexto) -> dict[str, Any]:
    # Las tres son independientes: en serie costaban dos RTT de mas contra la
    # consola. El lock de `_sesion()` evita que en frio hagan tres logins.
    salud, dispositivos, wifis = await asyncio.gather(
        ctx.unifi.salud(), ctx.unifi.dispositivos(), ctx.unifi.wifis()
    )
    return {"salud": salud, "dispositivos": dispositivos, "wifis": wifis}


async def _clientes(ctx: Contexto, texto: str | None = None) -> dict[str, Any]:
    clientes = await ctx.unifi.clientes(texto)
    return {"conectados": len(clientes), "clientes": clientes}


async def _wifi(ctx: Contexto, ssid: str, activar: bool) -> dict[str, Any]:
    return await ctx.unifi.cambiar_wifi(ssid, activar)


async def _bloquear(ctx: Contexto, mac: str, bloquear: bool) -> dict[str, Any]:
    return await ctx.unifi.bloquear_cliente(mac, bloquear)


async def _reiniciar(ctx: Contexto, mac: str) -> dict[str, Any]:
    return await ctx.unifi.reiniciar_dispositivo(mac)


HERRAMIENTAS = [
    Herramienta(
        nombre="red_estado",
        descripcion=(
            "Estado de la red UniFi: salud de WAN/LAN/WLAN con IP publica, latencia "
            "y caudal, inventario de puntos de acceso y switches con su estado y "
            "numero de clientes, y lista de redes wifi con si estan activas."
        ),
        esquema=esquema({}),
        riesgo=Riesgo.LECTURA,
        handler=_estado,
        requiere="unifi",
    ),
    Herramienta(
        nombre="red_clientes",
        descripcion=(
            "Dispositivos conectados a la red, con IP, MAC, si van por cable o wifi, "
            "SSID, potencia de senal y si estan bloqueados. Acepta filtro por texto."
        ),
        esquema=esquema({"texto": {"type": "string", "description": "Filtro por nombre o MAC"}}),
        riesgo=Riesgo.LECTURA,
        handler=_clientes,
        requiere="unifi",
    ),
    Herramienta(
        nombre="red_wifi_activar",
        descripcion=(
            "Activa o desactiva una red wifi entera. Desactivarla deja sin conexion "
            "a todo lo que dependa de ese SSID, que puede incluir la propia domotica "
            "y las camaras. Avisa de eso antes de proponerlo."
        ),
        esquema=esquema(
            {"ssid": {"type": "string"}, "activar": {"type": "boolean"}},
            obligatorias=["ssid", "activar"],
        ),
        riesgo=Riesgo.ALTO,
        handler=_wifi,
        resumen_confirmacion=lambda a: (
            f"{'Activar' if a.get('activar') else 'DESACTIVAR'} la red wifi "
            f"'{a.get('ssid')}' para todos los dispositivos"
        ),
        requiere="unifi",
    ),
    Herramienta(
        nombre="red_bloquear_cliente",
        descripcion="Bloquea o desbloquea el acceso a la red de un dispositivo por su MAC.",
        esquema=esquema(
            {"mac": {"type": "string"}, "bloquear": {"type": "boolean"}},
            obligatorias=["mac", "bloquear"],
        ),
        riesgo=Riesgo.ALTO,
        handler=_bloquear,
        resumen_confirmacion=lambda a: (
            f"{'Bloquear' if a.get('bloquear') else 'Desbloquear'} el acceso a la red "
            f"del dispositivo {a.get('mac')}"
        ),
        requiere="unifi",
    ),
    Herramienta(
        nombre="red_reiniciar_dispositivo",
        descripcion=(
            "Reinicia un punto de acceso, switch o gateway UniFi por su MAC. Corta "
            "el servicio 1-2 minutos. Ultimo recurso para un equipo que no responde."
        ),
        esquema=esquema({"mac": {"type": "string"}}, obligatorias=["mac"]),
        riesgo=Riesgo.ALTO,
        handler=_reiniciar,
        resumen_confirmacion=lambda a: (
            f"Reiniciar el equipo de red {a.get('mac')} (cortara la conexion 1-2 minutos)"
        ),
        requiere="unifi",
    ),
]
