"""Configuracion central: secretos por variables de entorno, inventario por YAML.

Los secretos (tokens, API keys) NUNCA van en el YAML: se leen del entorno o del
fichero .env. El YAML describe el inventario fisico de la casa (reproductores
BluOS, direcciones de grupo KNX de ONNA, camaras, alias de entidades) que es
informacion no sensible y comoda de editar a mano.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Inventario (config/config.yaml)
# ---------------------------------------------------------------------------


class BluOSPlayer(BaseModel):
    """Un reproductor BluOS (Bluesound / NAD / Blue Note) en la red local."""

    nombre: str
    host: str
    puerto: int = 11000
    zona: str | None = None


class KNXGroupAddress(BaseModel):
    """Una direccion de grupo del bus KNX de ONNA.

    `tipo_valor` es el DPT que entiende xknx: "binary", "percent",
    "temperature", "1byte_unsigned", etc. Se usa tanto para leer como para
    escribir, asi que debe coincidir con el datapoint real del bus.
    """

    nombre: str
    direccion: str
    tipo_valor: str = "binary"
    descripcion: str | None = None
    solo_lectura: bool = False


class Dispositivo(BaseModel):
    """Un aparato de la casa con significado, no solo un entity_id.

    Existe porque el agente necesita saber que ES cada cosa para decidir bien.
    Un `switch.wallbox_carga` no le dice nada; "el cargador del coche, 7,4 kW,
    candidato a excedente solar, prioridad 2" le permite responder a "aprovecha
    el sol que sobra" sin que nadie se lo explique cada vez.

    La mayoria de estos aparatos llegan a traves de Home Assistant (Wallbox,
    TaHoma, Nuki, HEOS, LG ThinQ, Solem, spa...), que es la capa de
    integracion. Aqui solo se les da nombre y sentido.
    """

    nombre: str
    # Categoria libre e informativa: "cargador_vehiculo", "spa", "riego",
    # "electrodomestico", "climatizacion", "audio", "acceso"...
    categoria: str = "otro"
    # Entidad principal, la que se enciende y apaga.
    entidad: str | None = None
    # Entidades auxiliares por rol, para que el agente sepa donde mirar:
    # {"potencia": "sensor.x", "corriente": "number.y", "temperatura": "..."}
    entidades: dict[str, str] = Field(default_factory=dict)
    # Consumo nominal en vatios. Es lo que permite razonar sobre el excedente.
    consumo_w: int | None = None
    # Si es candidato a encenderse cuando sobra sol.
    excedente: bool = False
    # Orden de reparto del excedente: 1 primero. Empates por consumo.
    prioridad: int = 5
    notas: str | None = None


class EnergiaHAConfig(BaseModel):
    """Mapeo de las magnitudes energeticas a sensores de Home Assistant.

    Solo hace falta si lees la energia via HA en vez de por Modbus directo
    (ver `ENERGIA_ORIGEN` y `adapters/energia_ha.py`). Busca los entity_id con
    la herramienta casa_buscar_entidades o en las herramientas de desarrollo
    de Home Assistant.

    Los `factor_*` convierten unidades y corrigen signos: si tu sensor da kW
    pon 1000, y si el signo esta al contrario pon -1 (o -1000).
    """

    solar: str | None = None
    bateria_potencia: str | None = None
    bateria_soc: str | None = None
    bateria_salud: str | None = None
    bateria_temperatura: str | None = None
    red: str | None = None
    consumo: str | None = None

    factor_solar: float = 1.0
    factor_bateria: float = 1.0
    factor_red: float = 1.0
    factor_consumo: float = 1.0

    # Control opcional. Depende de que tu integracion de Sungrow en HA exponga
    # estas entidades; sin ellas el sistema solo lee.
    control_modo_ems: str | None = None
    control_comando: str | None = None
    control_potencia: str | None = None

    # Nombres exactos de las opciones de los `select` de control, tal y como
    # aparecen en Home Assistant (varian con el idioma y la integracion).
    opcion_autoconsumo: str = "Self-consumption mode (default)"
    opcion_forzado: str = "Forced mode"
    opcion_cargar: str = "Forced charge"
    opcion_descargar: str = "Forced discharge"
    opcion_parar: str = "Stop (default)"


class Senal(BaseModel):
    """Un registro Modbus y como se decodifica.

    Las direcciones van en BASE 0, la de pymodbus: el documento de Sungrow las
    numera desde 1 y el de Janitza desde 0, asi que al copiar de un PDF hay
    que restar uno solo en el primer caso.
    """

    registro: int
    tipo: Literal["u16", "i16", "u32", "i32", "f32"] = "u16"
    escala: float = 1.0
    funcion: Literal["input", "holding"] = "input"
    # Sungrow manda los 32 bits con la palabra baja primero; Janitza con la alta.
    palabra_alta_primero: bool = False


class Escritura(BaseModel):
    """Un registro que se escribe al dar una orden a la bateria.

    O lleva un `valor` fijo, o es el registro de potencia y lleva la escala
    en la que el equipo la espera (1 = W, 0.001 = kW).
    """

    registro: int
    valor: int | None = None
    potencia_escala: float | None = None


class EquipoModbus(BaseModel):
    """Un equipo detras del registrador, o con IP propia.

    Sin `host` se habla con el del registrador (SUNGROW_HOST); el controlador
    de la bateria puede tener su propia IP y entonces se declara aqui.
    """

    host: str | None = None
    puerto: int | None = None
    unit: int = 1
    # Lo declarado aqui se suma al mapa que el adaptador conoce para ese
    # equipo (y lo pisa, senal a senal). Una senal con nombre nuevo se lee y
    # sale en `crudo`, que es como se casan registros contra iSolarCloud.
    senales: dict[str, Senal] = Field(default_factory=dict)
    # Solo la bateria: las escrituras de cada modo. Sin esto solo se lee.
    ordenes: dict[str, list[Escritura]] = Field(default_factory=dict)


class Planta(BaseModel):
    """Planta comercial: inversor de cadena + bateria + contador por el Logger1000.

    Es la instalacion a la que el adaptador del inversor hibrido no llega: ahi
    la bateria y el contador son registros del propio inversor; aqui son tres
    equipos distintos que se leen cada uno por su id de esclavo.
    """

    inversor: EquipoModbus = Field(default_factory=EquipoModbus)
    contador: EquipoModbus | None = None
    bateria: EquipoModbus | None = None
    # El contador da positivo = importando (Janitza); el sistema quiere
    # positivo = exportando y lo invierte. Si iSolarCloud lo contradice, esto.
    invertir_signo_red: bool = False
    # Positivo = la bateria carga. Igual: si sale al reves, se invierte aqui.
    invertir_signo_bateria: bool = False


class Camara(BaseModel):
    """Una camara y como se la nombra en cada sistema.

    Un identificador por sistema, como ya hacia `Dispositivo` con `entidad`:
    `id_protect` guardaba a veces un `entity_id` de Home Assistant y se
    distinguia mirando si empezaba por "camera.". Los dos son opcionales porque
    una casa puede tener solo uno de los dos caminos.
    """

    nombre: str
    id_protect: str | None = None
    entidad_ha: str | None = None
    zona: str | None = None


Nivel = Literal["dueno", "adulto", "nino"]

# Que es alguien que habla por un canal sin estar declarado en `personas:`.
# Lo decide el canal, no el inventario: la terminal, el API y las rutinas
# solo los usa quien tiene el token o la maquina, asi que son del dueno; un
# satelite de voz sin declarar es el cuarto del nino hasta que se diga lo
# contrario. Telegram y WhatsApp no estan: ahi quien no esta declarado es nino
# si hay personas, y adulto si la casa no las declara (como hasta ahora).
NIVEL_SIN_DECLARAR: dict[str, Nivel] = {
    "cli": "dueno",
    "http": "dueno",
    "rutina": "dueno",
    "voz": "nino",
}


class Persona(BaseModel):
    """Quien puede hablar con la casa y con que permisos.

    La identidad es el canal (el chat de Telegram, el numero de WhatsApp, el
    aparato desde el que habla), nunca la voz ni lo que diga el texto: una voz
    se clona y un texto se inyecta.

    Niveles: `dueno` y `adulto` tienen hoy las mismas herramientas; `nino`
    solo las marcadas `para_ninos` (luces, musica, persianas, cuanto sol hay),
    sin camaras, sin red, sin tocar la bateria y sin acciones de riesgo.
    """

    nombre: str
    nivel: Nivel = "adulto"
    telegram: list[str] = Field(default_factory=list)
    whatsapp: list[str] = Field(default_factory=list)
    # Identidades del canal de voz (un satelite en su cuarto), cuando exista.
    dispositivos: list[str] = Field(default_factory=list)

    def responde_a(self, canal: str, usuario: str) -> bool:
        identidades = {
            "telegram": self.telegram, "whatsapp": self.whatsapp, "voz": self.dispositivos,
        }
        return usuario in identidades.get(canal, [])

    @property
    def es_nino(self) -> bool:
        return self.nivel == "nino"


class Asistente(BaseModel):
    """Como se llama el agente y como trata a la gente.

    Va en el inventario y no en `Settings` porque es parte de la casa, como las
    zonas, y entra en el prompt del sistema con el resto del inventario. El
    caracter no se configura: esta escrito en el prompt, y lo que cambia por
    casa es el nombre y si habla de usted.
    """

    nombre: str = "Jarvis"
    tratamiento: Literal["usted", "tu"] = "usted"


class Inventario(BaseModel):
    """Inventario fisico de la instalacion, cargado del YAML."""

    asistente: Asistente = Field(default_factory=Asistente)
    # Sin personas declaradas, todo chat autorizado es un adulto (como hasta
    # ahora). Con personas declaradas, quien no este en la lista es un nino:
    # falla cerrado, igual que la autorizacion.
    personas: list[Persona] = Field(default_factory=list)
    zonas: list[str] = Field(default_factory=list)
    bluos: list[BluOSPlayer] = Field(default_factory=list)
    knx: list[KNXGroupAddress] = Field(default_factory=list)
    camaras: list[Camara] = Field(default_factory=list)
    energia_ha: EnergiaHAConfig = Field(default_factory=EnergiaHAConfig)
    # Declarada, la energia por Modbus es la planta (Logger1000) y no el
    # inversor hibrido. Basta `planta: {}` para el inversor con el mapa por
    # defecto; el contador y la bateria se declaran cuando se sepa su mapa.
    planta: Planta | None = None
    dispositivos: list[Dispositivo] = Field(default_factory=list)
    # Alias en lenguaje natural -> entity_id de Home Assistant.
    # Permite decir "apaga el salon" sin que el agente adivine el entity_id.
    alias_entidades: dict[str, str] = Field(default_factory=dict)
    # Un `script.` de Home Assistant puede hacer CUALQUIER cosa, incluido abrir
    # una cerradura, asi que se salta la exclusion de `lock.unlock` de la lista
    # blanca de servicios. Por eso los scripts son denegados por defecto y solo
    # se permiten los declarados aqui.
    scripts_permitidos: list[str] = Field(default_factory=list)
    notas_casa: str = ""

    def persona_de(self, canal: str, usuario: str) -> Persona | None:
        """Quien habla, por canal e identidad.

        Primero la tabla de personas; si no esta, el nivel que el canal
        declara en `NIVEL_SIN_DECLARAR`; y si el canal no declara nada, nino
        cuando la casa tiene personas y None (todo autorizado es adulto)
        cuando no las tiene.
        """
        for persona in self.personas:
            if persona.responde_a(canal, usuario):
                return persona
        nivel = NIVEL_SIN_DECLARAR.get(canal)
        if nivel is not None:
            return Persona(nombre=f"{canal} sin registrar", nivel=nivel)
        if not self.personas:
            return None
        return Persona(nombre="alguien sin registrar", nivel="nino")

    def resolver_alias(self, referencia: str) -> str:
        """Un alias en lenguaje natural a entity_id, si esta declarado.

        Es el unico sitio que normaliza: estaba en la herramienta de casa y
        la rutina del informe lo rehacia sin `strip().lower()`, asi que
        «Cocina» resolvia en un sitio y en el otro no.
        """
        return self.alias_entidades.get(referencia.strip().lower(), referencia.strip())

    def bluos_por_nombre(self, nombre: str) -> BluOSPlayer | None:
        objetivo = nombre.strip().lower()
        for p in self.bluos:
            if p.nombre.lower() == objetivo or (p.zona or "").lower() == objetivo:
                return p
        return None

    def knx_por_nombre(self, nombre: str) -> KNXGroupAddress | None:
        objetivo = nombre.strip().lower()
        for ga in self.knx:
            if ga.nombre.lower() == objetivo or ga.direccion == nombre.strip():
                return ga
        return None

    def dispositivo_por_nombre(self, nombre: str) -> Dispositivo | None:
        objetivo = nombre.strip().lower()
        for d in self.dispositivos:
            if d.nombre.lower() == objetivo or d.entidad == nombre.strip():
                return d
        return None

    def gestionables_por_excedente(self) -> list[Dispositivo]:
        """Candidatos a excedente, ya ordenados por prioridad y consumo."""
        return sorted(
            (d for d in self.dispositivos if d.excedente and d.entidad),
            key=lambda d: (d.prioridad, -(d.consumo_w or 0)),
        )

    def camara_por_nombre(self, nombre: str) -> Camara | None:
        objetivo = nombre.strip().lower()
        for c in self.camaras:
            if c.nombre.lower() == objetivo:
                return c
            if nombre.strip() in (c.id_protect, c.entidad_ha):
                return c
        return None


# ---------------------------------------------------------------------------
# Secretos y endpoints (variables de entorno / .env)
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Claude ---
    anthropic_api_key: str | None = None
    modelo: str = "claude-opus-5"
    esfuerzo: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    max_tokens: int = 16000
    # Fallback gestionado por servidor: si un clasificador de seguridad rechaza
    # la peticion, la API reencamina a otro modelo en vez de devolver nada.
    fallbacks_servidor: bool = True

    # --- Home Assistant (frontal de ONNA/KNX y del resto de la domotica) ---
    ha_url: str = "http://homeassistant.local:8123"
    ha_token: str | None = None

    # --- KNX directo (bus de ONNA) ---
    knx_habilitado: bool = False
    knx_gateway_ip: str | None = None
    knx_gateway_puerto: int = 3671
    # route_back=True hace falta si el backend corre tras NAT (Docker bridge).
    knx_route_back: bool = True

    # --- Sungrow (inversor hibrido + bateria) ---
    sungrow_host: str | None = None
    # Alternativa al host: la MAC del registrador. El router de Vodafone
    # reparte las IP y no se pueden reservar, asi que el adaptador la busca en
    # la lista de clientes de UniFi y la vuelve a buscar si deja de responder.
    # Sin host ni MAC, con la planta declarada, prueba todos los equipos
    # Sungrow que UniFi ve y se queda con el que contesta como registrador.
    sungrow_mac: str | None = None
    sungrow_puerto: int = 502
    sungrow_slave_id: int = 1
    # Potencia maxima que el agente puede ordenar a la bateria, en vatios.
    # Tope de seguridad: ninguna herramienta puede superarlo.
    sungrow_max_potencia_w: int = 5000
    # Ponlo en true si el diagnostico muestra el signo de la red al contrario
    # que iSolarCloud (varia entre firmwares). Ver `python -m casa_ai.verificar`.
    sungrow_invertir_signo_red: bool = False
    # De donde se lee la energia:
    #   auto           - Modbus si hay SUNGROW_HOST, si no Home Assistant
    #   modbus         - solo Modbus directo al inversor (exige estar en su red)
    #   homeassistant  - solo sensores de HA (permite ejecutar fuera de casa, y
    #                    convive con HA leyendo el inversor por Modbus)
    energia_origen: Literal["auto", "modbus", "homeassistant"] = "auto"
    # Gestion del excedente solar. La potencia que entra en la bateria solo
    # cuenta como excedente redirigible cuando la bateria ya va llena: antes
    # de eso, cargarla es mejor uso que encender un aparato.
    excedente_soc_minimo: int = 90
    # Margen que se deja sin usar, para no pasarse a importar de la red por un
    # pico de consumo o una nube.
    excedente_margen_w: int = 300

    # --- UniFi ---
    unifi_host: str | None = None
    unifi_puerto: int = 443
    unifi_usuario: str | None = None
    unifi_password: str | None = None
    unifi_site: str = "default"
    # Verificar por defecto: por este canal viajan las credenciales del
    # administrador local de UniFi. Las consolas traen certificado
    # autofirmado, asi que exporta el suyo y apuntalo con UNIFI_CA_BUNDLE en
    # vez de desactivar la verificacion.
    unifi_verificar_tls: bool = True
    unifi_ca_bundle: str | None = None
    # No hay PROTECT_*: Protect vive detras de la misma consola UniFi OS y
    # reutiliza la sesion de UNIFI_*. Tenerlos declarados prometia una
    # configuracion que ningun codigo leia.

    # --- Canales de conversacion ---
    telegram_token: str | None = None
    # Solo estos chat_id pueden dar ordenes. Vacio = nadie (fail closed).
    telegram_chats_autorizados: str = ""
    whatsapp_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_verify_token: str | None = None
    # Secreto de la app de Meta, para verificar la firma X-Hub-Signature-256.
    # Sin el, cualquiera que alcance el webhook puede falsificar el remitente:
    # el numero viaja en el cuerpo de la peticion. Si falta, no se atiende.
    whatsapp_app_secret: str | None = None
    whatsapp_numeros_autorizados: str = ""

    # --- Voz de salida (ElevenLabs): la voz disenada con `python -m casa_ai.voz` ---
    elevenlabs_api_key: str | None = None
    # La entidad de texto a voz de Home Assistant que lleva la voz de Jarvis
    # (p. ej. `tts.elevenlabs`). Sin ella, el agente no puede hablar por los
    # altavoces y la herramienta no se ofrece.
    tts_entidad: str | None = None
    # Altavoz (media_player de HA, o alias del inventario) por el que se lee
    # el informe de la manana. Solo el informe: un aviso de vigilancia a las
    # tres de la madrugada por el altavoz del dormitorio no es un aviso.
    rutinas_altavoz: str | None = None

    # --- Transcripcion de notas de voz (local, sin nube) ---
    whisper_modelo: str = "small"
    whisper_dispositivo: str = "cpu"
    whisper_compute_type: str = "int8"

    # --- Seguridad del canal HTTP ---
    # Sin esto los endpoints no se sirven: el canal HTTP da el mismo control de
    # la casa que Telegram, asi que abrirlo sin autenticacion no es una opcion.
    # Genera uno con: python -c "import secrets; print(secrets.token_urlsafe(32))"
    api_token: str | None = None
    # Por defecto solo escucha en localhost. Si lo abres a la red, pon el token.
    api_host: str = "127.0.0.1"
    api_puerto: int = 8099

    # --- Operacion ---
    config_path: Path = Path("config/config.yaml")
    db_path: Path = Path("data/casa_ai.sqlite3")
    log_level: str = "INFO"
    # Toda accion de riesgo alto pide confirmacion explicita antes de ejecutarse.
    exigir_confirmacion: bool = True
    zona_horaria: str = "Europe/Madrid"

    @property
    def chats_telegram(self) -> set[int]:
        # Los chat_id de grupo son negativos, de ahi el lstrip("-").
        return {int(x) for x in _csv(self.telegram_chats_autorizados) if x.lstrip("-").isdigit()}

    @property
    def numeros_whatsapp(self) -> set[str]:
        return set(_csv(self.whatsapp_numeros_autorizados))

    @property
    def verificacion_tls_unifi(self) -> bool | str:
        """Lo que espera httpx en `verify`: ruta al CA, o True/False."""
        if self.unifi_ca_bundle:
            return self.unifi_ca_bundle
        return self.unifi_verificar_tls

    def avisos_de_seguridad(self) -> list[str]:
        """Configuraciones que funcionan pero dejan la instalacion expuesta."""
        avisos: list[str] = []
        if not self.unifi_verificar_tls and not self.unifi_ca_bundle:
            avisos.append(
                "UNIFI_VERIFICAR_TLS esta desactivado: las credenciales del "
                "administrador local de UniFi viajan por un canal que no se "
                "puede verificar. Exporta el certificado de la consola y "
                "apuntalo con UNIFI_CA_BUNDLE."
            )
        if self.ha_url.startswith("http://") and not self.ha_url.startswith(
            ("http://localhost", "http://127.0.0.1")
        ):
            avisos.append(
                f"HA_URL usa http sin cifrar ({self.ha_url}): el token de "
                "Home Assistant, que da control total de la casa, viaja en "
                "claro por la red. Y un nombre .local se resuelve por mDNS, "
                "que cualquier equipo de la red puede suplantar."
            )
        if self.api_expuesta_sin_token:
            avisos.append(
                f"El API escucha en {self.api_host} sin API_TOKEN: cualquiera "
                "que alcance el puerto controlaria la casa. Los endpoints se "
                "serviran cerrados hasta que definas el token."
            )
        if not self.exigir_confirmacion:
            avisos.append(
                "EXIGIR_CONFIRMACION esta desactivado: las acciones de riesgo "
                "alto se ejecutan sin preguntar."
            )
        return avisos

    @property
    def api_expuesta_sin_token(self) -> bool:
        """El API escucha fuera de localhost y no hay token.

        Un solo sitio para la condicion: el aviso de arranque, el SystemExit de
        `run()` y el 503 del autenticador la evaluaban por separado, con la
        tupla de hosts locales escrita dos veces.
        """
        return self.api_host not in ("127.0.0.1", "localhost", "::1") and not self.api_token

    def cargar_inventario(self) -> Inventario:
        if not self.config_path.exists():
            return Inventario()
        datos: dict[str, Any] = yaml.safe_load(self.config_path.read_text("utf-8")) or {}
        return Inventario.model_validate(datos)


def _csv(valor: str) -> list[str]:
    """Los valores no vacios de una lista separada por comas."""
    return [trozo.strip() for trozo in valor.split(",") if trozo.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_inventario() -> Inventario:
    return get_settings().cargar_inventario()


def api_key_presente() -> bool:
    """La SDK tambien acepta perfiles OAuth de `ant auth login`, no solo la env var."""
    s = get_settings()
    return bool(s.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
