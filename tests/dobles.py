"""Dobles compartidos por los tests.

Estaban copiados en once ficheros y ya habian divergido: dos stubs llamados
`nada` que se diferenciaban en `configurado`, y descripciones de herramienta de
distinta longitud. Al estar aqui, la divergencia no puede volver.
"""

from __future__ import annotations

from typing import Any

import httpx
import respx

from casa_ai.adapters.camaras_base import elegir_camaras
from casa_ai.agent.registry import Contexto
from casa_ai.app import Aplicacion
from casa_ai.settings import Inventario, Settings
from casa_ai.store import Store

from .conftest import HA_URL

# Los adaptadores del Contexto, en el orden en que los declara el dataclass.
ADAPTADORES = ("ha", "energia", "knx", "musica", "unifi")


def adaptador(configurado: bool = False, **atributos: Any) -> Any:
    """Adaptador de mentira: lo minimo que el Contexto y el registro miran.

    `atributos` anade metodos o banderas concretas (`puede_controlar`,
    `estado`...) sin tener que escribir una clase para cada test.
    """

    async def cerrar(_self: Any) -> None:
        return None

    cuerpo: dict[str, Any] = {
        "configurado": configurado,
        # Un stub configurado se comporta como la fuente completa salvo que el
        # test diga otra cosa: `puede_controlar` es de `FuenteEnergia` y
        # `via`/`tiene_eventos` de `FuenteCamaras`.
        "puede_controlar": configurado,
        "tiene_eventos": configurado,
        # Hablar no viene con estar configurado: hace falta TTS_ENTIDAD.
        "puede_hablar": False,
        "via": "doble",
        "cerrar": cerrar,
    }
    cuerpo.update(atributos)
    return type("AdaptadorFalso", (), cuerpo)()


def contexto(
    settings: Settings,
    inventario: Inventario,
    store: Store,
    *,
    por_defecto: bool = False,
    **partes: Any,
) -> Contexto:
    """Contexto con adaptadores falsos.

    En `partes`, un adaptador puede venir como objeto (el doble de ese test) o
    como bool, que construye un stub configurado o sin configurar. Los que no
    se nombran caen en `por_defecto`: sin configurar, que es lo que hace que el
    registro no ofrezca sus herramientas.
    """
    adaptadores = {
        nombre: _resolver(partes.pop(nombre, por_defecto)) for nombre in ADAPTADORES
    }
    # La fuente de camaras se ELIGE con el elector de verdad a partir de lo que
    # el test haya puesto en unifi y ha. Copiarla aqui seria dejar de probarla.
    camaras = partes.pop(
        "camaras",
        elegir_camaras(adaptadores["unifi"], adaptadores["ha"], inventario),
    )
    return Contexto(
        settings=settings, inventario=inventario, store=store,
        **adaptadores, camaras=_resolver(camaras), **partes,
    )


def _resolver(valor: Any) -> Any:
    return adaptador(valor) if isinstance(valor, bool) else valor


class StoreFalso:
    """Doble del Store para los canales: solo turnos y pendientes."""

    def __init__(self, turno: int = 3) -> None:
        self.turno = turno
        self.pendientes: list[dict[str, Any]] = []
        self.turnos_pedidos: list[int | None] = []
        self.limpiadas: list[str] = []
        self.registros: list[dict[str, Any]] = []

    def turno_actual(self, conversacion: str) -> int:
        return self.turno

    def pendientes_de(
        self, conversacion: str, *, turno: int | None = None
    ) -> list[dict[str, Any]]:
        self.turnos_pedidos.append(turno)
        if turno is None:
            return self.pendientes
        return [p for p in self.pendientes if p.get("turno") == turno]

    def limpiar_conversacion(self, conversacion: str) -> None:
        self.limpiadas.append(conversacion)

    def auditoria(self, limite: int = 10) -> list[dict[str, Any]]:
        return self.registros

    def detalle_pendiente(self, token: str) -> dict[str, Any] | None:
        for p in self.pendientes:
            if p["token"] == token:
                return {"estado": "pendiente", "canal": "telegram", "usuario": "555", **p}
        return None

    def bloqueo(self) -> dict[str, Any] | None:
        return getattr(self, "_bloqueo", None)

    def bloquear(self, quien: str) -> None:
        self._bloqueo = {"quien": quien, "ts": 0.0}

    def desbloquear(self) -> None:
        self._bloqueo = None


class AplicacionFalsa:
    """Doble de Aplicacion: registra los turnos, no habla con ningun modelo."""

    def __init__(
        self,
        settings: Settings,
        respuesta: str = "hecho",
        *,
        store: Any = None,
        detalle: str = "cargando a 3000 W",
    ) -> None:
        self.settings = settings
        self.inventario = Inventario()
        self.nombre_asistente = "Jarvis"
        self.respuesta = respuesta
        self.detalle = detalle
        self.store = StoreFalso() if store is None else store
        self.turnos: list[dict[str, Any]] = []
        self.confirmadas: list[dict[str, Any]] = []
        self.canceladas: list[dict[str, Any]] = []

    async def responder(self, **kwargs: Any) -> str:
        self.turnos.append(kwargs)
        return self.respuesta

    async def confirmar_pendiente(self, **kwargs: Any) -> tuple[str, bool]:
        self.confirmadas.append(kwargs)
        return (self.detalle, False)

    def cancelar_pendiente(self, **kwargs: Any) -> bool:
        self.canceladas.append(kwargs)
        return True

    # Estos dos NO se re-implementan: son la politica de botones, y un doble
    # que la copiase dejaria de probarla. Se toman prestados de la clase real,
    # que para esto solo necesita `store`, `cancelar_pendiente` y
    # `confirmar_pendiente`, y los tres estan aqui.
    pendientes_para_ofrecer = Aplicacion.pendientes_para_ofrecer
    resolver_pulsacion = Aplicacion.resolver_pulsacion
    pulsacion_es_de = Aplicacion.pulsacion_es_de

    def resumen_configuracion(self) -> dict[str, Any]:
        return {"home_assistant": True, "unifi": False, "herramientas_activas": ["a", "b"]}

    def chats_de_duenos(self) -> set[int]:
        return Aplicacion.chats_de_duenos(self)  # type: ignore[arg-type]

    def es_dueno(self, canal: str, usuario: str) -> bool:
        return Aplicacion.es_dueno(self, canal, usuario)  # type: ignore[arg-type]

    async def comprobar(self) -> str:
        return (
            "✅ Home Assistant: 3 entidades\n❌ Musica BluOS: nadie responde\n"
            "🔒 Seguridad: sin configuraciones expuestas"
        )

    async def sondear_planta(self, texto: str = "sondear") -> str:
        self.sondeos = getattr(self, "sondeos", []) + [texto]
        return "id 1: responde. tipo 0x2C0B"

    async def descubrir(self, red: str | None = None) -> str:
        self.redes_pedidas = getattr(self, "redes_pedidas", []) + [red]
        return "Encontrados 2 equipos\nbluos:\n  - nombre: Salon"


# --- Home Assistant simulado ------------------------------------------------
#
# Los tests hablaban con "http://ha.test:8123" a mano en cinco ficheros, con la
# URL que en realidad declara la fixture `settings`. Estos ayudantes son el
# unico sitio que la conoce.


def estados_ha(*entidades: dict[str, Any]) -> Any:
    """Simula `GET /api/states`, la lista entera."""
    return respx.get(f"{HA_URL}/api/states").mock(
        return_value=httpx.Response(200, json=list(entidades))
    )


def estado_ha(entity_id: str, estado: str, **atributos: Any) -> Any:
    """Simula `GET /api/states/<entity_id>`, una sola entidad."""
    return respx.get(f"{HA_URL}/api/states/{entity_id}").mock(
        return_value=httpx.Response(
            200,
            json={"entity_id": entity_id, "state": estado, "attributes": atributos},
        )
    )


def entidad(entity_id: str, estado: str = "on", **atributos: Any) -> dict[str, Any]:
    """Una entidad tal como la devuelve HA, para pasarla a `estados_ha`."""
    return {"entity_id": entity_id, "state": estado, "attributes": atributos}


def servicio_ha(dominio: str, servicio: str) -> Any:
    """Simula `POST /api/services/<dominio>/<servicio>`."""
    return respx.post(f"{HA_URL}/api/services/{dominio}/{servicio}").mock(
        return_value=httpx.Response(200, json=[])
    )
