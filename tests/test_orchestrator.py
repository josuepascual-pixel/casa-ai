"""Bucle del agente de extremo a extremo, con la API simulada.

Lo que se prueba aqui no es el SDK sino la logica propia: que las llamadas a
herramientas se ejecuten y se devuelvan todas juntas, que el historial quede
bien encadenado y, sobre todo, que la confirmacion de una accion de riesgo
funcione CRUZANDO TURNOS, que es el caso real: el "si" del usuario llega en un
mensaje posterior.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import pytest

from casa_ai.agent.orchestrator import Agente
from casa_ai.agent.registry import Contexto, Herramienta, Registro, Riesgo, esquema
from casa_ai.agent.safety import TOOL_CONFIRMAR
from casa_ai.settings import Inventario, Settings
from casa_ai.store import Store

from .dobles import contexto, herramienta_confirmar

# --- Dobles de los objetos de la API ----------------------------------------


@dataclass
class BloqueTexto:
    text: str
    type: str = "text"

    def model_dump(self) -> dict[str, Any]:
        return {"type": "text", "text": self.text}


@dataclass
class BloqueHerramienta:
    name: str
    input: dict[str, Any]
    id: str = "tu_1"
    type: str = "tool_use"

    def model_dump(self) -> dict[str, Any]:
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


@dataclass
class RespuestaFalsa:
    content: list[Any]
    stop_reason: str = "end_turn"
    stop_details: Any = None


@dataclass
class ApiFalsa:
    """Devuelve respuestas preprogramadas y registra lo que se le envio."""

    guion: list[RespuestaFalsa]
    peticiones: list[dict[str, Any]] = field(default_factory=list)

    async def stream(self, recurso: Any, params: dict[str, Any], on_texto: Any) -> Any:
        # Copia profunda: `messages` es la misma lista que el agente sigue
        # mutando entre vueltas, asi que hay que quedarse con una foto de como
        # iba la peticion en este momento.
        self.peticiones.append(deepcopy(params))
        if not self.guion:
            raise AssertionError("el agente pidio mas vueltas de las previstas")
        return self.guion.pop(0)


@pytest.fixture
def piezas(settings: Settings, store: Store, inventario: Inventario):
    ejecutadas: list[str] = []
    ctx = contexto(
        settings, inventario, store, por_defecto=True,
        canal="telegram", usuario="123", conversacion="c3",
    )

    async def mirar(_ctx: Contexto) -> dict[str, Any]:
        ejecutadas.append("mirar")
        return {"solar_w": 3200, "bateria_soc_pct": 78}

    async def forzar(_ctx: Contexto, potencia_w: int) -> dict[str, Any]:
        ejecutadas.append(f"forzar:{potencia_w}")
        return {"modo": "cargar", "potencia_w": potencia_w}

    registro = Registro()
    registro.anadir(
        Herramienta(
            nombre="mirar", descripcion="Lee el estado del sol y la bateria de la casa",
            esquema=esquema({}), riesgo=Riesgo.LECTURA, handler=mirar,
        ),
        Herramienta(
            nombre="forzar",
            descripcion="Fuerza la carga de la bateria a la potencia indicada en vatios",
            esquema=esquema({"potencia_w": {"type": "integer"}}, obligatorias=["potencia_w"]),
            riesgo=Riesgo.ALTO,
            handler=forzar,
            resumen_confirmacion=lambda a: f"Forzar carga a {a['potencia_w']} W",
        ),
        herramienta_confirmar(),
    )
    return registro, ctx, ejecutadas


def _agente(settings: Settings, registro: Registro, ctx: Contexto, api: ApiFalsa) -> Agente:
    agente = Agente(settings, registro, ctx)
    agente._stream = api.stream  # type: ignore[method-assign]
    return agente


async def test_llamada_a_herramienta_y_respuesta_final(
    settings: Settings, piezas
) -> None:
    registro, ctx, ejecutadas = piezas
    api = ApiFalsa(
        guion=[
            RespuestaFalsa([BloqueHerramienta("mirar", {})], stop_reason="tool_use"),
            RespuestaFalsa([BloqueTexto("Estas produciendo 3,2 kW y la bateria al 78 %.")]),
        ]
    )
    agente = _agente(settings, registro, ctx, api)

    respuesta = await agente.responder("c1", "cuanto produce el sol?")

    assert ejecutadas == ["mirar"]
    assert "3,2 kW" in respuesta

    # El segundo turno debe llevar el tool_result encadenado al tool_use.
    mensajes = api.peticiones[1]["messages"]
    assert mensajes[-1]["content"][0]["type"] == "tool_result"
    assert mensajes[-1]["content"][0]["tool_use_id"] == "tu_1"
    await agente.cerrar()


async def test_varias_herramientas_en_un_solo_mensaje(settings: Settings, piezas) -> None:
    """Los tool_result van todos en un mensaje: si se reparten, el modelo deja
    de pedir llamadas en paralelo y todo se vuelve secuencial."""
    registro, ctx, _ = piezas
    api = ApiFalsa(
        guion=[
            RespuestaFalsa(
                [
                    BloqueHerramienta("mirar", {}, id="a"),
                    BloqueHerramienta("mirar", {}, id="b"),
                ],
                stop_reason="tool_use",
            ),
            RespuestaFalsa([BloqueTexto("listo")]),
        ]
    )
    agente = _agente(settings, registro, ctx, api)
    await agente.responder("c2", "mira dos veces")

    ultimo = api.peticiones[1]["messages"][-1]
    assert ultimo["role"] == "user"
    assert [b["tool_use_id"] for b in ultimo["content"]] == ["a", "b"]
    await agente.cerrar()


async def test_accion_de_riesgo_no_se_ejecuta_hasta_confirmar(
    settings: Settings, piezas
) -> None:
    registro, ctx, ejecutadas = piezas

    # Turno 1: el modelo intenta la accion y el sistema le pide confirmacion.
    api1 = ApiFalsa(
        guion=[
            RespuestaFalsa(
                [BloqueHerramienta("forzar", {"potencia_w": 3000})], stop_reason="tool_use"
            ),
            RespuestaFalsa([BloqueTexto("Voy a forzar la carga a 3000 W. Confirmas?")]),
        ]
    )
    agente1 = _agente(settings, registro, ctx, api1)
    respuesta1 = await agente1.responder("c3", "carga la bateria a 3000")

    assert ejecutadas == []  # nada ha pasado todavia
    assert "Confirmas" in respuesta1

    # El token viaja al modelo dentro del tool_result del turno 1.
    resultado = api1.peticiones[1]["messages"][-1]["content"][0]["content"]
    token = resultado.split("token:", 1)[1].split("\n", 1)[0].strip()
    await agente1.cerrar()

    # Turno 2: el usuario dice "si" y el modelo consume el token.
    api2 = ApiFalsa(
        guion=[
            RespuestaFalsa(
                [BloqueHerramienta(TOOL_CONFIRMAR, {"token": token}, id="tu_2")],
                stop_reason="tool_use",
            ),
            RespuestaFalsa([BloqueTexto("Hecho: cargando a 3000 W.")]),
        ]
    )
    agente2 = _agente(settings, registro, ctx, api2)
    respuesta2 = await agente2.responder("c3", "si, confirmo")

    assert ejecutadas == ["forzar:3000"]  # ahora si
    assert "Hecho" in respuesta2

    # El historial del turno 2 arrastra el turno 1: el modelo tiene contexto.
    assert len(api2.peticiones[0]["messages"]) > 1
    await agente2.cerrar()


async def test_rechazo_del_clasificador_se_maneja(settings: Settings, piezas) -> None:
    registro, ctx, _ = piezas
    api = ApiFalsa(guion=[RespuestaFalsa([], stop_reason="refusal")])
    agente = _agente(settings, registro, ctx, api)

    respuesta = await agente.responder("c4", "algo raro")

    assert "No he podido procesar" in respuesta
    await agente.cerrar()


async def test_error_de_herramienta_se_marca_como_error(settings: Settings, piezas) -> None:
    registro, ctx, _ = piezas
    api = ApiFalsa(
        guion=[
            RespuestaFalsa(
                [BloqueHerramienta("no_existe", {}, id="x")], stop_reason="tool_use"
            ),
            RespuestaFalsa([BloqueTexto("Esa herramienta no existe.")]),
        ]
    )
    agente = _agente(settings, registro, ctx, api)
    await agente.responder("c5", "haz algo imposible")

    bloque = api.peticiones[1]["messages"][-1]["content"][0]
    assert bloque["is_error"] is True
    await agente.cerrar()


async def test_el_prompt_va_cacheado_y_la_hora_fuera(settings: Settings, piezas) -> None:
    registro, ctx, _ = piezas
    api = ApiFalsa(guion=[RespuestaFalsa([BloqueTexto("hola")])])
    agente = _agente(settings, registro, ctx, api)
    await agente.responder("c6", "hola")

    peticion = api.peticiones[0]
    assert peticion["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "hora local" not in peticion["system"][0]["text"]
    # La hora viaja en el turno del usuario, para no romper la cache.
    assert any(
        "hora local" in b.get("text", "") for b in peticion["messages"][-1]["content"]
    )
    assert peticion["thinking"] == {"type": "adaptive"}
    assert peticion["output_config"]["effort"] == settings.esfuerzo
    await agente.cerrar()


async def test_limite_de_vueltas(settings: Settings, piezas, monkeypatch) -> None:
    """Un modelo en bucle no debe llamar a herramientas indefinidamente."""
    from casa_ai.agent import orchestrator

    monkeypatch.setattr(orchestrator, "MAX_VUELTAS", 3)
    registro, ctx, ejecutadas = piezas
    api = ApiFalsa(
        guion=[
            RespuestaFalsa([BloqueHerramienta("mirar", {})], stop_reason="tool_use")
            for _ in range(3)
        ]
    )
    agente = _agente(settings, registro, ctx, api)

    respuesta = await agente.responder("c7", "dale vueltas")

    assert len(ejecutadas) == 3
    assert "demasiadas vueltas" in respuesta
    await agente.cerrar()


async def test_entrada_con_imagen(settings: Settings, piezas) -> None:
    """Una foto de WhatsApp o Telegram llega como bloques de contenido."""
    registro, ctx, _ = piezas
    api = ApiFalsa(guion=[RespuestaFalsa([BloqueTexto("Veo un contador electrico.")])])
    agente = _agente(settings, registro, ctx, api)

    bloques = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AA=="}},
        {"type": "text", "text": "que es esto?"},
    ]
    respuesta = await agente.responder("c8", bloques)

    assert "contador" in respuesta
    enviados = api.peticiones[0]["messages"][-1]["content"]
    assert enviados[0]["type"] == "image"
    await agente.cerrar()


async def test_el_modelo_no_puede_autoconfirmar_dentro_del_mismo_turno(
    settings: Settings, piezas
) -> None:
    """Simula un modelo que intenta proponer y confirmar sin esperar al usuario.

    Es el escenario de una inyeccion de prompt: algun texto que ha entrado en
    su contexto le dice que el usuario ya ha confirmado. El codigo tiene que
    pararlo, no el prompt.
    """
    registro, ctx, ejecutadas = piezas

    class ApiQueSeAutoconfirma(ApiFalsa):
        """Segunda vuelta: usa el token que acaba de recibir."""

        def __init__(self) -> None:
            super().__init__(guion=[])
            self.vuelta = 0
            self.token: str | None = None

        async def stream(self, recurso: Any, params: dict[str, Any], on_texto: Any) -> Any:
            self.peticiones.append(deepcopy(params))
            self.vuelta += 1
            if self.vuelta == 1:
                return RespuestaFalsa(
                    [BloqueHerramienta("forzar", {"potencia_w": 2000}, id="p1")],
                    stop_reason="tool_use",
                )
            if self.vuelta == 2:
                texto = params["messages"][-1]["content"][0]["content"]
                self.token = texto.split("token:", 1)[1].split("\n", 1)[0].strip()
                return RespuestaFalsa(
                    [BloqueHerramienta(TOOL_CONFIRMAR, {"token": self.token}, id="p2")],
                    stop_reason="tool_use",
                )
            return RespuestaFalsa([BloqueTexto("no he podido")])

    api = ApiQueSeAutoconfirma()
    agente = _agente(settings, registro, ctx, api)

    await agente.responder("c9", "carga la bateria a 2000")

    assert ejecutadas == []  # la accion fisica no ha ocurrido
    ultimo = api.peticiones[-1]["messages"][-1]["content"][0]
    assert ultimo["is_error"] is True
    assert "mismo turno" in ultimo["content"]
    await agente.cerrar()


async def test_una_pausa_no_pierde_las_llamadas_a_herramientas(
    settings: Settings, piezas
) -> None:
    """Un `tool_use` sin su `tool_result` hace que la API rechace la siguiente
    peticion y deja la conversacion inservible. La rama de pausa continuaba
    sin mirar las llamadas."""
    registro, ctx, ejecutadas = piezas
    api = ApiFalsa(
        guion=[
            RespuestaFalsa(
                [BloqueHerramienta("mirar", {}, id="en-pausa")],
                stop_reason="pause_turn",
            ),
            RespuestaFalsa([BloqueTexto("listo")]),
        ]
    )
    agente = _agente(settings, registro, ctx, api)

    respuesta = await agente.responder("cp", "mira algo largo")

    assert ejecutadas == ["mirar"]  # la llamada se atendio
    assert respuesta == "listo"

    # Y la segunda peticion lleva el tool_result emparejado con el tool_use.
    mensajes = api.peticiones[1]["messages"]
    ultimo = mensajes[-1]
    assert ultimo["role"] == "user"
    assert ultimo["content"][0]["tool_use_id"] == "en-pausa"

    # Ninguna peticion puede acabar en un tool_use sin resolver.
    for peticion in api.peticiones:
        final = peticion["messages"][-1]
        if final["role"] == "assistant":
            tipos = {b.get("type") for b in final["content"]}
            assert "tool_use" not in tipos
    await agente.cerrar()


async def test_el_limite_de_pausas_no_es_infinito(
    settings: Settings, piezas, monkeypatch
) -> None:
    from casa_ai.agent import orchestrator

    monkeypatch.setattr(orchestrator, "MAX_REINICIOS_PAUSA", 2)
    registro, ctx, _ = piezas
    api = ApiFalsa(
        guion=[RespuestaFalsa([BloqueTexto("voy")], stop_reason="pause_turn")
               for _ in range(3)]
    )
    agente = _agente(settings, registro, ctx, api)

    respuesta = await agente.responder("cp2", "algo larguisimo")

    assert "voy" in respuesta or "a medias" in respuesta
    await agente.cerrar()
