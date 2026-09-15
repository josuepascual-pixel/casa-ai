"""Representacion comun del estado energetico.

Hay dos formas de leer la instalacion fotovoltaica y ambas producen esto:

* `Sungrow` habla Modbus TCP directamente con el inversor. Da todo, incluida
  la salud de la bateria y los contadores acumulados, y permite controlarla.
  Exige estar en la misma red que el inversor.
* `EnergiaHA` lee los mismos datos de sensores de Home Assistant. No necesita
  estar en la red del inversor, solo alcanzar Home Assistant, y da lo que esos
  sensores expongan (a veces menos).

Los campos que una fuente puede no conocer son opcionales, y `resumen()` los
omite cuando faltan. Eso importa: devolver 0.0 en la salud de la bateria
porque no se ha podido leer haria que el agente informase de una bateria
muerta.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .base import AdapterError

# Lo que vive una lectura. El panel y la vigilancia preguntan varias veces por
# segundo cuando coinciden, y ningun inversor ni Home Assistant cambia de
# valor tan rapido.
CACHE_TTL_S = 5.0

MODOS_EMS = {0: "autoconsumo", 2: "forzado", 3: "ems_externo", 4: "vpp"}


@dataclass
class EstadoEnergia:
    """Foto instantanea del sistema energetico."""

    pv_w: int
    bateria_w: int                      # >0 cargando, <0 descargando
    # None cuando no se ha podido leer. Publicarlo como 0 haria que la
    # vigilancia avisara de bateria vacia cada media hora.
    bateria_soc: float | None
    red_w: int                          # >0 exportando, <0 importando
    consumo_casa_w: int
    # Lo que no toda fuente conoce:
    bateria_soh: float | None = None
    bateria_temp_c: float | None = None
    modo_ems: int | None = None
    pv_total_kwh: float | None = None
    carga_total_kwh: float | None = None
    descarga_total_kwh: float | None = None
    import_total_kwh: float | None = None
    export_total_kwh: float | None = None
    origen: str = "modbus"
    crudo: dict[str, Any] = field(default_factory=dict)

    def resumen(self) -> dict[str, Any]:
        datos: dict[str, Any] = {
            "solar_w": self.pv_w,
            "consumo_casa_w": self.consumo_casa_w,
            "bateria_w": self.bateria_w,
            "bateria_estado": (
                "cargando" if self.bateria_w > 0
                else "descargando" if self.bateria_w < 0
                else "reposo"
            ),
            "red_w": self.red_w,
            "red_estado": "exportando" if self.red_w > 0 else "importando",
            "origen_de_los_datos": self.origen,
        }
        if self.bateria_soc is not None:
            datos["bateria_soc_pct"] = round(self.bateria_soc, 1)
        else:
            datos["bateria_soc_pct"] = None
            datos["aviso"] = (
                "No se ha podido leer la carga de la bateria. No la des por "
                "vacia: revisa de donde se lee (`bateria_soc` en `energia_ha:` "
                "o la seccion `bateria:` de `planta:` en config/config.yaml)."
            )
        if self.bateria_soh is not None:
            datos["bateria_salud_pct"] = round(self.bateria_soh, 1)
        if self.bateria_temp_c is not None:
            datos["bateria_temp_c"] = round(self.bateria_temp_c, 1)
        if self.modo_ems is not None:
            datos["modo_ems"] = MODOS_EMS.get(self.modo_ems, f"desconocido({self.modo_ems})")

        acumulados = {
            "solar_generado": self.pv_total_kwh,
            "bateria_cargada": self.carga_total_kwh,
            "bateria_descargada": self.descarga_total_kwh,
            "red_importada": self.import_total_kwh,
            "red_exportada": self.export_total_kwh,
        }
        conocidos = {k: round(v, 1) for k, v in acumulados.items() if v is not None}
        if conocidos:
            datos["acumulados_kwh"] = conocidos
        return datos


MODOS_BATERIA = ("autoconsumo", "cargar", "descargar", "parar")


def validar_orden_bateria(modo: str, potencia_w: int | None, max_w: int) -> tuple[str, int]:
    """Normaliza y valida una orden de carga o descarga forzada.

    El tope de potencia es un limite de seguridad, no una preferencia: vivia
    duplicado en los dos adaptadores de energia, asi que subirlo en uno y
    olvidar el otro no lo detectaba nada.

    Devuelve `(modo, potencia)` para "cargar" y "descargar", los dos unicos
    modos que llevan potencia.
    """
    modo = modo.strip().lower()
    if modo not in MODOS_BATERIA:
        raise AdapterError(f"Modo '{modo}' no valido. Usa: {', '.join(MODOS_BATERIA)}.")
    if modo in ("autoconsumo", "parar"):
        raise AdapterError(f"El modo '{modo}' no lleva potencia.")
    if potencia_w is None:
        raise AdapterError(f"Para '{modo}' hace falta indicar la potencia en vatios.")
    potencia = int(potencia_w)
    if potencia <= 0:
        raise AdapterError("La potencia debe ser mayor que 0 W.")
    if potencia > max_w:
        raise AdapterError(
            f"{potencia} W supera el tope de seguridad configurado "
            f"({max_w} W, ajustable en SUNGROW_MAX_POTENCIA_W)."
        )
    return modo, potencia


class ConCacheDeEstado:
    """`estado()` con cache corta, igual en las tres fuentes.

    Estaba copiado en las tres con la misma doble comprobacion bajo lock. La
    fuente concreta implementa `_leer_estado()` y ya esta; el lock de aqui
    solo protege la cache, la serializacion del socket es cosa de cada una.
    """

    _cache: tuple[float, EstadoEnergia] | None = None
    _lock_cache: asyncio.Lock | None = None

    async def _leer_estado(self) -> EstadoEnergia:  # pragma: no cover - abstracto
        raise NotImplementedError

    def _cacheado(self, usar_cache: bool) -> EstadoEnergia | None:
        if usar_cache and self._cache and time.monotonic() - self._cache[0] < CACHE_TTL_S:
            return self._cache[1]
        return None

    def invalidar_estado(self) -> None:
        """Se llama al escribir: el estado siguiente tiene que verse cambiado."""
        self._cache = None

    async def estado(self, *, usar_cache: bool = True) -> EstadoEnergia:
        fresco = self._cacheado(usar_cache)
        if fresco is not None:
            return fresco
        if self._lock_cache is None:
            self._lock_cache = asyncio.Lock()
        async with self._lock_cache:
            # Otro pudo haberla rellenado mientras esperabamos el lock.
            fresco = self._cacheado(usar_cache)
            if fresco is not None:
                return fresco
            estado = await self._leer_estado()
            self._cache = (time.monotonic(), estado)
            return estado


# Lo que se le dice al usuario al cambiar de modo, igual en las dos fuentes
# que pueden ordenarlo. Habia dos redacciones para el mismo hecho.
DETALLES_MODO = {
    "autoconsumo": "El inversor vuelve a decidir por si mismo.",
    "parar": "Bateria en reposo forzado (ni carga ni descarga).",
}


def detalle_forzado(modo: str, potencia_w: int) -> str:
    """El aviso de que el modo manual no se deshace solo."""
    return (
        f"Bateria forzada a {modo} a {potencia_w} W. Se mantiene asi hasta que "
        "vuelvas a poner modo 'autoconsumo'."
    )


@runtime_checkable
class FuenteEnergia(Protocol):
    """Lo que el resto del sistema puede pedirle a una fuente de energia.

    La mitad de datos de este contrato ya estaba en su sitio (`EstadoEnergia`,
    con campos `None` para lo que una fuente no conoce); faltaba la de
    comportamiento. Sin declararla, `Contexto.energia` era `Any` y cada
    consumidor inventaba su propio `getattr(..., "puede_controlar", X)` con un
    default distinto: el panel podia decir que si se puede controlar la bateria
    mientras el registro ocultaba la herramienta, o al contrario.
    """

    @property
    def configurado(self) -> bool: ...

    @property
    def puede_controlar(self) -> bool:
        """Si esta fuente puede dar ordenes a la bateria, no solo leerla."""
        ...

    async def estado(self, *, usar_cache: bool = True) -> EstadoEnergia: ...

    async def fijar_modo_bateria(
        self, modo: str, potencia_w: int | None = None
    ) -> dict[str, Any]: ...

    async def cerrar(self) -> None: ...
