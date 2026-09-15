"""La integracion de Home Assistant no se puede ejecutar aqui (HA no esta
instalado), pero si se puede comprobar que esta bien formada y que habla con
el endpoint que existe."""

from __future__ import annotations

import ast
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
COMPONENTE = RAIZ / "custom_components" / "casa_ai"


def test_el_manifiesto_es_valido() -> None:
    m = json.loads((COMPONENTE / "manifest.json").read_text("utf-8"))
    assert m["domain"] == "casa_ai" and m["config_flow"] is True
    assert "conversation" in m["dependencies"]
    # Home Assistant carga translations/<idioma>.json; strings.json es la
    # fuente de la que el core genera traducciones, y aqui seria una copia.
    es = json.loads((COMPONENTE / "translations" / "es.json").read_text("utf-8"))
    datos = es["config"]["step"]["user"]["data"]
    assert datos == {"url": "URL del backend", "token": "Token del API"}


def test_todos_los_ficheros_compilan_y_llaman_a_voz() -> None:
    for ruta in COMPONENTE.glob("*.py"):
        ast.parse(ruta.read_text("utf-8"), filename=str(ruta))
    conversacion = (COMPONENTE / "conversation.py").read_text("utf-8")
    assert '/voz"' in conversacion and '"dispositivo": dispositivo' in conversacion
    # La identidad es el aparato que oyo la frase, nunca el texto.
    assert "user_input.device_id" in conversacion
