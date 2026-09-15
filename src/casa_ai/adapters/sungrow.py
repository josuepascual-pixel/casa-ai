"""Adaptador Sungrow (inversor hibrido SH + bateria) por Modbus TCP.

Direcciones
-----------
El documento oficial de Sungrow numera los registros en base 1; pymodbus
direcciona en base 0. Todas las constantes de este fichero estan en BASE 0
(es decir, ya con el -1 aplicado), que es la convencion que usan los mapas
publicos contrastados. Por eso aqui el comando de carga/descarga es 13050 y
no 13051: son el mismo registro.

Signos
------
El firmware de Sungrow no es homogeneo entre modelos y versiones en como
codifica el signo de la potencia de bateria y de red. Este adaptador:

* deriva el signo de la bateria del registro de estado 13000 (bit 0x02 =
  cargando, bit 0x04 = descargando) en vez de fiarse del signo del valor;
* expone la potencia de red con la convencion "positivo = exportando", y
  permite invertirla por configuracion si tu instalacion la reporta al
  contrario.

Antes de dar por buenos los numeros, ejecuta el diagnostico:

    python -m casa_ai.adapters.sungrow

Compara con lo que muestra la app iSolarCloud en ese momento. Si el signo de
la red esta al reves, pon `invertir_signo_red: true`.

Conexion
--------
El dongle WiNet-S admite UNA sola conexion Modbus simultanea y se atraganta si
se le piden lecturas muy seguidas. Por eso hay un lock que serializa el acceso
y una cache corta de lecturas.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..settings import Settings
from .base import AdapterError
from .energia_base import (
    DETALLES_MODO,
    MODOS_BATERIA,
    ConCacheDeEstado,
    EstadoEnergia,
    detalle_forzado,
    validar_orden_bateria,
)
from .modbus import ConexionModbus, i16, i32_de_u32, u32_baja_primero

# --- Registros de entrada (funcion 4), base 0 -------------------------------
REG_POTENCIA_PV = 5016          # uint32, W
REG_ESTADO_BATERIA = 13000      # uint16, mascara de bits
REG_PV_TOTAL = 13002            # uint32, x0.1 kWh
REG_POTENCIA_RED = 13009        # int32, W (ver nota de signos)
REG_BATERIA_CORRIENTE = 13020   # int16, x0.1 A
REG_BATERIA_POTENCIA = 13021    # int16/uint16, W
REG_BATERIA_SOC = 13022         # uint16, x0.1 %
REG_BATERIA_SOH = 13023         # uint16, x0.1 %
REG_BATERIA_TEMP = 13024        # int16, x0.1 C
REG_DESCARGA_TOTAL = 13026      # uint32, x0.1 kWh
REG_IMPORT_TOTAL = 13036        # uint32, x0.1 kWh
REG_CARGA_TOTAL = 13040         # uint32, x0.1 kWh
REG_EXPORT_TOTAL = 13045        # uint32, x0.1 kWh

# --- Registros de mantenimiento (funcion 3 / escritura), base 0 -------------
REG_MODO_EMS = 13049            # 0=autoconsumo, 2=forzado, 3=EMS externo, 4=VPP
REG_COMANDO_BATERIA = 13050     # 0xAA=cargar, 0xBB=descargar, 0xCC=parar
REG_POTENCIA_FORZADA = 13051    # uint16, W

MODO_AUTOCONSUMO = 0
MODO_FORZADO = 2

CMD_CARGAR = 0xAA
CMD_DESCARGAR = 0xBB
CMD_PARAR = 0xCC

BIT_CARGANDO = 0x02
BIT_DESCARGANDO = 0x04



_u32 = u32_baja_primero


def _i32(regs: list[int]) -> int:
    return i32_de_u32(_u32(regs))


class Sungrow(ConCacheDeEstado):
    def __init__(self, settings: Settings, *, invertir_signo_red: bool = False) -> None:
        self._host = settings.sungrow_host
        self._max_w = settings.sungrow_max_potencia_w
        self._invertir_red = invertir_signo_red
        self._conexion = ConexionModbus(
            settings.sungrow_host,
            settings.sungrow_puerto,
            settings.sungrow_slave_id,
            sin_host=(
                "Inversor sin configurar: falta SUNGROW_HOST (IP del dongle "
                "WiNet-S o del inversor en la red local)."
            ),
            no_conecta=(
                "Recuerda que el WiNet-S solo acepta una conexion a la vez: si "
                "Home Assistant o iSolarCloud la tienen abierta, este cliente no entra."
            ),
        )
        self._lock = self._conexion.lock

    @property
    def configurado(self) -> bool:
        return bool(self._host)

    @property
    def puede_controlar(self) -> bool:
        """Por Modbus siempre: los registros de control estan ahi."""
        return True

    @property
    def nota_signo_red(self) -> str:
        """Donde se invierte el signo de la red, para el diagnostico."""
        return f"SUNGROW_INVERTIR_SIGNO_RED esta en: {self._invertir_red}"

    # --- Conexion --------------------------------------------------------
    async def cerrar(self) -> None:
        await self._conexion.cerrar()

    async def _leer(
        self, direccion: int, cantidad: int, *, mantenimiento: bool = False
    ) -> list[int]:
        return await self._conexion.leer(direccion, cantidad, mantenimiento=mantenimiento)

    async def _escribir(self, direccion: int, valor: int) -> None:
        await self._conexion.escribir(direccion, valor)

    # --- Lectura de estado ------------------------------------------------
    async def _leer_estado(self) -> EstadoEnergia:
        async with self._lock:
            # Un bloque grande en vez de 12 lecturas: el WiNet-S lo agradece.
            pv = _u32(await self._leer(REG_POTENCIA_PV, 2))
            bloque = await self._leer(REG_ESTADO_BATERIA, 50)  # 13000..13049

            def r(direccion: int) -> int:
                return bloque[direccion - REG_ESTADO_BATERIA]

            def r32(direccion: int, firmado: bool) -> int:
                i = direccion - REG_ESTADO_BATERIA
                par = [bloque[i], bloque[i + 1]]
                return _i32(par) if firmado else _u32(par)

            estado_bat = r(REG_ESTADO_BATERIA)
            magnitud = r(REG_BATERIA_POTENCIA)
            # Si el firmware ya firma el valor lo respetamos; si viene como
            # magnitud sin signo, lo firmamos con los bits de estado.
            bateria_w = i16(magnitud)
            if bateria_w >= 0:
                if estado_bat & BIT_DESCARGANDO:
                    bateria_w = -abs(bateria_w)
                elif estado_bat & BIT_CARGANDO:
                    bateria_w = abs(bateria_w)

            red_w = r32(REG_POTENCIA_RED, firmado=True)
            if self._invertir_red:
                red_w = -red_w

            # Balance: lo que genera el sol, mas lo que suelta la bateria, mas
            # lo que entra de la red, es lo que consume la casa.
            consumo = pv - bateria_w - red_w

            estado = EstadoEnergia(
                pv_w=pv,
                bateria_w=bateria_w,
                bateria_soc=r(REG_BATERIA_SOC) * 0.1,
                bateria_soh=r(REG_BATERIA_SOH) * 0.1,
                bateria_temp_c=i16(r(REG_BATERIA_TEMP)) * 0.1,
                red_w=red_w,
                consumo_casa_w=max(0, consumo),
                modo_ems=r(REG_MODO_EMS),
                pv_total_kwh=r32(REG_PV_TOTAL, firmado=False) * 0.1,
                carga_total_kwh=r32(REG_CARGA_TOTAL, firmado=False) * 0.1,
                descarga_total_kwh=r32(REG_DESCARGA_TOTAL, firmado=False) * 0.1,
                import_total_kwh=r32(REG_IMPORT_TOTAL, firmado=False) * 0.1,
                export_total_kwh=r32(REG_EXPORT_TOTAL, firmado=False) * 0.1,
                origen="modbus",
                crudo={"13000_estado": estado_bat, "13021_bateria": magnitud},
            )
            return estado

    # --- Control de bateria ----------------------------------------------
    async def fijar_modo_bateria(self, modo: str, potencia_w: int | None = None) -> dict[str, Any]:
        """Cambia el modo de la bateria.

        modo: "autoconsumo" | "cargar" | "descargar" | "parar"

        "autoconsumo" devuelve el control al inversor (es el estado normal).
        Los otros tres fuerzan el modo manual, que persiste hasta que se
        vuelva a autoconsumo: es una decision con consecuencias economicas,
        asi que la capa de seguridad la marca como riesgo alto.
        """
        modo = modo.strip().lower()
        if modo not in MODOS_BATERIA:
            raise AdapterError(f"Modo '{modo}' no valido. Usa: {', '.join(MODOS_BATERIA)}.")

        if modo == "autoconsumo":
            async with self._lock:
                await self._escribir(REG_COMANDO_BATERIA, CMD_PARAR)
                await self._escribir(REG_MODO_EMS, MODO_AUTOCONSUMO)
            self.invalidar_estado()
            return {"modo": "autoconsumo", "detalle": DETALLES_MODO["autoconsumo"]}

        if modo == "parar":
            async with self._lock:
                await self._escribir(REG_MODO_EMS, MODO_FORZADO)
                await self._escribir(REG_COMANDO_BATERIA, CMD_PARAR)
            self.invalidar_estado()
            return {"modo": "parar", "detalle": DETALLES_MODO["parar"]}

        modo, potencia = validar_orden_bateria(modo, potencia_w, self._max_w)
        comandos = {"cargar": CMD_CARGAR, "descargar": CMD_DESCARGAR}
        async with self._lock:
            await self._escribir(REG_MODO_EMS, MODO_FORZADO)
            await self._escribir(REG_POTENCIA_FORZADA, potencia)
            await self._escribir(REG_COMANDO_BATERIA, comandos[modo])
        self.invalidar_estado()
        return {
            "modo": modo,
            "potencia_w": potencia,
            "detalle": detalle_forzado(modo, potencia),
        }


async def _diagnostico() -> None:  # pragma: no cover - utilidad manual
    """Vuelca registros crudos para verificar el mapa contra tu instalacion real."""
    from ..settings import get_settings

    s = get_settings()
    inv = Sungrow(s, invertir_signo_red=s.sungrow_invertir_signo_red)
    if not inv.configurado:
        print("SUNGROW_HOST no configurado. Exporta la variable o rellena .env")
        return
    print(f"Conectando a {s.sungrow_host}:{s.sungrow_puerto} (slave {s.sungrow_slave_id})...")
    estado = await inv.estado(usar_cache=False)
    import json

    print(json.dumps(estado.resumen(), indent=2, ensure_ascii=False))
    print("\nRegistros crudos:", estado.crudo)
    print(
        "\nCompara estos valores con la app iSolarCloud AHORA MISMO.\n"
        "- Si el signo de 'red_w' esta al reves, activa invertir_signo_red.\n"
        "- Si 'bateria_estado' no coincide, revisa el registro 13000 arriba."
    )
    await inv.cerrar()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_diagnostico())
