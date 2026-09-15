"""Una conexion Modbus TCP con lo que todos los equipos necesitan igual.

Estaba dentro del adaptador del inversor hibrido: conectar perezosamente,
serializar el acceso, poner el id de esclavo con el nombre de kwarg que toque
en cada version de pymodbus, y tirar el socket cuando algo falla. La planta
comercial habla con tres equipos por el mismo registrador y no tenia sentido
copiarlo tres veces.

Decodificado: Sungrow manda los enteros de 32 bits con la palabra baja
primero; Janitza (el contador) manda los float IEEE con la alta primero. Los
dos ordenes estan aqui con el nombre que dice cual es.
"""

from __future__ import annotations

import asyncio
import inspect
import struct
from collections.abc import Callable
from typing import Any

from .base import AdapterError, NoConfigurado


def u32_baja_primero(regs: list[int]) -> int:
    return (regs[1] << 16) | regs[0]


def u32_alta_primero(regs: list[int]) -> int:
    return (regs[0] << 16) | regs[1]


def i32_de_u32(v: int) -> int:
    return v - 0x100000000 if v >= 0x80000000 else v


def i16(v: int) -> int:
    return v - 0x10000 if v >= 0x8000 else v


def u32(regs: list[int], *, alta_primero: bool) -> int:
    return u32_alta_primero(regs) if alta_primero else u32_baja_primero(regs)


def f32(regs: list[int], *, alta_primero: bool) -> float:
    return struct.unpack(">f", struct.pack(">I", u32(regs, alta_primero=alta_primero)))[0]


class ErrorConexion(AdapterError):
    """El socket ha fallado (timeout, reset): merece reconectar o buscar otra IP."""


class ErrorEquipo(AdapterError):
    """El equipo ha respondido con una excepcion Modbus: la peticion esta mal
    (direccion ilegal, id sin equipo detras), y repetirla da lo mismo."""


class ConexionModbus:
    """Un socket Modbus TCP contra un host, compartido por varios ids de esclavo."""

    def __init__(
        self,
        host: str | None,
        puerto: int,
        unit: int | None = None,
        *,
        sin_host: str,
        no_conecta: str,
        timeout: int = 8,
    ) -> None:
        self.host = host
        self.puerto = puerto
        self.unit = unit
        self._sin_host = sin_host
        self._no_conecta = no_conecta
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._cliente: Any = None

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    async def _conectar(self) -> Any:
        if not self.host:
            raise NoConfigurado(self._sin_host)
        if self._cliente is not None and getattr(self._cliente, "connected", False):
            return self._cliente
        try:
            from pymodbus.client import AsyncModbusTcpClient
        except ImportError as e:  # pragma: no cover
            raise AdapterError("Falta la dependencia pymodbus (pip install pymodbus)") from e

        self._cliente = AsyncModbusTcpClient(self.host, port=self.puerto, timeout=self._timeout)
        if not await self._cliente.connect():
            self._cliente = None
            raise AdapterError(
                f"No se pudo abrir Modbus TCP contra {self.host}:{self.puerto}. "
                + self._no_conecta
            )
        return self._cliente

    async def cerrar(self) -> None:
        if self._cliente is not None:
            self._cliente.close()
            self._cliente = None

    @staticmethod
    def _kwarg_id(fn: Callable[..., Any]) -> str:
        """pymodbus renombro `slave` a `device_id` en 3.9. Detectamos cual toca."""
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):  # pragma: no cover
            return "slave"
        return "device_id" if "device_id" in params else "slave"

    async def _operar(
        self, operacion: str, gerundio: str, unit: int | None, *args: Any, **kw: Any
    ) -> Any:
        if unit is None:
            unit = self.unit
        if unit is None:
            raise AdapterError(f"Falta el id de esclavo para {gerundio}.")
        cliente = await self._conectar()
        fn = getattr(cliente, operacion)
        try:
            resp = await fn(*args, **kw, **{self._kwarg_id(fn): unit})
        except Exception as e:
            # La conexion se descarta: un socket que ha fallado una vez falla
            # igual para siempre si se reutiliza.
            await self.cerrar()
            raise ErrorConexion(f"Error {gerundio}: {e}") from e
        if resp.isError():
            raise ErrorEquipo(f"El equipo devolvio error {gerundio}: {resp}")
        return resp

    async def leer(
        self,
        direccion: int,
        cantidad: int,
        *,
        mantenimiento: bool = False,
        unit: int | None = None,
    ) -> list[int]:
        operacion = "read_holding_registers" if mantenimiento else "read_input_registers"
        resp = await self._operar(
            operacion, f"leyendo {direccion} (x{cantidad})", unit, direccion, count=cantidad
        )
        return list(resp.registers)

    async def escribir(self, direccion: int, valor: int, *, unit: int | None = None) -> None:
        await self._operar(
            "write_register", f"escribiendo {valor} en {direccion}", unit, direccion, valor
        )
