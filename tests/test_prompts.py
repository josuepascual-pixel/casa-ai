"""El prompt del sistema debe llevar el inventario real y nada volatil."""

from __future__ import annotations

from casa_ai.agent.prompts import construir_system
from casa_ai.settings import Inventario, Settings


def test_incluye_el_inventario_de_la_casa(settings: Settings, inventario: Inventario) -> None:
    prompt = construir_system(settings, inventario)

    assert "Salon" in prompt and "Cocina" in prompt      # reproductores
    assert "Puerta" in prompt                             # camaras
    assert "light.salon" in prompt                        # alias
    assert "2/1/10" in prompt                             # direcciones KNX
    assert "Europe/Madrid" in prompt


def test_no_lleva_nada_volatil(settings: Settings, inventario: Inventario) -> None:
    """Si el prompt cambiara en cada mensaje, la cache no serviria de nada."""
    primero = construir_system(settings, inventario)
    segundo = construir_system(settings, inventario)
    assert primero == segundo


def test_avisa_si_la_confirmacion_esta_desactivada(
    settings: Settings, inventario: Inventario
) -> None:
    peligroso = settings.model_copy(update={"exigir_confirmacion": False})
    assert "DESACTIVADA" in construir_system(peligroso, inventario)
    assert "DESACTIVADA" not in construir_system(settings, inventario)


def test_funciona_con_inventario_vacio(settings: Settings) -> None:
    prompt = construir_system(settings, Inventario())
    assert "ONNA" in prompt and "BluOS" in prompt and "Sungrow" in prompt


def test_lleva_el_nombre_y_el_trato_del_inventario(settings: Settings) -> None:
    """El nombre sale del YAML; el caracter no, para que no se pueda perder."""
    por_defecto = construir_system(settings, Inventario())
    assert por_defecto.startswith("Eres Jarvis, ")
    assert "Tratas de usted" in por_defecto
    assert "Tony Stark" in por_defecto

    otro = Inventario.model_validate({"asistente": {"nombre": "Alfred", "tratamiento": "tu"}})
    prompt = construir_system(settings, otro)
    assert prompt.startswith("Eres Alfred, ") and "Jarvis" not in prompt
    assert "Tuteas" in prompt and "Tratas de usted" not in prompt
