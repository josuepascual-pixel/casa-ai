"""Adaptador KNX para el sistema domotico ONNA.

ONNA (fabricante espanol) son pantallas y servidores IP-KNX que se conectan
directamente al bus KNX de la instalacion: no hay API propietaria en la nube,
la instalacion "es" el bus. Por tanto hay dos caminos para controlarla, y este
sistema usa los dos:

1. **Via Home Assistant (preferido).** La integracion `knx` de HA expone las
   direcciones de grupo como entidades normales (luces, persianas, clima). Es
   el camino por defecto: da nombres legibles, estados y es mas seguro.

2. **Bus KNX directo (este fichero).** Para direcciones de grupo que no estan
   expuestas en HA. Escribe telegramas crudos en el bus, asi que es riesgo
   alto por definicion: una direccion equivocada puede accionar cualquier
   actuador de la casa. Solo se permiten las direcciones declaradas
   explicitamente en `config/config.yaml`; no se acepta una direccion
   arbitraria inventada por el agente.

Conexion: tunel KNXnet/IP contra el servidor ONNA o contra un router KNX/IP.
Si el backend corre en Docker con red bridge, hace falta `knx_route_back`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..settings import Inventario, KNXGroupAddress, Settings
from .base import AdapterError, NoConfigurado


class KNXOnna:
    def __init__(self, settings: Settings, inventario: Inventario) -> None:
        self._s = settings
        self._inv = inventario
        self._xknx: Any = None
        self._lock = asyncio.Lock()

    @property
    def configurado(self) -> bool:
        return bool(self._s.knx_habilitado and self._s.knx_gateway_ip)

    @property
    def direcciones(self) -> list[KNXGroupAddress]:
        return self._inv.knx

    async def _conectar(self) -> Any:
        if not self.configurado:
            raise NoConfigurado(
                "Bus KNX directo desactivado. Para usarlo pon KNX_HABILITADO=true "
                "y KNX_GATEWAY_IP con la IP del servidor ONNA o del router KNX/IP. "
                "Sin esto, la domotica se controla a traves de Home Assistant."
            )
        if self._xknx is not None:
            return self._xknx
        try:
            from xknx import XKNX
            from xknx.io import ConnectionConfig, ConnectionType
        except ImportError as e:  # pragma: no cover
            raise AdapterError("Falta la dependencia xknx (pip install xknx)") from e

        config = ConnectionConfig(
            connection_type=ConnectionType.TUNNELING,
            gateway_ip=self._s.knx_gateway_ip,
            gateway_port=self._s.knx_gateway_puerto,
            route_back=self._s.knx_route_back,
        )
        xknx = XKNX(connection_config=config)
        try:
            await xknx.start()
        except Exception as e:
            raise AdapterError(
                f"No se pudo abrir el tunel KNX contra {self._s.knx_gateway_ip}: {e}. "
                "Comprueba que el servidor ONNA acepta tuneles KNXnet/IP y que "
                "no ha agotado sus conexiones simultaneas."
            ) from e
        self._xknx = xknx
        return xknx

    async def cerrar(self) -> None:
        if self._xknx is not None:
            await self._xknx.stop()
            self._xknx = None

    def _resolver(self, nombre_o_direccion: str) -> KNXGroupAddress:
        ga = self._inv.knx_por_nombre(nombre_o_direccion)
        if ga is None:
            conocidas = ", ".join(f"{g.nombre} ({g.direccion})" for g in self._inv.knx) or "ninguna"
            raise AdapterError(
                f"La direccion de grupo '{nombre_o_direccion}' no esta declarada en "
                f"config/config.yaml. Por seguridad solo se permiten las declaradas. "
                f"Conocidas: {conocidas}."
            )
        return ga

    def listar(self) -> list[dict[str, Any]]:
        return [
            {
                "nombre": g.nombre,
                "direccion": g.direccion,
                "tipo": g.tipo_valor,
                "descripcion": g.descripcion,
                "solo_lectura": g.solo_lectura,
            }
            for g in self._inv.knx
        ]

    async def leer(self, nombre_o_direccion: str) -> dict[str, Any]:
        ga = self._resolver(nombre_o_direccion)
        from xknx.tools import read_group_value

        async with self._lock:
            xknx = await self._conectar()
            try:
                valor = await read_group_value(xknx, ga.direccion, value_type=ga.tipo_valor)
            except Exception as e:
                raise AdapterError(f"Error leyendo {ga.direccion} del bus KNX: {e}") from e
        if valor is None:
            raise AdapterError(
                f"Ningun dispositivo respondio a la lectura de {ga.direccion}. "
                "Puede que esa direccion no tenga bandera de lectura activada en ETS."
            )
        return {"nombre": ga.nombre, "direccion": ga.direccion, "valor": valor}

    async def escribir(self, nombre_o_direccion: str, valor: Any) -> dict[str, Any]:
        ga = self._resolver(nombre_o_direccion)
        if ga.solo_lectura:
            raise AdapterError(f"'{ga.nombre}' esta marcada como solo lectura en el inventario.")
        from xknx.tools import group_value_write

        async with self._lock:
            xknx = await self._conectar()
            try:
                await group_value_write(xknx, ga.direccion, valor, value_type=ga.tipo_valor)
            except Exception as e:
                raise AdapterError(f"Error escribiendo {ga.direccion} en el bus KNX: {e}") from e
        return {
            "nombre": ga.nombre,
            "direccion": ga.direccion,
            "valor_escrito": valor,
            "detalle": "Telegrama enviado al bus. KNX no confirma ejecucion: "
            "verifica el estado si necesitas certeza.",
        }
