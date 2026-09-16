"""Jarvis escribe codigo, pero no lo ejecuta ni se da trabajo a si mismo.

Es la condicion del dueno para dejarle programar: que nada de lo que escriba
pueda convertirse en una accion que nadie pidio. Cada ausencia se fija aqui
para que anadir una herramienta «util» no la deshaga sin que se note.
"""

from __future__ import annotations

from casa_ai.adapters.homeassistant import SERVICIOS_PERMITIDOS
from casa_ai.agent.orchestrator import Agente
from casa_ai.agent.prompts import construir_system
from casa_ai.settings import Inventario, Settings
from casa_ai.store import Store
from casa_ai.tools import construir_registro

from .dobles import contexto

# Lo que un agente con «acceso a codigo» tendria y este no debe tener nunca.
NOMBRES_PROHIBIDOS = ("exec", "shell", "bash", "terminal", "python", "eval", "escribir_fichero")


def test_ninguna_herramienta_ejecuta_codigo_ni_toca_ficheros() -> None:
    nombres = set(construir_registro().herramientas)
    for prohibido in NOMBRES_PROHIBIDOS:
        assert not any(prohibido in n for n in nombres), prohibido
    # Entregar un archivo es dar texto al usuario, no escribirlo en disco.
    import inspect

    from casa_ai.tools import archivos

    fuente = inspect.getsource(archivos)
    assert "open(" not in fuente and "Path(" not in fuente and "write" not in fuente


def test_la_api_no_recibe_ejecucion_de_codigo(settings: Settings, store: Store) -> None:
    """Solo la busqueda web como herramienta de servidor: nada de code_execution."""
    ctx = contexto(settings, Inventario(), store, por_defecto=True)
    tipos = {h.get("type") for h in Agente(settings, construir_registro(), ctx)._herramientas()}
    assert not any(t and "code" in t for t in tipos)
    assert not any(t and "fetch" in t for t in tipos)


def test_home_assistant_no_puede_ejecutar_ni_automatizar() -> None:
    for dominio in ("shell_command", "python_script", "automation", "hassio",
                    "homeassistant", "rest_command", "command_line"):
        assert dominio not in SERVICIOS_PERMITIDOS, dominio
    assert SERVICIOS_PERMITIDOS["script"] == {"turn_on"}  # y solo los declarados


def test_un_turno_desatendido_no_puede_programar_mas_trabajo(
    settings: Settings, store: Store
) -> None:
    """Desde una orden programada no se puede programar otra: sin eso el agente
    podria encadenarse tareas sin que nadie se lo pidiera."""
    registro = construir_registro()

    def ofrecidas(**partes: object) -> set[str]:
        ctx = contexto(settings, Inventario(), store, por_defecto=True, canal="telegram", **partes)
        return {h.nombre for h in registro.disponibles(ctx)}

    con_persona = ofrecidas(confirmacion="boton")
    sin_nadie = ofrecidas(confirmacion="imposible")
    assert {"programar", "programacion_cancelar"} <= con_persona
    assert not {"programar", "programacion_cancelar"} & sin_nadie
    assert "programaciones_listar" in sin_nadie  # leer si


def test_el_prompt_le_dice_lo_que_no_puede(settings: Settings) -> None:
    prompt = construir_system(settings, Inventario())
    assert "No ejecutas codigo" in prompt
    assert "No creas automatizaciones" in prompt
    assert "No actuas por iniciativa propia" in prompt
