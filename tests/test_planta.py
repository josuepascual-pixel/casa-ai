"""La planta comercial: tres equipos por el Logger1000 y un mapa que es datos.

Lo delicado: el orden de palabras cambia entre Sungrow y Janitza, los signos
de red y bateria tienen convencion propia, y una bateria sin declarar no puede
salir como bateria vacia.
"""

from __future__ import annotations

import struct
from typing import Any

import pytest

from casa_ai.adapters.base import AdapterError
from casa_ai.adapters.modbus import ConexionModbus, ErrorConexion, ErrorEquipo
from casa_ai.adapters.planta import (
    MAX_BLOQUE,
    MAX_HUECO,
    SENALES_CONTADOR,
    SENALES_INVERSOR,
    PlantaSungrow,
    bloques,
    decodificar,
)
from casa_ai.app import Aplicacion
from casa_ai.settings import Inventario, Planta, Senal, Settings

from .dobles import adaptador

# --- Decodificado -----------------------------------------------------------


def _f32_alta(valor: float) -> list[int]:
    entero = struct.unpack(">I", struct.pack(">f", valor))[0]
    return [entero >> 16, entero & 0xFFFF]


def test_decodifica_cada_tipo_con_su_orden_y_escala() -> None:
    assert decodificar(Senal(registro=0), [412]) == 412
    assert decodificar(Senal(registro=0, tipo="i16", escala=0.1), [0xFFF6]) == pytest.approx(-1.0)
    # Sungrow: palabra baja primero.
    assert decodificar(Senal(registro=0, tipo="u32"), [0x1234, 0x0001]) == 0x00011234
    assert decodificar(Senal(registro=0, tipo="i32"), [0xFFFF, 0xFFFF]) == -1
    # Janitza: float con la alta primero, y la escala convierte Wh en kWh.
    senal = Senal(registro=0, tipo="f32", palabra_alta_primero=True, escala=0.001)
    assert decodificar(senal, _f32_alta(11_100_000.0)) == pytest.approx(11_100.0)


def test_los_bloques_agrupan_lecturas_contiguas() -> None:
    """Doce lecturas sueltas atragantan al registrador; tres bloques no."""
    inversor = bloques(SENALES_INVERSOR)
    assert inversor == [(False, 4999, 39), (False, 5114, 2)]
    assert bloques(SENALES_CONTADOR) == [(True, 19000, 86)]

    lejos = {"a": Senal(registro=0), "b": Senal(registro=MAX_HUECO + 2)}
    assert len(bloques(lejos)) == 2
    largo = {"a": Senal(registro=0), "b": Senal(registro=MAX_BLOQUE)}
    assert len(bloques(largo)) == 2


# --- Estado compuesto -------------------------------------------------------

UNIT_INVERSOR, UNIT_BATERIA, UNIT_CONTADOR = 1, 2, 3

SENALES_BATERIA_YAML = {
    "potencia_w": {"registro": 100, "tipo": "i32"},
    "soc_pct": {"registro": 102, "escala": 0.1},
    "soh_pct": {"registro": 103, "escala": 0.1},
}


def _planta_completa(**extra: Any) -> Planta:
    return Planta.model_validate(
        {
            "inversor": {"unit": UNIT_INVERSOR},
            "contador": {"unit": UNIT_CONTADOR},
            "bateria": {"unit": UNIT_BATERIA, "senales": SENALES_BATERIA_YAML},
            **extra,
        }
    )


def _u32_baja(valor: int) -> list[int]:
    valor &= 0xFFFFFFFF
    return [valor & 0xFFFF, valor >> 16]


class Registros:
    """Los registros de cada equipo, por (mantenimiento, unit, direccion)."""

    def __init__(self) -> None:
        self.valores: dict[tuple[bool, int, int], int] = {}
        self.lecturas: list[tuple[int, int, int]] = []
        self.hosts: list[str | None] = []

    def poner(self, unit: int, direccion: int, regs: list[int], *, mantenimiento: bool = False):
        for i, v in enumerate(regs):
            self.valores[(mantenimiento, unit, direccion + i)] = v


@pytest.fixture
def registros(monkeypatch) -> Registros:
    r = Registros()
    # Un dia de sol a mediodia: 41,2 kW de sol, 25,2 kW a la bateria al 88 %,
    # 6,4 kW a la red (el contador lo da negativo: positivo es importar).
    r.poner(UNIT_INVERSOR, 5030, _u32_baja(41_200))
    r.poner(UNIT_INVERSOR, 5003, _u32_baja(30_800))
    r.poner(UNIT_INVERSOR, 5037, [0x8100])
    r.poner(UNIT_BATERIA, 100, _u32_baja(25_200))
    r.poner(UNIT_BATERIA, 102, [880, 1000])
    r.poner(UNIT_CONTADOR, 19026, _f32_alta(-6_400.0), mantenimiento=True)
    r.poner(UNIT_CONTADOR, 19076, _f32_alta(11_100_000.0), mantenimiento=True)
    r.poner(UNIT_CONTADOR, 19084, _f32_alta(7_430_000.0), mantenimiento=True)

    async def leer(self, direccion, cantidad, *, mantenimiento=False, unit=None):
        r.hosts.append(self.host)
        r.lecturas.append((unit, direccion, cantidad))
        return [r.valores.get((mantenimiento, unit, direccion + i), 0) for i in range(cantidad)]

    monkeypatch.setattr(ConexionModbus, "leer", leer)
    return r


async def test_compone_el_estado_con_los_tres_equipos(settings: Settings, registros) -> None:
    planta = PlantaSungrow(settings, _planta_completa())
    estado = await planta.estado(usar_cache=False)

    assert estado.pv_w == 41_200
    assert estado.bateria_w == 25_200 and estado.bateria_soc == pytest.approx(88.0)
    assert estado.bateria_soh == pytest.approx(100.0)
    assert estado.red_w == 6_400 and estado.resumen()["red_estado"] == "exportando"
    assert estado.consumo_casa_w == 41_200 - 25_200 - 6_400
    assert estado.pv_total_kwh == 30_800
    assert estado.import_total_kwh == pytest.approx(11_100.0)
    assert estado.export_total_kwh == pytest.approx(7_430.0)
    # Cada equipo se lee por su id, y el inversor en dos bloques, no en veinte.
    assert {u for u, _, _ in registros.lecturas} == {1, 2, 3}
    assert sum(1 for u, _, _ in registros.lecturas if u == UNIT_INVERSOR) == 2


async def test_lo_que_no_consume_el_estado_sale_en_crudo(settings: Settings, registros) -> None:
    """Asi se casa un registro candidato contra iSolarCloud sin tocar codigo."""
    planta = _planta_completa()
    planta.bateria.senales["candidato_tension"] = Senal(registro=104, escala=0.1)  # type: ignore[union-attr]
    registros.poner(UNIT_BATERIA, 104, [7_650])

    estado = await PlantaSungrow(settings, planta).estado(usar_cache=False)

    assert estado.crudo["bateria"]["candidato_tension"] == pytest.approx(765.0)
    assert estado.crudo["inversor"]["estado"] == "funcionando limitado"
    assert "potencia_w" not in estado.crudo["inversor"]


async def test_los_signos_se_invierten_por_configuracion(settings: Settings, registros) -> None:
    planta = _planta_completa(invertir_signo_red=True, invertir_signo_bateria=True)
    estado = await PlantaSungrow(settings, planta).estado(usar_cache=False)
    assert estado.red_w == -6_400 and estado.bateria_w == -25_200


async def test_sin_bateria_declarada_no_sale_vacia(settings: Settings, registros) -> None:
    planta = Planta.model_validate({"contador": {"unit": UNIT_CONTADOR}})
    estado = await PlantaSungrow(settings, planta).estado(usar_cache=False)

    assert estado.bateria_soc is None and estado.bateria_w == 0
    assert "aviso" in estado.resumen()
    assert "planta.bateria" in estado.crudo["aviso_bateria"]


async def test_bateria_declarada_sin_senales_dice_que_faltan(settings: Settings, registros) -> None:
    """Es el primer paso documentado (solo `unit`), y el aviso tiene que
    apuntar al arreglo de verdad, no decir que no esta declarada."""
    planta = Planta.model_validate(
        {"contador": {"unit": UNIT_CONTADOR}, "bateria": {"unit": UNIT_BATERIA}}
    )
    estado = await PlantaSungrow(settings, planta).estado(usar_cache=False)
    assert "sin la senal `potencia_w`" in estado.crudo["aviso_bateria"]
    assert estado.bateria_soc is None


async def test_lecturas_y_escrituras_comparten_el_lock_del_socket(
    settings: Settings, registros, monkeypatch
) -> None:
    """Una lectura de la vigilancia y una orden confirmada no pueden cruzarse
    en el mismo socket: las dos van bajo el lock de la conexion."""
    planta = PlantaSungrow(settings, _con_ordenes())
    conexion = await planta._conexion(planta._planta.bateria)  # type: ignore[arg-type]
    tomado: list[bool] = []
    leer_falso = ConexionModbus.leer

    async def leer_mirando(self, *args, **kw):
        tomado.append(self.lock.locked())
        return await leer_falso(self, *args, **kw)

    monkeypatch.setattr(ConexionModbus, "leer", leer_mirando)
    await planta.estado(usar_cache=False)
    assert tomado and all(tomado)
    assert conexion.lock is (await planta._conexion(planta._planta.inversor)).lock


async def test_sin_contador_lo_dice_en_vez_de_inventar_la_red(settings: Settings, registros):
    planta = PlantaSungrow(settings, Planta())
    with pytest.raises(AdapterError, match="planta.contador"):
        await planta.estado(usar_cache=False)
    # El inversor solo si se puede leer, que es lo que hace el sondeo.
    assert (await planta.leer_todo())["inversor"]["potencia_w"] == 41_200


async def test_la_cache_evita_releer(settings: Settings, registros) -> None:
    planta = PlantaSungrow(settings, _planta_completa())
    await planta.estado()
    n = len(registros.lecturas)
    await planta.estado()
    assert len(registros.lecturas) == n


# --- Control ----------------------------------------------------------------


async def test_sin_ordenes_solo_se_lee(settings: Settings) -> None:
    planta = PlantaSungrow(settings, _planta_completa())
    assert planta.puede_controlar is False
    with pytest.raises(AdapterError, match="solo se lee"):
        await planta.fijar_modo_bateria("cargar", 1000)


@pytest.fixture
def escrituras(monkeypatch) -> list[tuple[int, int, int | None]]:
    hechas: list[tuple[int, int, int | None]] = []

    async def escribir(self, direccion, valor, *, unit=None):
        hechas.append((direccion, valor, unit))

    monkeypatch.setattr(ConexionModbus, "escribir", escribir)
    return hechas


def _con_ordenes() -> Planta:
    return _planta_completa(
        bateria={
            "unit": UNIT_BATERIA,
            "senales": SENALES_BATERIA_YAML,
            "ordenes": {
                "autoconsumo": [{"registro": 200, "valor": 0}],
                "cargar": [
                    {"registro": 200, "valor": 2},
                    {"registro": 201, "potencia_escala": 0.001},
                ],
            },
        }
    )


async def test_las_ordenes_escriben_lo_declarado(settings: Settings, escrituras, registros) -> None:
    planta = PlantaSungrow(settings, _con_ordenes())
    assert planta.puede_controlar is True
    await planta.estado()

    r = await planta.fijar_modo_bateria("cargar", 4_000)

    assert escrituras == [(200, 2, UNIT_BATERIA), (201, 4, UNIT_BATERIA)]  # 4 kW
    assert r["potencia_w"] == 4_000 and "autoconsumo" in r["detalle"]
    # La cache se tira: el estado siguiente tiene que verse cambiado.
    n = len(registros.lecturas)
    await planta.estado()
    assert len(registros.lecturas) > n


async def test_las_ordenes_respetan_el_tope_y_los_modos(settings: Settings, escrituras) -> None:
    planta = PlantaSungrow(settings, _con_ordenes())
    with pytest.raises(AdapterError, match="tope de seguridad"):
        await planta.fijar_modo_bateria("cargar", 999_999)
    with pytest.raises(AdapterError, match="no esta declarado.*autoconsumo, cargar"):
        await planta.fijar_modo_bateria("parar")
    assert escrituras == []

    await planta.fijar_modo_bateria("autoconsumo")
    assert escrituras == [(200, 0, UNIT_BATERIA)]


# --- Donde esta el registrador ---------------------------------------------


async def test_sin_host_busca_la_ip_por_mac_y_la_vuelve_a_buscar_si_falla(
    settings: Settings, registros, monkeypatch
) -> None:
    """El router de Vodafone no deja reservar IP: si el Logger cambia de
    direccion, la lectura falla una vez, se pregunta a UniFi y se reintenta."""
    ips = iter(["192.168.0.130", "192.168.0.131"])
    pedidas: list[str | None] = []

    async def resolver(mac: str | None) -> list[str]:
        pedidas.append(mac)
        return [next(ips)]

    sin_host = settings.model_copy(
        update={"sungrow_host": None, "sungrow_mac": "ac:19:9f:00:00:01"}
    )
    planta = PlantaSungrow(sin_host, _planta_completa(), buscar_ip=resolver)
    assert planta.configurado is True
    assert PlantaSungrow(sin_host, _planta_completa()).configurado is False

    await planta.estado(usar_cache=False)
    assert registros.hosts[0] == "192.168.0.130" and pedidas == ["ac:19:9f:00:00:01"]

    fallos = {"n": 0}
    leer_bien = ConexionModbus.leer

    async def leer_o_falla(self, *args, **kw):
        if fallos["n"] == 0:
            fallos["n"] += 1
            raise ErrorConexion("Error leyendo 4999: timeout")
        return await leer_bien(self, *args, **kw)

    monkeypatch.setattr(ConexionModbus, "leer", leer_o_falla)
    await planta.estado(usar_cache=False)
    assert pedidas == ["ac:19:9f:00:00:01"] * 2
    assert registros.hosts[-1] == "192.168.0.131"


async def test_si_unifi_no_conoce_la_mac_lo_dice(settings: Settings) -> None:
    async def nadie(mac: str | None) -> list[str]:
        return []

    sin_host = settings.model_copy(update={"sungrow_host": None, "sungrow_mac": "aa:bb"})
    planta = PlantaSungrow(sin_host, _planta_completa(), buscar_ip=nadie)
    with pytest.raises(AdapterError, match="ningun cliente con la MAC aa:bb"):
        await planta.estado(usar_cache=False)


async def test_sin_ip_ni_mac_prueba_los_equipos_sungrow_de_la_red(
    settings: Settings, registros, monkeypatch
) -> None:
    """Hay dos equipos Sungrow en la red (el registrador y el controlador de la
    bateria) y no hace falta saber cual es cual: se prueba a leer el tipo de
    inversor en cada uno y se queda con el que contesta algo distinto de cero."""
    async def todos(mac: str | None) -> list[str]:
        assert mac is None
        return ["192.168.0.146", "192.168.0.130"]

    leer_falso = ConexionModbus.leer

    async def leer_segun_ip(self, direccion, cantidad, *, mantenimiento=False, unit=None):
        if self.host == "192.168.0.146":
            raise ErrorConexion("Error leyendo 4999: timeout")
        if direccion == 4999 and cantidad == 1:
            return [0x2C0B]
        return await leer_falso(self, direccion, cantidad, mantenimiento=mantenimiento, unit=unit)

    monkeypatch.setattr(ConexionModbus, "leer", leer_segun_ip)
    sin_nada = settings.model_copy(update={"sungrow_host": None, "sungrow_mac": None})
    planta = PlantaSungrow(sin_nada, _planta_completa(), buscar_ip=todos)
    assert planta.configurado

    estado = await planta.estado(usar_cache=False)

    assert estado.pv_w == 41_200
    assert await planta._host_logger() == "192.168.0.130"


async def test_si_ningun_sungrow_contesta_lo_dice(settings: Settings, monkeypatch) -> None:
    async def todos(mac: str | None) -> list[str]:
        return ["192.168.0.146", "192.168.0.130"]

    async def nunca(self, *args, **kw):
        raise ErrorConexion("Error leyendo 4999: timeout")

    monkeypatch.setattr(ConexionModbus, "leer", nunca)
    sin_nada = settings.model_copy(update={"sungrow_host": None, "sungrow_mac": None})
    planta = PlantaSungrow(sin_nada, _planta_completa(), buscar_ip=todos)
    with pytest.raises(AdapterError, match="Activa Modbus TCP"):
        await planta.estado(usar_cache=False)


# --- El elector -------------------------------------------------------------


def _app(settings: Settings, inventario: Inventario) -> Aplicacion:
    app = Aplicacion.__new__(Aplicacion)
    app.settings = settings
    app.inventario = inventario
    return app


def test_con_planta_declarada_manda_la_planta(settings: Settings) -> None:
    con = _app(settings, Inventario.model_validate({"planta": {}}))
    assert isinstance(con._fuente_modbus(adaptador(False)), PlantaSungrow)
    sin = _app(settings, Inventario())
    assert type(sin._fuente_modbus(adaptador(False))).__name__ == "Sungrow"


async def test_la_ip_por_mac_sale_de_los_clientes_de_unifi(settings: Settings) -> None:
    async def clientes(texto: str | None = None, limite: int = 50):
        return [
            {"mac": "ac:19:9f:00:00:01", "ip": "192.168.0.130"},
            {"mac": "e4:5f:01:8f:de:77", "ip": "192.168.0.100"},
            {"mac": "ac:19:9f:00:00:02", "ip": "192.168.0.146"},
        ]

    unifi = adaptador(True, clientes=lambda _self, **kw: clientes(**kw))
    app = _app(
        settings.model_copy(update={"sungrow_host": None, "sungrow_mac": "AC:19:9F:00:00:02"}),
        Inventario.model_validate({"planta": {}}),
    )
    planta = app._fuente_modbus(unifi)
    assert isinstance(planta, PlantaSungrow) and planta.configurado
    assert await planta._host_logger() == "192.168.0.146"

    # Sin MAC: todos los de Sungrow, por el prefijo de fabricante.
    sin_nada = _app(
        settings.model_copy(update={"sungrow_host": None, "sungrow_mac": None}),
        Inventario.model_validate({"planta": {}}),
    )
    planta = sin_nada._fuente_modbus(unifi)
    assert await planta._buscar_ip(None) == ["192.168.0.130", "192.168.0.146"]  # type: ignore[misc]


async def test_un_error_del_equipo_no_hace_buscar_otra_ip(settings: Settings, monkeypatch) -> None:
    """Un registro mal declarado devuelve excepcion Modbus en cada lectura; si
    eso disparase la busqueda por MAC, cada estado() cerraria los sockets y
    pediria a UniFi la tabla de clientes para volver a fallar igual."""
    pedidas: list[str | None] = []

    async def resolver(mac: str | None) -> list[str]:
        pedidas.append(mac)
        return ["192.168.0.130"]

    async def leer_mal(self, *args, **kw):
        raise ErrorEquipo("El equipo devolvio error leyendo 100 (x2): IllegalAddress")

    monkeypatch.setattr(ConexionModbus, "leer", leer_mal)
    sin_host = settings.model_copy(update={"sungrow_host": None, "sungrow_mac": "aa:bb"})
    planta = PlantaSungrow(sin_host, _planta_completa(), buscar_ip=resolver)
    with pytest.raises(ErrorEquipo):
        await planta.estado(usar_cache=False)
    assert pedidas == ["aa:bb"]


async def test_el_sondeo_devuelve_texto_para_el_movil(settings: Settings, monkeypatch) -> None:
    from casa_ai.adapters.planta import orden_de_sondeo

    async def leer(self, direccion, cantidad, *, mantenimiento=False, unit=None):
        if unit == 1:
            return [0x2C0B, 500]
        if unit == 3:
            raise ErrorEquipo("El equipo devolvio error leyendo 4999 (x2): IllegalFunction")
        raise ErrorConexion("Error leyendo 4999 (x2): timeout")

    monkeypatch.setattr(ConexionModbus, "leer", leer)
    planta = PlantaSungrow(settings, _planta_completa())

    texto = await orden_de_sondeo(planta, "sondear 3")
    assert "id 1: responde. tipo 0x2C0B, nominal 50.0 kW" in texto
    assert "id 2: sin respuesta" in texto and "id 3: El equipo devolvio error" in texto

    ayuda = await orden_de_sondeo(planta, "barrer x")
    assert "Ordenes:" in ayuda
