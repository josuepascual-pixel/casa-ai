"""Planta comercial Sungrow por el registrador Logger1000.

La instalacion real no es un inversor hibrido con la bateria colgando: es un
inversor de cadena (SG-CX), un sistema de baterias ST con su propio
controlador y un contador Janitza, los tres detras de un Logger1000 que habla
Modbus TCP y reparte cada peticion por su id de esclavo. Cada uno tiene su
mapa de registros, y solo el del inversor es publico.

Por eso el mapa es DATOS, no codigo: el inversor viene con el suyo de serie,
el contador con el de Janitza pendiente de contrastar, y la bateria vacio
hasta que el instalador de el documento o se casen los registros contra
iSolarCloud con el sondeo:

    python -m casa_ai.adapters.planta sondear      # que ids responden
    python -m casa_ai.adapters.planta leer         # lo que se lee ahora
    python -m casa_ai.adapters.planta barrer 3 0 100   # registros crudos

Un registro que se declara en `config.yaml` con un nombre nuevo se lee y sale
en `crudo`: asi se comprueba un candidato sin tocar codigo.

El control de la bateria es un cambio de estrategia en el controlador, no una
potencia: se declara como lista de escrituras por modo (`ordenes:`), y sin
ellas el adaptador solo lee y lo dice.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from ..settings import EquipoModbus, Escritura, Planta, Senal, Settings
from .base import AdapterError
from .energia_base import (
    DETALLES_MODO,
    ConCacheDeEstado,
    EstadoEnergia,
    detalle_forzado,
    validar_orden_bateria,
)
from .modbus import (
    ConexionModbus,
    ErrorConexion,
    f32,
    i16,
    i32_de_u32,
    u32,
    u32_alta_primero,
    u32_baja_primero,
)

# Dada una MAC, la IP que tiene ahora; dada None, las IP de todos los equipos
# Sungrow que la red conoce (por el prefijo de fabricante de su MAC).
BuscadorIP = Callable[[str | None], Awaitable[list[str]]]

# Prefijo de fabricante (OUI) de Sungrow Power Supply Co., segun el registro
# IEEE. Es lo que permite encontrar el registrador sin saber su IP ni su MAC.
OUI_SUNGROW = ("ac:19:9f",)

# --- Inversor de cadena SG-CX (documento publico de Sungrow, base 0) --------
SENALES_INVERSOR: dict[str, Senal] = {
    "tipo_equipo": Senal(registro=4999),
    "potencia_nominal_kw": Senal(registro=5000, escala=0.1),
    "pv_dia_kwh": Senal(registro=5002, escala=0.1),
    "pv_total_kwh": Senal(registro=5003, tipo="u32"),
    "temperatura_c": Senal(registro=5007, tipo="i16", escala=0.1),
    "mppt1_v": Senal(registro=5010, escala=0.1),
    "mppt1_a": Senal(registro=5011, escala=0.1),
    "mppt2_v": Senal(registro=5012, escala=0.1),
    "mppt2_a": Senal(registro=5013, escala=0.1),
    "mppt3_v": Senal(registro=5014, escala=0.1),
    "mppt3_a": Senal(registro=5015, escala=0.1),
    "potencia_dc_w": Senal(registro=5016, tipo="u32"),
    "tension_a_v": Senal(registro=5018, escala=0.1),
    "tension_b_v": Senal(registro=5019, escala=0.1),
    "tension_c_v": Senal(registro=5020, escala=0.1),
    "potencia_w": Senal(registro=5030, tipo="u32"),
    "factor_potencia": Senal(registro=5034, tipo="i16", escala=0.001),
    "frecuencia_hz": Senal(registro=5035, escala=0.1),
    "estado": Senal(registro=5037),
    "mppt4_v": Senal(registro=5114, escala=0.1),
    "mppt4_a": Senal(registro=5115, escala=0.1),
}

ESTADOS_INVERSOR = {
    0x0000: "funcionando",
    0x8000: "parado",
    0x1300: "parado por tecla",
    0x1500: "parada de emergencia",
    0x1400: "en espera",
    0x1200: "espera inicial",
    0x1600: "arrancando",
    0x9100: "funcionando con alarma",
    0x8100: "funcionando limitado",
    0x8200: "funcionando por consigna",
    0x5500: "fallo",
    0x2500: "fallo de comunicacion",
}

# --- Contador Janitza UMG104 (lista de direcciones de Janitza, base 0) ------
# Float IEEE con la palabra alta primero, registros de mantenimiento. La
# potencia es positiva cuando la casa IMPORTA. Los acumulados estan por
# contrastar con `sondear`: iSolarCloud los muestra en MWh, con lo que un
# registro equivocado se ve a la primera.
_JANITZA = {"tipo": "f32", "funcion": "holding", "palabra_alta_primero": True}
SENALES_CONTADOR: dict[str, Senal] = {
    "tension_a_v": Senal(registro=19000, **_JANITZA),
    "tension_b_v": Senal(registro=19002, **_JANITZA),
    "tension_c_v": Senal(registro=19004, **_JANITZA),
    "potencia_w": Senal(registro=19026, **_JANITZA),
    "frecuencia_hz": Senal(registro=19050, **_JANITZA),
    "import_total_kwh": Senal(registro=19076, escala=0.001, **_JANITZA),
    "export_total_kwh": Senal(registro=19084, escala=0.001, **_JANITZA),
}

# La bateria ST no tiene mapa publico: lo que se sepa se declara en el YAML.
SENALES_BATERIA: dict[str, Senal] = {}

MAPAS_DE_SERIE = {
    "inversor": SENALES_INVERSOR,
    "contador": SENALES_CONTADOR,
    "bateria": SENALES_BATERIA,
}

# Nombres que el estado energetico consume; el resto de senales van a `crudo`.
_CONSUMIDAS = {
    "inversor": {"potencia_w", "pv_total_kwh"},
    "contador": {"potencia_w", "import_total_kwh", "export_total_kwh"},
    "bateria": {
        "potencia_w", "soc_pct", "soh_pct", "temperatura_c", "modo_ems",
        "carga_total_kwh", "descarga_total_kwh",
    },
}

# Un bloque Modbus admite 125 registros; se deja margen y no se salta huecos
# grandes, que en algunos equipos devuelven excepcion.
MAX_BLOQUE = 100
MAX_HUECO = 30


def decodificar(senal: Senal, regs: list[int]) -> float:
    """Los registros crudos de una senal, ya con signo, orden y escala."""
    if senal.tipo == "u16":
        valor: float = regs[0]
    elif senal.tipo == "i16":
        valor = i16(regs[0])
    elif senal.tipo == "f32":
        valor = f32(regs, alta_primero=senal.palabra_alta_primero)
    else:
        entero = u32(regs, alta_primero=senal.palabra_alta_primero)
        valor = i32_de_u32(entero) if senal.tipo == "i32" else entero
    return valor * senal.escala


def ancho(senal: Senal) -> int:
    return 2 if senal.tipo in ("u32", "i32", "f32") else 1


def bloques(senales: dict[str, Senal]) -> list[tuple[bool, int, int]]:
    """Agrupa las senales en lecturas contiguas: `(mantenimiento, inicio, cantidad)`.

    Doce lecturas sueltas por equipo y por segundo es lo que atraganta a un
    registrador; tres bloques no.
    """
    salida: list[tuple[bool, int, int]] = []
    for funcion in ("input", "holding"):
        tramos = sorted(
            (s.registro, s.registro + ancho(s))
            for s in senales.values() if s.funcion == funcion
        )
        inicio: int | None = None
        fin = 0
        for a, b in tramos:
            if inicio is not None and a - fin <= MAX_HUECO and b - inicio <= MAX_BLOQUE:
                fin = max(fin, b)
                continue
            if inicio is not None:
                salida.append((funcion == "holding", inicio, fin - inicio))
            inicio, fin = a, b
        if inicio is not None:
            salida.append((funcion == "holding", inicio, fin - inicio))
    return salida


def _mapa(nombre: str, equipo: EquipoModbus) -> dict[str, Senal]:
    return {**MAPAS_DE_SERIE[nombre], **equipo.senales}


class PlantaSungrow(ConCacheDeEstado):
    def __init__(
        self,
        settings: Settings,
        planta: Planta,
        *,
        buscar_ip: BuscadorIP | None = None,
    ) -> None:
        self._settings = settings
        self._planta = planta
        self._buscar_ip = buscar_ip
        self._max_w = settings.sungrow_max_potencia_w
        self._host_resuelto: str | None = None
        self._conexiones: dict[tuple[str, int], ConexionModbus] = {}

    @property
    def configurado(self) -> bool:
        """Con IP, o con alguien que la busque (UniFi). La planta ya esta declarada
        en el YAML, asi que no se ofrece energia a una casa que no la tiene."""
        return bool(self._settings.sungrow_host or self._buscar_ip)

    @property
    def puede_controlar(self) -> bool:
        """Solo si el YAML declara las escrituras de cada modo de la bateria."""
        return bool(self._planta.bateria and self._planta.bateria.ordenes)

    @property
    def nota_signo_red(self) -> str:
        return (
            "planta por Logger1000: el signo de la red se invierte con "
            "`planta.invertir_signo_red` en config.yaml"
        )

    @property
    def equipos(self) -> dict[str, EquipoModbus]:
        """Los declarados, por nombre. El inversor siempre esta."""
        salida = {"inversor": self._planta.inversor}
        if self._planta.contador:
            salida["contador"] = self._planta.contador
        if self._planta.bateria:
            salida["bateria"] = self._planta.bateria
        return salida

    # --- Conexion --------------------------------------------------------
    async def _host_logger(self) -> str | None:
        s = self._settings
        if s.sungrow_host:
            return s.sungrow_host
        if self._host_resuelto is None and self._buscar_ip:
            self._host_resuelto = await self._descubrir(await self._buscar_ip(s.sungrow_mac))
        return self._host_resuelto

    async def _descubrir(self, candidatas: list[str]) -> str:
        """De las IP de equipos Sungrow que hay en la red, la que es el Logger.

        En la casa hay dos: el registrador y el controlador de la bateria, que
        llega por su propio cable. Se prueba en cada una a leer el tipo de
        equipo del inversor (registro 4999 por su id de esclavo): el Logger lo
        reenvia y contesta con un tipo distinto de cero; el otro no contesta
        o contesta con basura. Asi no hace falta saber cual es cual.
        """
        if not candidatas:
            que = f"con la MAC {self._settings.sungrow_mac}" if self._settings.sungrow_mac else (
                f"de Sungrow (MAC que empieza por {', '.join(OUI_SUNGROW)})"
            )
            raise AdapterError(
                f"UniFi no tiene ningun cliente {que}. Comprueba que el registrador "
                "esta encendido y conectado al switch, o pon SUNGROW_HOST con su IP."
            )
        if len(candidatas) == 1:
            return candidatas[0]
        responden: list[str] = []
        for ip in candidatas:
            tipo = await self._tipo_de_inversor_en(ip)
            if tipo:
                return ip
            if tipo is not None:
                responden.append(ip)
        if responden:
            return responden[0]
        raise AdapterError(
            f"Ninguno de los equipos Sungrow de la red ({', '.join(candidatas)}) contesta "
            "por Modbus TCP. Activa Modbus TCP en la web del Logger1000 (Sistema → "
            "Parametros de puerto) o pon SUNGROW_HOST con su IP."
        )

    async def _tipo_de_inversor_en(self, ip: str) -> int | None:
        """El tipo de equipo que contesta el inversor por esa IP; None si nada."""
        conexion = ConexionModbus(
            ip, self._settings.sungrow_puerto, sin_host="", no_conecta="", timeout=4
        )
        try:
            regs = await conexion.leer(4999, 1, unit=self._planta.inversor.unit)
            return regs[0]
        except AdapterError:
            return None
        finally:
            await conexion.cerrar()

    async def _conexion(self, equipo: EquipoModbus) -> ConexionModbus:
        host = equipo.host or await self._host_logger()
        puerto = equipo.puerto or self._settings.sungrow_puerto
        clave = (host or "", puerto)
        if clave not in self._conexiones:
            # Sin id de esclavo por defecto: la conexion la comparten los tres
            # equipos y cada lectura pasa el suyo.
            self._conexiones[clave] = ConexionModbus(
                host,
                puerto,
                sin_host=(
                    "Planta sin configurar: falta SUNGROW_HOST (IP del Logger1000) "
                    "o SUNGROW_MAC con UniFi configurado para buscarla."
                ),
                no_conecta=(
                    "Comprueba en la web del Logger1000 que Modbus TCP esta activado "
                    "(puerto 502) y que el registrador esta en la misma red."
                ),
            )
        return self._conexiones[clave]

    async def cerrar(self) -> None:
        for conexion in self._conexiones.values():
            await conexion.cerrar()
        self._conexiones.clear()

    async def _leer_equipo(self, nombre: str, equipo: EquipoModbus) -> dict[str, float]:
        senales = _mapa(nombre, equipo)
        try:
            return await self._leer_senales(equipo, senales)
        except ErrorConexion:
            # Solo si el socket ha fallado, no si el equipo ha respondido con
            # excepcion (un registro mal declarado no se arregla reconectando).
            # Si la IP vino de la MAC puede haber cambiado: se busca otra vez
            # y se reintenta una sola vez.
            if self._settings.sungrow_host or self._host_resuelto is None:
                raise
            await self.cerrar()
            self._host_resuelto = None
            return await self._leer_senales(equipo, senales)

    async def _leer_senales(
        self, equipo: EquipoModbus, senales: dict[str, Senal]
    ) -> dict[str, float]:
        conexion = await self._conexion(equipo)
        crudos: dict[tuple[bool, int], int] = {}
        # El lock es el del socket, el mismo que usan las escrituras: una
        # lectura de la vigilancia y una orden confirmada no pueden cruzarse
        # en la misma conexion.
        async with conexion.lock:
            for mantenimiento, inicio, cantidad in bloques(senales):
                regs = await conexion.leer(
                    inicio, cantidad, mantenimiento=mantenimiento, unit=equipo.unit
                )
                for i, valor in enumerate(regs):
                    crudos[(mantenimiento, inicio + i)] = valor
        salida: dict[str, float] = {}
        for nombre, senal in senales.items():
            m = senal.funcion == "holding"
            regs = [crudos[(m, senal.registro + i)] for i in range(ancho(senal))]
            salida[nombre] = decodificar(senal, regs)
        return salida

    async def leer_todo(self) -> dict[str, dict[str, float]]:
        """Todas las senales de todos los equipos, para el sondeo y `crudo`."""
        salida: dict[str, dict[str, float]] = {}
        for nombre, equipo in self.equipos.items():
            salida[nombre] = await self._leer_equipo(nombre, equipo)
        return salida

    # --- Estado ----------------------------------------------------------
    async def _leer_estado(self) -> EstadoEnergia:
        return self._componer(await self.leer_todo())

    def _componer(self, lecturas: dict[str, dict[str, float]]) -> EstadoEnergia:
        inv = lecturas["inversor"]
        if "potencia_w" not in inv:
            raise AdapterError(
                "El inversor no declara la senal `potencia_w`: sin ella no hay "
                "produccion solar. Revisa `planta.inversor.senales` en config.yaml."
            )
        pv = int(round(inv["potencia_w"]))

        contador = lecturas.get("contador")
        if contador is None or "potencia_w" not in contador:
            raise AdapterError(
                "La planta no tiene contador declarado (`planta.contador` en "
                "config.yaml), y sin el no se sabe lo que entra o sale de la red "
                "ni lo que consume la casa. Solo se puede leer el inversor: usa "
                "`python -m casa_ai.adapters.planta leer` para verlo."
            )
        red = -int(round(contador["potencia_w"]))
        if self._planta.invertir_signo_red:
            red = -red

        declarada = lecturas.get("bateria")
        bateria = declarada or {}
        bateria_w = int(round(bateria.get("potencia_w", 0)))
        if self._planta.invertir_signo_bateria:
            bateria_w = -bateria_w

        crudo: dict[str, Any] = {}
        for nombre, valores in lecturas.items():
            extra = {k: v for k, v in valores.items() if k not in _CONSUMIDAS[nombre]}
            if nombre == "inversor" and "estado" in extra:
                codigo = int(extra["estado"])
                extra["estado"] = ESTADOS_INVERSOR.get(codigo, f"desconocido(0x{codigo:04X})")
            if extra:
                crudo[nombre] = extra
        consecuencia = (
            "se supone en reposo, asi que el consumo de la casa puede salir alto "
            "mientras carga y bajo mientras descarga."
        )
        if declarada is None:
            crudo["aviso_bateria"] = (
                f"La bateria no esta declarada en `planta.bateria`: {consecuencia}"
            )
        elif "potencia_w" not in declarada:
            crudo["aviso_bateria"] = (
                "La bateria esta declarada pero sin la senal `potencia_w` (ni soc_pct, "
                f"soh_pct...): {consecuencia} Casa sus registros con `barrer`."
            )

        modo = bateria.get("modo_ems")
        return EstadoEnergia(
            pv_w=pv,
            bateria_w=bateria_w,
            bateria_soc=bateria.get("soc_pct"),
            red_w=red,
            consumo_casa_w=max(0, pv - bateria_w - red),
            bateria_soh=bateria.get("soh_pct"),
            bateria_temp_c=bateria.get("temperatura_c"),
            modo_ems=None if modo is None else int(modo),
            pv_total_kwh=inv.get("pv_total_kwh"),
            carga_total_kwh=bateria.get("carga_total_kwh"),
            descarga_total_kwh=bateria.get("descarga_total_kwh"),
            import_total_kwh=contador.get("import_total_kwh"),
            export_total_kwh=contador.get("export_total_kwh"),
            origen="modbus",
            crudo=crudo,
        )

    # --- Control de bateria ----------------------------------------------
    async def fijar_modo_bateria(self, modo: str, potencia_w: int | None = None) -> dict[str, Any]:
        modo = modo.strip().lower()
        if not self.puede_controlar:
            raise AdapterError(
                "La bateria de la planta solo se lee: no hay `ordenes:` declaradas "
                "en `planta.bateria`. Hacen falta los registros de control del "
                "controlador ST (documento de Sungrow o del instalador) para "
                "poder cambiar su estrategia."
            )
        assert self._planta.bateria is not None
        escrituras = self._planta.bateria.ordenes.get(modo)
        if escrituras is None:
            declarados = ", ".join(sorted(self._planta.bateria.ordenes))
            raise AdapterError(
                f"El modo '{modo}' no esta declarado en `ordenes:` (hay: {declarados})."
            )
        potencia: int | None = None
        if modo in ("cargar", "descargar"):
            modo, potencia = validar_orden_bateria(modo, potencia_w, self._max_w)

        conexion = await self._conexion(self._planta.bateria)
        async with conexion.lock:
            for e in escrituras:
                valor = _valor_de(e, potencia)
                await conexion.escribir(e.registro, valor, unit=self._planta.bateria.unit)
        self.invalidar_estado()

        if potencia is not None:
            return {
                "modo": modo, "potencia_w": potencia, "detalle": detalle_forzado(modo, potencia),
            }
        return {"modo": modo, "detalle": DETALLES_MODO.get(modo, f"Orden '{modo}' enviada.")}


def _valor_de(escritura: Escritura, potencia: int | None) -> int:
    if escritura.valor is not None:
        return escritura.valor
    if escritura.potencia_escala is None:
        raise AdapterError(
            f"La escritura al registro {escritura.registro} no tiene `valor` ni "
            "`potencia_escala`: no se sabe que mandar."
        )
    if potencia is None:
        raise AdapterError(
            f"El registro {escritura.registro} lleva potencia, pero este modo no la tiene."
        )
    return int(round(potencia * escritura.potencia_escala))


# --- Sondeo ------------------------------------------------------------------
#
# Devuelven texto, no lo imprimen: el complemento no tiene terminal y el
# sondeo se pide por Telegram (/sondear) o por el API. La linea de comandos
# solo lo imprime.


async def sondear(planta: PlantaSungrow, hasta: int = 30) -> str:
    """Que ids de esclavo responden tras el registrador, y que dicen ser."""
    equipo = planta._planta.inversor
    conexion = await planta._conexion(equipo)
    lineas = [f"Sondeando {conexion.host}:{conexion.puerto}, ids 1-{hasta}"]
    for unit in range(1, hasta + 1):
        try:
            regs = await conexion.leer(4999, 2, unit=unit)
            lineas.append(
                f"id {unit}: responde. tipo 0x{regs[0]:04X}, nominal {regs[1] * 0.1:.1f} kW"
            )
        except ErrorConexion:
            lineas.append(f"id {unit}: sin respuesta")
            await planta.cerrar()
            conexion = await planta._conexion(equipo)
        except AdapterError as e:
            lineas.append(f"id {unit}: {e}")
    lineas.append(
        "Un tipo distinto de 0 es un inversor Sungrow. El contador y la bateria "
        "responden con otra cosa o con error: apunta sus ids en `planta:`."
    )
    return "\n".join(lineas)


async def leer(planta: PlantaSungrow) -> str:
    """Todas las senales declaradas y el estado compuesto, para comparar con iSolarCloud."""
    import json

    lecturas = await planta.leer_todo()
    partes = [json.dumps(lecturas, indent=1, ensure_ascii=False)]
    try:
        resumen = planta._componer(lecturas).resumen()
        partes.append(json.dumps(resumen, indent=1, ensure_ascii=False))
    except AdapterError as e:
        partes.append(f"Estado compuesto: {e}")
    partes.append("Compara con iSolarCloud AHORA. Los acumulados en MWh son la mejor pista.")
    return "\n\n".join(partes)


async def barrer(
    planta: PlantaSungrow, unit: int, inicio: int, cantidad: int, mantenimiento: bool = False
) -> str:
    """Registros crudos con sus lecturas posibles, para casarlos con iSolarCloud."""
    cantidad = min(cantidad, 200)
    conexion = await planta._conexion(planta._planta.inversor)
    regs: list[int] = []
    for desde in range(inicio, inicio + cantidad, MAX_BLOQUE):
        n = min(MAX_BLOQUE, inicio + cantidad - desde)
        regs += await conexion.leer(desde, n, mantenimiento=mantenimiento, unit=unit)
    columnas = ("reg", "u16", "i16", "u32 baja", "u32 alta", "f32 alta")
    anchos = (6, 6, 7, 11, 11, 12)
    lineas = [" ".join(f"{c:>{a}}" for c, a in zip(columnas, anchos, strict=True))]
    for i, v in enumerate(regs):
        fila = f"{inicio + i:>6} {v:>6} {i16(v):>7}"
        if i + 1 < len(regs):
            par = [v, regs[i + 1]]
            fila += (
                f" {u32_baja_primero(par):>11} {u32_alta_primero(par):>11}"
                f" {f32(par, alta_primero=True):>12.4g}"
            )
        lineas.append(fila)
    return "\n".join(lineas)


async def orden_de_sondeo(planta: PlantaSungrow, texto: str) -> str:
    """Interpreta «sondear [hasta]», «leer» o «barrer unit inicio cantidad [holding]».

    Es lo que escribe el usuario detras de /sondear en Telegram.
    """
    partes = texto.split()
    orden = partes[0] if partes else "sondear"
    try:
        if orden == "sondear":
            return await sondear(planta, int(partes[1]) if len(partes) > 1 else 30)
        if orden == "leer":
            return await leer(planta)
        if orden == "barrer" and len(partes) >= 4:
            return await barrer(
                planta, int(partes[1]), int(partes[2]), int(partes[3]),
                mantenimiento=len(partes) > 4 and partes[4] == "holding",
            )
    except ValueError:
        pass
    return (
        "Ordenes: `sondear [hasta]` (que ids responden), `leer` (todas las senales "
        "y el estado), `barrer <id> <inicio> <cantidad> [holding]` (registros crudos)."
    )


async def _principal() -> None:  # pragma: no cover
    import argparse

    from ..settings import get_inventario, get_settings

    ap = argparse.ArgumentParser(description="Sondeo de la planta Sungrow por el Logger1000")
    sub = ap.add_subparsers(dest="orden", required=True)
    s = sub.add_parser("sondear", help="que ids de esclavo responden")
    s.add_argument("--hasta", type=int, default=30)
    sub.add_parser("leer", help="todas las senales declaradas, y el estado compuesto")
    b = sub.add_parser("barrer", help="registros crudos de un id")
    b.add_argument("unit", type=int)
    b.add_argument("inicio", type=int)
    b.add_argument("cantidad", type=int)
    b.add_argument("--holding", action="store_true", help="funcion 3 en vez de 4")
    args = ap.parse_args()

    settings = get_settings()
    inventario = get_inventario()
    planta = PlantaSungrow(settings, inventario.planta or Planta())
    if not planta.configurado:
        print("Falta SUNGROW_HOST (la IP del Logger1000).")
        return
    try:
        if args.orden == "sondear":
            print(await sondear(planta, args.hasta))
        elif args.orden == "barrer":
            print(await barrer(planta, args.unit, args.inicio, args.cantidad, args.holding))
        else:
            print(await leer(planta))
    finally:
        await planta.cerrar()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_principal())
