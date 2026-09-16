"""Registro de herramientas y contexto de ejecucion.

Una `Herramienta` junta tres cosas que en la API de Claude van separadas: el
esquema que ve el modelo, el nivel de riesgo que aplica la capa de seguridad y
la funcion Python que ejecuta la accion de verdad. Tenerlas juntas evita el
fallo clasico de anadir una herramienta potente y olvidarse de clasificarla.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from ..adapters.bluos import BluOS
    from ..adapters.camaras_base import FuenteCamaras
    from ..adapters.energia_base import FuenteEnergia
    from ..adapters.homeassistant import HomeAssistant
    from ..adapters.knx_onna import KNXOnna
    from ..adapters.unifi import UniFi
    from ..settings import Inventario, Persona, Settings
    from ..store import Store


Confirmacion = Literal["en_banda", "boton", "imposible"]


class Riesgo(StrEnum):
    """Cuanto dano puede hacer una herramienta si el agente se equivoca."""

    LECTURA = "lectura"   # solo consulta, se ejecuta sin preguntar
    MEDIO = "medio"       # cambio reversible y evidente (luz, volumen, escena)
    ALTO = "alto"         # exige confirmacion explicita del usuario


@dataclass
class Adjunto:
    """Un archivo que el agente entrega junto a su respuesta.

    Un programa, un documento largo, un CSV: cosas que se guardan o se abren
    en otro sitio y que en una burbuja de chat no caben o no sirven.
    """

    nombre: str
    contenido: str


@dataclass
class Contexto:
    """Todo lo que una herramienta puede necesitar para ejecutarse."""

    settings: Settings
    inventario: Inventario
    store: Store
    ha: HomeAssistant
    # Cualquiera de las dos fuentes: Modbus directo o los sensores de Home
    # Assistant. El Protocol es lo que quita los `getattr` defensivos.
    energia: FuenteEnergia
    knx: KNXOnna
    musica: BluOS
    unifi: UniFi
    # UniFi Protect directo o el proxy de Home Assistant, lo que haya.
    camaras: FuenteCamaras
    canal: str = "cli"
    usuario: str = "local"
    # Conversacion en curso. Una accion pendiente queda atada a ella, y solo se
    # puede confirmar en un turno POSTERIOR de esa misma conversacion.
    conversacion: str = "cli:local"
    # Como puede confirmar una accion de riesgo el humano que hay al otro lado.
    # Son tres situaciones distintas, no dos, y la tercera es la que un bool no
    # sabia decir: en una rutina programada NO HAY NADIE. Con el bool, ese caso
    # caia en la rama de token por defecto, asi que un turno programado que
    # tocase riesgo alto recibia en su contexto un token vivo de 128 bits y la
    # instruccion de pedir confirmacion, y ese texto salia por Telegram.
    #
    #   "en_banda"  el token se le da al modelo, que llama a la herramienta de
    #               confirmacion cuando el usuario diga que si (HTTP, CLI).
    #   "boton"     el canal manda un boton y la pulsacion llama al codigo
    #               directamente; el token NUNCA entra en el contexto del
    #               modelo, asi que una inyeccion no tiene con que trabajar.
    #   "imposible" no hay humano escuchando (rutinas): no se crea pendiente ni
    #               se emite token, y se le dice al modelo que lo senale.
    confirmacion: Confirmacion = "en_banda"
    # Quien habla, resuelto por el canal. None cuando la casa no declara
    # personas: entonces todo autorizado es adulto.
    persona: Persona | None = None
    # Archivos que el agente ha entregado en este turno. Es del turno, no del
    # contexto base: `Aplicacion.contexto_para` estrena la lista cada vez.
    adjuntos: list[Adjunto] = field(default_factory=list)

    @property
    def es_nino(self) -> bool:
        return self.persona is not None and self.persona.es_nino

    @property
    def confirmacion_fuera_de_banda(self) -> bool:
        return self.confirmacion == "boton"

    async def cerrar(self) -> None:
        for adaptador in (
            self.ha, self.energia, self.knx, self.musica, self.unifi, self.camaras
        ):
            try:
                await adaptador.cerrar()
            except Exception:  # pragma: no cover - cierre best-effort
                pass


# El handler recibe el contexto y los argumentos ya validados por la API.
# Devuelve texto, un dict/lista serializable, o una lista de bloques de
# contenido (para devolver imagenes de camara al modelo).
Handler = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class Cualquiera:
    """Basta con que uno de estos adaptadores este configurado.

    Las camaras, por ejemplo, funcionan por UniFi Protect o por Home Assistant.
    """

    nombres: tuple[str, ...]

    def __init__(self, *nombres: str) -> None:
        object.__setattr__(self, "nombres", nombres)

    def cumple(self, adaptadores: dict[str, Any]) -> bool:
        return any(adaptadores[n].configurado for n in self.nombres)

    def faltan(self, adaptadores: dict[str, Any]) -> str:
        return " o ".join(self.nombres)


@dataclass(frozen=True)
class Todos:
    """Hacen falta todos. El excedente solar necesita ver el inversor Y los consumos."""

    nombres: tuple[str, ...]

    def __init__(self, *nombres: str) -> None:
        object.__setattr__(self, "nombres", nombres)

    def cumple(self, adaptadores: dict[str, Any]) -> bool:
        return all(adaptadores[n].configurado for n in self.nombres)

    def faltan(self, adaptadores: dict[str, Any]) -> str:
        return ", ".join(n for n in self.nombres if not adaptadores[n].configurado)


Requisito = str | Cualquiera | Todos


@dataclass
class Herramienta:
    nombre: str
    descripcion: str
    esquema: dict[str, Any]
    riesgo: Riesgo
    handler: Handler
    # Frase en lenguaje natural para pedir confirmacion. Recibe los argumentos.
    resumen_confirmacion: Callable[[dict[str, Any]], str] | None = None
    # Adaptador(es) que necesita. Si el requisito no se cumple, la herramienta
    # no se ofrece al modelo: mejor que no exista que ofrecerla y fallar
    # siempre.
    #
    # El combinador es explicito a proposito. Antes una tupla significaba "O",
    # y la conjuncion se expresaba con un `disponible_si` que repetia los
    # mismos nombres: dos call sites identicos a la vista significaban cosas
    # contrarias, y anadir un tercer adaptador a la tupla sin tocar el lambda
    # ofrecia la herramienta mal sin que nada lo detectara.
    requiere: Requisito | None = None
    # Condicion extra, para lo que "estar configurado" no puede decir: una
    # CAPACIDAD. La usa el control de bateria, porque leer la energia via Home
    # Assistant no implica poder controlarla. Las conjunciones de adaptadores
    # van en `requiere` con `Todos`, no aqui.
    disponible_si: Callable[[Contexto], bool] | None = None
    # Si un nino puede usarla. Lista blanca: lo que no se marca, no se ofrece.
    # Ninguna de riesgo alto lo es, y hay un test que lo fija.
    para_ninos: bool = False

    def definicion_api(self) -> dict[str, Any]:
        """Formato que espera el parametro `tools` de la Messages API."""
        return {
            "name": self.nombre,
            "description": self.descripcion,
            "input_schema": self.esquema,
            # strict garantiza que los argumentos validan contra el esquema,
            # asi el handler no tiene que defenderse de tipos raros.
            "strict": True,
        }

    def resumir(self, argumentos: dict[str, Any]) -> str:
        if self.resumen_confirmacion is not None:
            return self.resumen_confirmacion(argumentos)
        return f"{self.nombre}({argumentos})"


@dataclass
class Registro:
    herramientas: dict[str, Herramienta] = field(default_factory=dict)

    def anadir(self, *herramientas: Herramienta) -> None:
        for h in herramientas:
            if h.nombre in self.herramientas:
                raise ValueError(f"Herramienta duplicada: {h.nombre}")
            self.herramientas[h.nombre] = h

    def get(self, nombre: str) -> Herramienta | None:
        return self.herramientas.get(nombre)

    def disponibles(self, ctx: Contexto) -> list[Herramienta]:
        """Herramientas cuyos adaptadores estan configurados.

        El orden es alfabetico y por tanto estable entre peticiones. Eso
        importa para la cache de prompt: `tools` se renderiza antes que
        `system`, asi que un orden variable invalidaria la cache entera en
        cada mensaje.
        """
        adaptadores = _adaptadores(ctx)
        activas = [
            h for h in self.herramientas.values() if _disponible(h, ctx, adaptadores)
        ]
        return sorted(activas, key=lambda h: h.nombre)

    def ausentes(self, ctx: Contexto) -> dict[str, str]:
        """Las herramientas que NO se ofrecen y por que.

        La pregunta que se hace quien esta montando la casa es "por que no
        puedo pedirle esto"; antes solo se podia listar lo activo.
        """
        adaptadores = _adaptadores(ctx)
        motivos: dict[str, str] = {}
        for nombre, h in sorted(self.herramientas.items()):
            if _disponible(h, ctx, adaptadores):
                continue
            requisito = _como_requisito(h.requiere)
            if ctx.es_nino and not h.para_ninos:
                motivos[nombre] = f"no es para ninos ({ctx.persona.nombre})"  # type: ignore[union-attr]
            elif requisito is not None and not requisito.cumple(adaptadores):
                motivos[nombre] = f"falta configurar: {requisito.faltan(adaptadores)}"
            else:
                motivos[nombre] = "el sistema configurado no puede hacerlo"
        return motivos

    def disponible(self, nombre: str, ctx: Contexto) -> Herramienta | None:
        """La herramienta si existe Y se le ofrece a quien habla.

        El Ejecutor la usa en vez de `get`: la API ya impide llamar a una
        herramienta no ofrecida, pero un nino no debe poder ejecutar una de
        adultos ni aunque el modelo se la invente.
        """
        h = self.herramientas.get(nombre)
        if h is None or not _disponible(h, ctx, _adaptadores(ctx)):
            return None
        return h

    def definiciones_api(self, ctx: Contexto) -> list[dict[str, Any]]:
        return [h.definicion_api() for h in self.disponibles(ctx)]


def _adaptadores(ctx: Contexto) -> dict[str, Any]:
    return {
        "ha": ctx.ha,
        "energia": ctx.energia,
        "knx": ctx.knx,
        "musica": ctx.musica,
        "unifi": ctx.unifi,
        "camaras": ctx.camaras,
    }


def _como_requisito(requiere: Requisito | None) -> Cualquiera | Todos | None:
    if requiere is None:
        return None
    return Cualquiera(requiere) if isinstance(requiere, str) else requiere


def _disponible(h: Herramienta, ctx: Contexto, adaptadores: dict[str, Any]) -> bool:
    if ctx.es_nino and not h.para_ninos:
        return False
    requisito = _como_requisito(h.requiere)
    if requisito is not None and not requisito.cumple(adaptadores):
        return False
    return h.disponible_si is None or h.disponible_si(ctx)


def esquema(
    propiedades: dict[str, Any], obligatorias: list[str] | None = None
) -> dict[str, Any]:
    """Atajo para esquemas JSON compatibles con `strict: true`.

    `strict` exige `additionalProperties: false` y que `required` liste todas
    las propiedades; las opcionales se declaran admitiendo null.
    """
    # Construye dicts nuevos en vez de escribir dentro de los del llamante:
    # hoy funciona porque cada herramienta pasa literales de un solo uso, pero
    # el dia que dos compartan un fragmento de esquema, la primera llamada se
    # lo dejaria ya convertido a la segunda.
    fijas = set(obligatorias or ())
    return {
        "type": "object",
        "properties": {
            nombre: prop if nombre in fijas else _admite_null(prop)
            for nombre, prop in propiedades.items()
        },
        "required": list(propiedades),
        "additionalProperties": False,
    }


def _admite_null(prop: dict[str, Any]) -> dict[str, Any]:
    tipo = prop.get("type")
    if isinstance(tipo, str) and tipo != "null":
        return {**prop, "type": [tipo, "null"]}
    return prop
