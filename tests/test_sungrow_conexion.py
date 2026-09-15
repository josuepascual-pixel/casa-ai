"""Capa de conexion Modbus del inversor Sungrow.

Es la parte que va a fallar en el despliegue real, porque el dongle WiNet-S
solo acepta UNA conexion simultanea: si Home Assistant o iSolarCloud la tienen
abierta, este cliente no entra. Todo eso estaba sin cubrir.
"""

from __future__ import annotations

import asyncio

import pytest

from casa_ai.adapters.base import AdapterError, NoConfigurado
from casa_ai.adapters.modbus import ConexionModbus
from casa_ai.adapters.sungrow import REG_ESTADO_BATERIA, Sungrow
from casa_ai.settings import Settings


class RespuestaFalsa:
    def __init__(self, registros: list[int] | None = None, error: bool = False) -> None:
        self.registers = registros or []
        self._error = error

    def isError(self) -> bool:  # noqa: N802 - es la API de pymodbus
        return self._error

    def __repr__(self) -> str:
        return "ModbusIOException" if self._error else "ok"


class ClienteFalso:
    """Doble de AsyncModbusTcpClient con la firma de pymodbus 3.9+."""

    def __init__(self, host: str, port: int = 502, timeout: int = 8) -> None:
        self.host = host
        self.port = port
        self.connected = False
        self.conectar_devuelve = True
        self.cerrado = 0
        self.lecturas: list[tuple[str, int, int, int]] = []
        self.escrituras: list[tuple[int, int, int]] = []
        self.respuesta = RespuestaFalsa([0] * 60)
        self.lanzar: Exception | None = None

    async def connect(self) -> bool:
        self.connected = self.conectar_devuelve
        return self.conectar_devuelve

    def close(self) -> None:
        self.cerrado += 1
        self.connected = False

    async def read_input_registers(
        self, address: int, *, count: int = 1, device_id: int = 1
    ) -> RespuestaFalsa:
        if self.lanzar:
            raise self.lanzar
        self.lecturas.append(("input", address, count, device_id))
        return self.respuesta

    async def read_holding_registers(
        self, address: int, *, count: int = 1, device_id: int = 1
    ) -> RespuestaFalsa:
        self.lecturas.append(("holding", address, count, device_id))
        return self.respuesta

    async def write_register(
        self, address: int, value: int, *, device_id: int = 1
    ) -> RespuestaFalsa:
        if self.lanzar:
            raise self.lanzar
        self.escrituras.append((address, value, device_id))
        return self.respuesta


@pytest.fixture
def inversor(settings: Settings, monkeypatch) -> Sungrow:
    """Un Sungrow que habla con un cliente falso en vez de con el dongle."""
    creados: list[ClienteFalso] = []

    def fabrica(host: str, port: int = 502, timeout: int = 8) -> ClienteFalso:
        cliente = ClienteFalso(host, port, timeout)
        creados.append(cliente)
        return cliente

    import pymodbus.client

    monkeypatch.setattr(pymodbus.client, "AsyncModbusTcpClient", fabrica)
    inv = Sungrow(settings)
    inv.creados = creados  # type: ignore[attr-defined]
    return inv


# --- Conexion ---------------------------------------------------------------


async def test_sin_host_lo_dice(settings: Settings) -> None:
    sin = Sungrow(settings.model_copy(update={"sungrow_host": None}))
    assert sin.configurado is False
    with pytest.raises(NoConfigurado, match="SUNGROW_HOST"):
        await sin.estado(usar_cache=False)


async def test_si_el_dongle_esta_ocupado_lo_explica(inversor: Sungrow) -> None:
    """Es EL error que se va a encontrar en la instalacion real, asi que el
    mensaje tiene que nombrar la causa, no solo decir que fallo."""
    def fabrica_ocupada(host: str, port: int = 502, timeout: int = 8) -> ClienteFalso:
        cliente = ClienteFalso(host, port, timeout)
        cliente.conectar_devuelve = False
        return cliente

    import pymodbus.client

    import casa_ai.adapters.sungrow  # noqa: F401

    original = pymodbus.client.AsyncModbusTcpClient
    pymodbus.client.AsyncModbusTcpClient = fabrica_ocupada  # type: ignore[assignment]
    try:
        with pytest.raises(AdapterError, match="una conexion a la vez"):
            await inversor.estado(usar_cache=False)
    finally:
        pymodbus.client.AsyncModbusTcpClient = original  # type: ignore[assignment]

    # Y no se queda con un cliente inservible guardado.
    assert inversor._conexion._cliente is None


async def test_la_conexion_se_reutiliza(inversor: Sungrow) -> None:
    await inversor.estado(usar_cache=False)
    await inversor.estado(usar_cache=False)

    # Un solo cliente para las dos lecturas: el dongle no aguanta mas.
    assert len(inversor.creados) == 1  # type: ignore[attr-defined]
    await inversor.cerrar()


async def test_se_reconecta_si_la_conexion_se_cayo(inversor: Sungrow) -> None:
    await inversor.estado(usar_cache=False)
    inversor.creados[0].connected = False  # type: ignore[attr-defined]

    await inversor.estado(usar_cache=False)

    assert len(inversor.creados) == 2  # type: ignore[attr-defined]
    await inversor.cerrar()


async def test_cerrar_es_idempotente(inversor: Sungrow) -> None:
    await inversor.estado(usar_cache=False)
    await inversor.cerrar()
    await inversor.cerrar()  # no debe lanzar

    assert inversor.creados[0].cerrado == 1  # type: ignore[attr-defined]
    assert inversor._conexion._cliente is None


# --- Lectura y escritura ----------------------------------------------------


async def test_lee_registros_de_entrada_y_de_mantenimiento(inversor: Sungrow) -> None:
    await inversor._leer(5016, 2)
    await inversor._leer(13049, 1, mantenimiento=True)

    tipos = [t for t, *_ in inversor.creados[0].lecturas]  # type: ignore[attr-defined]
    assert tipos == ["input", "holding"]


async def test_el_slave_id_llega_al_cliente(settings: Settings, monkeypatch) -> None:
    creados: list[ClienteFalso] = []

    def fabrica(host: str, port: int = 502, timeout: int = 8) -> ClienteFalso:
        c = ClienteFalso(host, port, timeout)
        creados.append(c)
        return c

    import pymodbus.client

    monkeypatch.setattr(pymodbus.client, "AsyncModbusTcpClient", fabrica)
    inv = Sungrow(settings.model_copy(update={"sungrow_slave_id": 3}))

    await inv._leer(5016, 2)

    assert creados[0].lecturas[0][3] == 3
    await inv.cerrar()


async def test_un_error_del_inversor_se_explica(inversor: Sungrow) -> None:
    await inversor._leer(5016, 2)  # fuerza la conexion
    inversor.creados[0].respuesta = RespuestaFalsa(error=True)  # type: ignore[attr-defined]

    with pytest.raises(AdapterError, match="devolvio error leyendo 5016"):
        await inversor._leer(5016, 2)
    await inversor.cerrar()


async def test_un_fallo_de_red_cierra_la_conexion(inversor: Sungrow) -> None:
    """Si no se cierra, la siguiente lectura reutiliza un socket muerto y falla
    igual para siempre."""
    await inversor._leer(5016, 2)
    inversor.creados[0].lanzar = OSError("connection reset")  # type: ignore[attr-defined]

    with pytest.raises(AdapterError, match="connection reset"):
        await inversor._leer(5016, 2)

    assert inversor._conexion._cliente is None
    assert inversor.creados[0].cerrado == 1  # type: ignore[attr-defined]


async def test_escribir_pasa_direccion_y_valor(inversor: Sungrow) -> None:
    await inversor._escribir(13050, 0xAA)
    assert inversor.creados[0].escrituras == [(13050, 0xAA, 1)]  # type: ignore[attr-defined]
    await inversor.cerrar()


async def test_un_rechazo_de_escritura_se_explica(inversor: Sungrow) -> None:
    await inversor._escribir(13050, 0xAA)
    inversor.creados[0].respuesta = RespuestaFalsa(error=True)  # type: ignore[attr-defined]

    with pytest.raises(AdapterError, match="devolvio error escribiendo 170 en 13050"):
        await inversor._escribir(13050, 0xAA)
    await inversor.cerrar()


async def test_un_fallo_escribiendo_tambien_cierra(inversor: Sungrow) -> None:
    await inversor._escribir(13050, 0xAA)
    inversor.creados[0].lanzar = OSError("broken pipe")  # type: ignore[attr-defined]

    with pytest.raises(AdapterError, match="broken pipe"):
        await inversor._escribir(13050, 0xAA)

    assert inversor._conexion._cliente is None


# --- Compatibilidad de pymodbus --------------------------------------------


def test_detecta_device_id_o_slave() -> None:
    """pymodbus renombro `slave` a `device_id` en 3.9 y el codigo tiene que
    funcionar con las dos versiones."""
    async def nueva(address: int, *, count: int = 1, device_id: int = 1): ...
    async def vieja(address: int, *, count: int = 1, slave: int = 1): ...

    assert ConexionModbus._kwarg_id(nueva) == "device_id"
    assert ConexionModbus._kwarg_id(vieja) == "slave"


def test_con_una_firma_ilegible_cae_a_slave() -> None:
    """Un builtin sin firma introspectable no debe tumbar la lectura."""
    assert ConexionModbus._kwarg_id(print) in ("slave", "device_id")


# --- Cache y serializacion --------------------------------------------------


async def test_la_cache_evita_machacar_el_dongle(inversor: Sungrow) -> None:
    await inversor.estado()
    await inversor.estado()
    await inversor.estado()

    # Dos lecturas por estado (el bloque de PV y el de bateria), una sola vez.
    assert len(inversor.creados[0].lecturas) == 2  # type: ignore[attr-defined]
    await inversor.cerrar()


async def test_usar_cache_false_fuerza_la_lectura(inversor: Sungrow) -> None:
    await inversor.estado()
    await inversor.estado(usar_cache=False)

    assert len(inversor.creados[0].lecturas) == 4  # type: ignore[attr-defined]
    await inversor.cerrar()


async def test_un_cambio_de_modo_invalida_la_cache(inversor: Sungrow) -> None:
    await inversor.estado()
    await inversor.fijar_modo_bateria("autoconsumo")

    assert inversor._cache is None
    await inversor.cerrar()


async def test_las_lecturas_concurrentes_no_se_pisan(inversor: Sungrow) -> None:
    """El WiNet-S admite una conexion: si dos turnos del agente preguntan a la
    vez, el lock tiene que serializarlos."""
    resultados = await asyncio.gather(*(inversor.estado(usar_cache=False)
                                        for _ in range(5)))

    assert len(resultados) == 5
    assert len(inversor.creados) == 1  # type: ignore[attr-defined]
    # Las lecturas salieron en pares ordenados, sin entrelazarse.
    direcciones = [d for _, d, *_ in inversor.creados[0].lecturas]  # type: ignore[attr-defined]
    assert direcciones == [5016, REG_ESTADO_BATERIA] * 5
    await inversor.cerrar()
