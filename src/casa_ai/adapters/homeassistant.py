"""Adaptador de Home Assistant: el frontal de toda la domotica.

Home Assistant es la pieza central del sistema porque su integracion `knx`
habla directamente con el bus KNX donde vive ONNA, y ademas expone como
entidades normales el resto de aparatos (climatizacion, persianas, luces,
sensores). El agente trabaja casi siempre contra HA; solo baja al bus KNX
crudo cuando hace falta una direccion de grupo que HA no expone.

API usada: REST (`/api/states`, `/api/services/...`, `/api/history/...`) con
token de acceso de larga duracion en cabecera Bearer.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..settings import Settings
from .base import AdapterError, NoConfigurado, contacto

# Mas corto que cualquier accion humana, para que un cambio se vea en la
# siguiente pregunta. Lo que colapsa son las repeticiones dentro de un mismo
# turno y las pasadas del panel.
CACHE_ESTADOS_S = 3.0

# Servicios permitidos por dominio. Una lista blanca evita que el agente llame
# a servicios administrativos de HA (homeassistant.stop, hassio.*, recorder.*)
# a traves de la herramienta generica de acciones.
SERVICIOS_PERMITIDOS: dict[str, set[str]] = {
    "light": {"turn_on", "turn_off", "toggle"},
    "switch": {"turn_on", "turn_off", "toggle"},
    # TaHoma/Somfy: toldos y persianas orientables necesitan tambien el tilt
    "cover": {
        "open_cover", "close_cover", "stop_cover", "set_cover_position",
        "open_cover_tilt", "close_cover_tilt", "stop_cover_tilt",
        "set_cover_tilt_position",
    },
    "climate": {"set_temperature", "set_hvac_mode", "set_fan_mode", "turn_on", "turn_off"},
    "scene": {"turn_on"},
    "script": {"turn_on"},
    "media_player": {
        "turn_on", "turn_off", "volume_set", "media_play", "media_pause",
        "media_stop", "media_next_track", "media_previous_track",
    },
    "fan": {"turn_on", "turn_off", "set_percentage"},
    # Hablar por un altavoz. Solo `speak`: `clear_cache` y compania no pintan nada.
    "tts": {"speak"},
    "lock": {"lock"},  # `unlock` queda fuera a proposito: no se abre por chat
    "input_boolean": {"turn_on", "turn_off", "toggle"},
    # select/number hacen falta para controlar la bateria via Home Assistant
    # cuando no hay Modbus directo (ver adapters/energia_ha.py).
    "select": {"select_option"},
    "number": {"set_value"},
    "input_number": {"set_value"},
    # Spa (ControlMySpa) y termo electrico
    "water_heater": {"set_temperature", "set_operation_mode", "turn_on", "turn_off"},
    # Riego (Solem) y electrovalvulas
    "valve": {"open_valve", "close_valve", "set_valve_position"},
    "humidifier": {"turn_on", "turn_off", "set_humidity"},
    # Procesador de AV (Anthem). `send_command` queda fuera: es un canal libre
    # por el que se puede mandar cualquier cosa al equipo.
    "remote": {"turn_on", "turn_off"},
    # Electrodomesticos (LG ThinQ). Arrancar una lavadora a distancia es
    # razonable; `cancel` y demas se quedan fuera por no conocer el estado real.
    "button": {"press"},
    "vacuum": {"start", "pause", "return_to_base"},
    "alarm_control_panel": {"alarm_arm_home", "alarm_arm_away"},  # desarmar, no
}


# Lo que un nino puede tocar y mirar. Ni enchufes (la bomba de la piscina es
# un switch), ni clima, ni cerraduras, ni camaras (su estado trae el token del
# stream), ni personas (dicen quien esta en casa): la lista blanca es lo que no
# tiene consecuencias si se pulsa cien veces ni cuenta nada que no deba.
DOMINIOS_PARA_NINOS = frozenset({"light", "media_player", "cover", "fan"})

# Un `cover` puede ser una persiana o la puerta del garaje: Home Assistant los
# distingue por `device_class`. Abrir uno de estos es abrir la casa, y va por
# la misma puerta estrecha que la cerradura: riesgo alto, nunca un nino.
ACCESOS = frozenset({"garage", "gate", "door"})
SERVICIOS_QUE_ABREN = frozenset({"open_cover", "set_cover_position", "toggle", "open_cover_tilt"})


def es_acceso(estado: dict[str, Any]) -> bool:
    return str((estado.get("attributes") or {}).get("device_class", "")) in ACCESOS


_ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_DOMINIO = re.compile(r"^[a-z0-9_]+$")


def entity_id_normalizado(entity_id: str) -> str:
    """El identificador tal y como lo entiende Home Assistant, o error.

    HA pasa a minusculas y acepta listas separadas por comas en el historico,
    e ignora lo que va tras `?` en la ruta. Comparar el texto literal contra
    las entidades privadas dejaba pasar `Input_number.peso_ana`,
    `input_number.peso_ana?x` o `a,input_number.peso_ana`. Aqui se reduce a
    una forma unica antes de mirar nada.
    """
    limpio = str(entity_id).strip().lower()
    if not _ENTITY_ID.match(limpio):
        raise AdapterError(
            f"'{entity_id}' no es un identificador de entidad valido "
            "(dominio.nombre, solo minusculas, numeros y guion bajo)."
        )
    return limpio


def _dominio_normalizado(dominio: str) -> str:
    limpio = str(dominio).strip().lower()
    if not _DOMINIO.match(limpio):
        raise AdapterError(f"'{dominio}' no es un dominio valido de Home Assistant.")
    return limpio


def _dominio(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


class HomeAssistantRestringido:
    """La vista de Home Assistant que recibe un nino.

    Esta en el adaptador y no en las herramientas porque asi NINGUNA
    herramienta puede saltarselo: `dispositivos_estado` leia el estado de
    cada aparato sin mirar el dominio, y cualquier herramienta nueva marcada
    para ninos volveria a tener que acordarse. Aqui se acuerda el mecanismo.
    """

    def __init__(
        self,
        ha: HomeAssistant,
        dominios: frozenset[str] | None = None,
        ocultas: frozenset[str] = frozenset(),
    ) -> None:
        self._ha = ha
        # None = todos los dominios (un adulto al que solo se le ocultan las
        # entidades privadas de los demas).
        self._dominios = dominios
        self._ocultas = ocultas

    def _permitida(self, entity_id: str) -> bool:
        if entity_id in self._ocultas:
            return False
        return self._dominios is None or _dominio(entity_id) in self._dominios

    def _vetar(self, entity_id: str) -> str:
        """Comprueba una entidad y devuelve su identificador normalizado."""
        limpio = entity_id_normalizado(entity_id)
        if limpio in self._ocultas:
            # Con las mismas palabras que una entidad inexistente: decir «es
            # privada de otro» ya cuenta algo.
            raise AdapterError(f"No hay ninguna entidad '{entity_id}' en Home Assistant.")
        self._vetar_dominio(_dominio(limpio))
        return limpio

    def _vetar_dominio(self, dominio: str) -> None:
        if self._dominios is not None and dominio not in self._dominios:
            raise AdapterError(
                f"'{dominio}' no es algo que un nino pueda tocar ni consultar "
                f"desde aqui (solo {', '.join(sorted(self._dominios))}). Dile que se lo "
                "pida a sus padres."
            )

    def _filtrar(self, entidades: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [e for e in entidades if self._permitida(str(e.get("entity_id", "")))]

    @property
    def configurado(self) -> bool:
        return self._ha.configurado

    @property
    def puede_hablar(self) -> bool:
        return self._ha.puede_hablar

    async def cerrar(self) -> None:
        # El cliente es del adaptador de verdad, que se cierra por su cuenta.
        return None

    def invalidar(self) -> None:
        self._ha.invalidar()

    async def precalentar(self) -> None:
        await self._ha.precalentar()

    async def estados(self) -> list[dict[str, Any]]:
        return self._filtrar(await self._ha.estados())

    async def estado(self, entity_id: str) -> dict[str, Any]:
        return await self._ha.estado(self._vetar(entity_id))

    async def buscar_entidades(
        self, *, dominio: str | None = None, texto: str | None = None, limite: int = 60
    ) -> list[dict[str, Any]]:
        if dominio:
            self._vetar_dominio(_dominio_normalizado(dominio))
        encontradas = await self._ha.buscar_entidades(dominio=dominio, texto=texto, limite=limite)
        return self._filtrar(encontradas)

    async def historico(
        self, entity_id: str, *, horas: int = 24, max_puntos: int = 120
    ) -> list[dict[str, Any]]:
        limpio = self._vetar(entity_id)
        return await self._ha.historico(limpio, horas=horas, max_puntos=max_puntos)

    async def snapshot_camara(self, entity_id: str) -> bytes:
        limpio = self._vetar(entity_id)
        return await self._ha.snapshot_camara(limpio)  # pragma: no cover - camera no entra

    async def hablar(self, texto: str, altavoz: str) -> dict[str, Any]:
        return await self._ha.hablar(texto, self._vetar(altavoz))

    async def llamar_servicio(
        self, dominio: str, servicio: str, datos: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        dominio = _dominio_normalizado(dominio)
        self._vetar_dominio(dominio)
        datos = _con_entidades_normalizadas(datos)
        for eid in _entidades_de(datos):
            self._vetar(eid)
            es_nino = self._dominios is not None
            if es_nino and dominio == "cover" and es_acceso(await self._ha.estado(eid)):
                raise AdapterError(
                    f"'{eid}' es una puerta o un porton, no una persiana: eso no lo "
                    "abre ni lo cierra un nino. Dile que se lo pida a sus padres."
                )
        return await self._ha.llamar_servicio(dominio, servicio, datos)


def _entidades_de(datos: dict[str, Any] | None) -> list[str]:
    valor = (datos or {}).get("entity_id")
    if isinstance(valor, str):
        return [valor]
    return [str(v) for v in valor or []]


def _con_entidades_normalizadas(datos: dict[str, Any] | None) -> dict[str, Any]:
    """Los mismos datos, con cada entidad en su forma unica (o error).

    Una cadena con comas es lo que HA entiende como lista: se valida cada
    trozo, y lo que llega al veto y al servicio es la lista ya limpia.
    """
    salida = dict(datos or {})
    for clave in ("entity_id", "media_player_entity_id"):
        valor = salida.get(clave)
        if valor is None:
            continue
        trozos = valor.split(",") if isinstance(valor, str) else list(valor)
        limpios = [entity_id_normalizado(t) for t in trozos]
        salida[clave] = limpios[0] if len(limpios) == 1 else limpios
    return salida


class HomeAssistant:
    def restringido_a(
        self, dominios: frozenset[str] | None = None, *, ocultas: frozenset[str] = frozenset()
    ) -> HomeAssistantRestringido:
        return HomeAssistantRestringido(self, dominios, ocultas)

    def __init__(
        self, settings: Settings, scripts_permitidos: list[str] | None = None
    ) -> None:
        self._url = settings.ha_url.rstrip("/")
        self._token = settings.ha_token
        self._tts = settings.tts_entidad
        self._cliente: httpx.AsyncClient | None = None
        # Un `script.` de Home Assistant ejecuta una secuencia arbitraria, asi
        # que puede hacer lo que la lista blanca de servicios prohibe: un
        # script llamado "bienvenida" puede abrir la cerradura. Por eso los
        # scripts se deniegan salvo los declarados en config/config.yaml.
        self._scripts = set(scripts_permitidos or ())
        # Cache muy corta de /api/states. Con el panel abierto (refresco cada
        # 10 s) y un turno del agente que busca entidades tres o cuatro veces,
        # esto colapsa decenas de peticiones a Home Assistant en una. El TTL es
        # deliberadamente mas corto que cualquier accion humana: nadie enciende
        # una luz y pregunta por ella en menos de tres segundos.
        self._cache: tuple[float, list[dict[str, Any]]] | None = None
        self._lock = asyncio.Lock()

    @property
    def configurado(self) -> bool:
        return bool(self._token)

    async def _http(self) -> httpx.AsyncClient:
        if not self.configurado:
            raise NoConfigurado(
                "Home Assistant sin configurar: falta HA_TOKEN (token de acceso "
                "de larga duracion, se crea en el perfil de usuario de HA)."
            )
        if self._cliente is None:
            self._cliente = httpx.AsyncClient(
                base_url=self._url,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(20.0, connect=5.0),
            )
        return self._cliente

    async def cerrar(self) -> None:
        if self._cliente is not None:
            await self._cliente.aclose()
            self._cliente = None

    async def _get(self, ruta: str, **params: Any) -> Any:
        cliente = await self._http()
        with contacto("Home Assistant"):
            try:
                r = await cliente.get(
                    ruta, params={k: v for k, v in params.items() if v is not None}
                )
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise AdapterError(
                    f"Home Assistant respondio {e.response.status_code} a GET {ruta}"
                ) from e
            return r.json()

    # --- Lectura ---------------------------------------------------------
    def _frescos(self) -> list[dict[str, Any]] | None:
        if self._cache and time.monotonic() - self._cache[0] < CACHE_ESTADOS_S:
            return self._cache[1]
        return None

    def invalidar(self) -> None:
        """Tira la cache. Se llama al escribir, no despues de un rato.

        Sin esto, un `casa_accion` seguido de `casa_estado` en el mismo turno
        contaria el estado viejo, que es peor que no cachear nada.
        """
        self._cache = None

    async def estados(self) -> list[dict[str, Any]]:
        frescos = self._frescos()
        if frescos is not None:
            return frescos
        async with self._lock:
            # Otro pudo haberla rellenado mientras esperabamos el lock: un
            # turno pide entidades en paralelo.
            frescos = self._frescos()
            if frescos is not None:
                return frescos
            todas: list[dict[str, Any]] = await self._get("/api/states")
            self._cache = (time.monotonic(), todas)
            return todas

    async def precalentar(self) -> None:
        """Trae la lista una vez para que las lecturas sueltas salgan de ella.

        La llama quien sabe que va a leer muchas entidades seguidas (el panel,
        el reparto de excedente). Sin esto cada aparato era su propio GET:
        ~33 por pasada del panel donde basta uno. No propaga el fallo: si no se
        puede, cada lectura suelta dara su propio error, que es mas util.
        """
        try:
            await self.estados()
        except AdapterError:
            pass

    async def estado(self, entity_id: str) -> dict[str, Any]:
        """Una entidad. Se sirve de la cache de `estados()` si esta fresca.

        No se cachea por entidad: la lista entera ya la tenemos, y pedir una
        por una era lo que hacia que el panel disparase ~33 GET por pasada.
        """
        entity_id = entity_id_normalizado(entity_id)
        frescos = self._frescos()
        if frescos is not None:
            for e in frescos:
                if e.get("entity_id") == entity_id:
                    return e
        return await self._get(f"/api/states/{entity_id}")

    async def buscar_entidades(
        self, *, dominio: str | None = None, texto: str | None = None, limite: int = 60
    ) -> list[dict[str, Any]]:
        """Busca entidades por dominio y/o texto en entity_id o friendly_name."""
        todas = await self.estados()
        texto_low = (texto or "").lower()
        salida: list[dict[str, Any]] = []
        for e in todas:
            eid: str = e.get("entity_id", "")
            if dominio and not eid.startswith(f"{dominio}."):
                continue
            nombre = str(e.get("attributes", {}).get("friendly_name", ""))
            if texto_low and texto_low not in eid.lower() and texto_low not in nombre.lower():
                continue
            salida.append(
                {
                    "entity_id": eid,
                    "nombre": nombre,
                    "estado": e.get("state"),
                    "unidad": e.get("attributes", {}).get("unit_of_measurement"),
                }
            )
            if len(salida) >= limite:
                break
        return salida

    async def historico(
        self, entity_id: str, *, horas: int = 24, max_puntos: int = 120
    ) -> list[dict[str, Any]]:
        """Historico de una entidad. Se submuestrea para no saturar el contexto."""
        entity_id = entity_id_normalizado(entity_id)
        desde = (datetime.now(UTC) - timedelta(hours=horas)).isoformat()
        datos = await self._get(
            f"/api/history/period/{desde}",
            filter_entity_id=entity_id,
            minimal_response="true",
        )
        if not datos or not isinstance(datos, list) or not datos[0]:
            return []
        serie = datos[0]
        paso = max(1, len(serie) // max_puntos)
        return [
            {"ts": p.get("last_changed") or p.get("last_updated"), "valor": p.get("state")}
            for p in serie[::paso]
        ]

    def _comprobar_script(self, datos: dict[str, Any] | None) -> None:
        objetivo = str((datos or {}).get("entity_id", ""))
        if not self._scripts:
            raise AdapterError(
                "Los scripts de Home Assistant estan denegados. Un script puede "
                "ejecutar cualquier secuencia, incluido abrir una cerradura, asi "
                "que se salta la lista blanca de servicios. Si quieres poder "
                "llamar a alguno, declaralo en `scripts_permitidos:` en "
                "config/config.yaml."
            )
        if objetivo not in self._scripts:
            raise AdapterError(
                f"El script '{objetivo}' no esta en `scripts_permitidos:`. "
                f"Permitidos: {', '.join(sorted(self._scripts))}."
            )

    async def snapshot_camara(self, entity_id: str) -> bytes:
        """Captura JPEG de una entidad `camera.` a traves de Home Assistant.

        Es la via que hace que la vision siga funcionando cuando el backend no
        esta en la red de las camaras (por ejemplo corriendo en la nube): en vez
        de hablar con UniFi Protect directamente, se le pide la imagen a HA,
        que si esta en casa.
        """
        entity_id = entity_id_normalizado(entity_id)
        cliente = await self._http()
        with contacto("Home Assistant"):
            try:
                r = await cliente.get(f"/api/camera_proxy/{entity_id}")
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise AdapterError(
                    f"Home Assistant no dio imagen de '{entity_id}' "
                    f"(HTTP {e.response.status_code}). Comprueba que es una entidad "
                    "`camera.` y que esta disponible."
                ) from e
        if not r.content:
            raise AdapterError(f"Home Assistant devolvio una imagen vacia de '{entity_id}'.")
        return r.content

    # --- Escritura -------------------------------------------------------
    @property
    def puede_hablar(self) -> bool:
        return bool(self._tts)

    async def hablar(self, texto: str, altavoz: str) -> dict[str, Any]:
        """Lee `texto` por un media_player con la voz de Jarvis.

        La voz la pone la entidad de texto a voz de Home Assistant (con
        ElevenLabs y el voice_id de Jarvis, o la que haya): este adaptador
        solo dice que y por donde.
        """
        if not self._tts:
            raise AdapterError(
                "No hay entidad de texto a voz configurada (TTS_ENTIDAD, p. ej. "
                "`tts.elevenlabs`). Sin ella no se puede hablar por los altavoces."
            )
        altavoz = entity_id_normalizado(altavoz)
        if not altavoz.startswith("media_player."):
            raise AdapterError(
                f"'{altavoz}' no es un altavoz de Home Assistant (media_player.*). "
                "Busca el entity_id con casa_buscar_entidades o declara un alias."
            )
        await self.llamar_servicio(
            "tts", "speak",
            {
                "entity_id": self._tts,
                "media_player_entity_id": altavoz,
                "message": texto,
                "language": "es",
            },
        )
        return {"altavoz": altavoz, "dicho": texto}

    async def llamar_servicio(
        self, dominio: str, servicio: str, datos: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        dominio = _dominio_normalizado(dominio)
        datos = _con_entidades_normalizadas(datos)
        permitidos = SERVICIOS_PERMITIDOS.get(dominio)
        if permitidos is None:
            raise AdapterError(
                f"Dominio '{dominio}' no permitido. Dominios disponibles: "
                f"{', '.join(sorted(SERVICIOS_PERMITIDOS))}."
            )
        if servicio not in permitidos:
            raise AdapterError(
                f"Servicio '{dominio}.{servicio}' no permitido. Permitidos en "
                f"'{dominio}': {', '.join(sorted(permitidos))}."
            )
        if dominio == "script":
            self._comprobar_script(datos)
        # Antes de la llamada: si falla a medias, mejor haber tirado la cache
        # de mas que servir un estado que ya no es.
        self.invalidar()

        cliente = await self._http()
        with contacto("Home Assistant"):
            try:
                r = await cliente.post(f"/api/services/{dominio}/{servicio}", json=datos or {})
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise AdapterError(
                    f"Home Assistant rechazo {dominio}.{servicio}: "
                    f"HTTP {e.response.status_code} {e.response.text[:200]}"
                ) from e
            return r.json()
