"""Lectura (y control opcional) de la energia a traves de Home Assistant.

Existe por dos motivos muy concretos:

1. **El dongle WiNet-S de Sungrow admite una sola conexion Modbus.** Si Home
   Assistant ya esta leyendo el inversor, un segundo cliente no entra. En vez
   de obligar a elegir, este adaptador lee los mismos datos de los sensores
   que HA ya tiene.

2. **Permite ejecutar el sistema fuera de la red de casa.** Modbus, BluOS y
   KNX solo funcionan en la LAN. Home Assistant, en cambio, se puede alcanzar
   desde fuera por un tunel. Si la energia se lee via HA, el unico sistema que
   hay que exponer es HA, y el backend puede vivir en la nube.

Que se pierde respecto a Modbus directo: solo se conoce lo que expongan los
sensores configurados. Los campos que falten se omiten en vez de rellenarse
con ceros, para que el agente no informe de datos que no ha leido.

Configuracion: la seccion `energia_ha:` de config/config.yaml mapea cada
magnitud a un entity_id. El control de bateria solo esta disponible si se
declaran ademas las entidades de control (que dependen de como tengas montada
la integracion de Sungrow en HA).
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..settings import EnergiaHAConfig, Settings
from .base import AdapterError, NoConfigurado
from .energia_base import (
    MODOS_BATERIA,
    MODOS_EMS,
    ConCacheDeEstado,
    EstadoEnergia,
    detalle_forzado,
    validar_orden_bateria,
)
from .homeassistant import HomeAssistant

# Un sensor de HA puede estar "unavailable", "unknown" o vacio.
NO_DISPONIBLE = {"unavailable", "unknown", "none", "", None}

# El mismo que en `Sungrow`: las dos fuentes de energia se comportan igual.


class EnergiaHA(ConCacheDeEstado):
    def __init__(
        self, settings: Settings, ha: HomeAssistant, config: EnergiaHAConfig
    ) -> None:
        self._s = settings
        self._ha = ha
        self._cfg = config
        self._max_w = settings.sungrow_max_potencia_w

    @property
    def configurado(self) -> bool:
        # Hace falta HA y, como minimo, saber de donde sale la produccion solar.
        return self._ha.configurado and bool(self._cfg.solar)


    @property
    def puede_controlar(self) -> bool:
        return bool(self._cfg.control_comando and self._cfg.control_potencia)

    async def cerrar(self) -> None:
        # El cliente HTTP es el del adaptador de Home Assistant, que se cierra
        # por su cuenta. Cerrarlo aqui tambien lo dejaria inservible para el
        # resto de herramientas.
        return None

    # --- Lectura ---------------------------------------------------------
    async def _valor(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        try:
            datos = await self._ha.estado(entity_id)
        except AdapterError:
            return None
        crudo = datos.get("state")
        if isinstance(crudo, str) and crudo.strip().lower() in NO_DISPONIBLE:
            return None
        try:
            return float(crudo)
        except (TypeError, ValueError):
            return None

    async def _leer_estado(self) -> EstadoEnergia:
        """Lee todos los sensores en paralelo y compone el estado."""
        if not self.configurado:
            raise NoConfigurado(
                "Energia via Home Assistant sin configurar. Necesita HA_TOKEN y, "
                "en config/config.yaml, la seccion `energia_ha:` con al menos el "
                "sensor `solar`. Busca los entity_id con casa_buscar_entidades."
            )
        c = self._cfg
        claves = [
            "solar", "bateria_potencia", "bateria_soc", "red", "consumo",
            "bateria_salud", "bateria_temperatura",
        ]
        valores = dict(
            zip(
                claves,
                await asyncio.gather(*(self._valor(getattr(c, k)) for k in claves)),
                strict=True,
            )
        )

        if valores["solar"] is None:
            raise AdapterError(
                f"El sensor de produccion solar '{c.solar}' no da un numero "
                "utilizable (puede estar 'unavailable'). Revisa en Home Assistant "
                "que ese entity_id existe y tiene valor."
            )

        pv = int(round(valores["solar"] * c.factor_solar))
        bateria = valores["bateria_potencia"]
        red = valores["red"]

        bateria_w = int(round(bateria * c.factor_bateria)) if bateria is not None else 0
        red_w = int(round(red * c.factor_red)) if red is not None else 0

        # El consumo, si hay sensor propio se usa; si no, se deriva del balance.
        if valores["consumo"] is not None:
            consumo = int(round(valores["consumo"] * c.factor_consumo))
        else:
            consumo = max(0, pv - bateria_w - red_w)

        modo = await self._leer_modo()

        estado = EstadoEnergia(
            pv_w=pv,
            bateria_w=bateria_w,
            bateria_soc=valores["bateria_soc"],
            red_w=red_w,
            consumo_casa_w=consumo,
            bateria_soh=valores["bateria_salud"],
            bateria_temp_c=valores["bateria_temperatura"],
            modo_ems=modo,
            origen="home_assistant",
            crudo={
                "sensores": {k: getattr(c, k) for k in claves if getattr(c, k)},
                "sin_lectura": [k for k, v in valores.items() if v is None],
            },
        )
        return estado

    async def _leer_modo(self) -> int | None:
        """El modo EMS suele ser un `select` con el nombre del modo, no un numero."""
        if not self._cfg.control_modo_ems:
            return None
        try:
            datos = await self._ha.estado(self._cfg.control_modo_ems)
        except AdapterError:
            return None
        texto = str(datos.get("state", "")).strip().lower()
        for numero, nombre in MODOS_EMS.items():
            if nombre in texto or texto in nombre:
                return numero
        # "Self-consumption mode (default)" y similares en ingles
        if "self" in texto or "consum" in texto:
            return 0
        if "forc" in texto or "forz" in texto:
            return 2
        return None

    # --- Control ---------------------------------------------------------
    async def fijar_modo_bateria(
        self, modo: str, potencia_w: int | None = None
    ) -> dict[str, Any]:
        """Controla la bateria a traves de las entidades de HA declaradas.

        A diferencia de Modbus, aqui no hay registros: depende de que tu
        integracion de Sungrow en Home Assistant exponga entidades de control.
        Si no estan declaradas, se dice claramente en vez de fingir.
        """
        if not self.puede_controlar:
            raise AdapterError(
                "Solo puedo LEER la energia, no controlarla: este sistema esta "
                "leyendo via Home Assistant y no hay entidades de control "
                "declaradas. Para controlar la bateria tienes dos opciones: "
                "conectar por Modbus directo (SUNGROW_HOST), o declarar "
                "`control_comando`, `control_potencia` y `control_modo_ems` en la "
                "seccion `energia_ha:` de config/config.yaml."
            )

        modo = modo.strip().lower()
        c = self._cfg
        if modo not in MODOS_BATERIA:
            raise AdapterError(f"Modo '{modo}' no valido. Usa: {', '.join(MODOS_BATERIA)}.")

        if modo == "autoconsumo":
            await self._seleccionar(c.control_comando, c.opcion_parar)
            if c.control_modo_ems:
                await self._seleccionar(c.control_modo_ems, c.opcion_autoconsumo)
            self.invalidar_estado()
            return {
                "modo": "autoconsumo",
                "via": "home_assistant",
                "detalle": "El inversor vuelve a decidir por si mismo.",
            }

        if modo == "parar":
            await self._seleccionar(c.control_comando, c.opcion_parar)
            self.invalidar_estado()
            return {"modo": "parar", "via": "home_assistant",
                    "detalle": "Bateria en reposo forzado."}

        modo, potencia = validar_orden_bateria(modo, potencia_w, self._max_w)
        if c.control_modo_ems:
            await self._seleccionar(c.control_modo_ems, c.opcion_forzado)
        await self._fijar_numero(c.control_potencia, potencia)
        await self._seleccionar(
            c.control_comando, c.opcion_cargar if modo == "cargar" else c.opcion_descargar
        )
        self.invalidar_estado()
        return {
            "modo": modo,
            "potencia_w": potencia,
            "via": "home_assistant",
            "detalle": detalle_forzado(modo, potencia),
        }

    async def _seleccionar(self, entity_id: str | None, opcion: str) -> None:
        if not entity_id:
            return
        dominio = entity_id.split(".", 1)[0]
        if dominio == "select":
            await self._ha.llamar_servicio(
                "select", "select_option", {"entity_id": entity_id, "option": opcion}
            )
        elif dominio in ("switch", "input_boolean"):
            servicio = "turn_on" if opcion.lower() in ("on", "true", "1") else "turn_off"
            await self._ha.llamar_servicio(dominio, servicio, {"entity_id": entity_id})
        else:
            raise AdapterError(
                f"No se como fijar '{entity_id}': esperaba una entidad `select.` o "
                "`switch.`. Revisa la seccion `energia_ha:` de config/config.yaml."
            )

    async def _fijar_numero(self, entity_id: str | None, valor: int) -> None:
        if not entity_id:
            return
        dominio = entity_id.split(".", 1)[0]
        if dominio not in ("number", "input_number"):
            raise AdapterError(
                f"No se como fijar '{entity_id}': esperaba una entidad `number.`. "
                "Revisa la seccion `energia_ha:` de config/config.yaml."
            )
        await self._ha.llamar_servicio(
            dominio, "set_value", {"entity_id": entity_id, "value": valor}
        )
