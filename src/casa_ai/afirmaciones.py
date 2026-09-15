"""Reconocer un «si» o un «no» sin pasar por el modelo.

En los canales sin botones (API, terminal, satelite de voz) la confirmacion
de una accion de riesgo la daba el modelo llamando a una herramienta con un
token que se le habia entregado. Eso hacia que la garantia fuese «paso un
turno humano», no «el humano dijo que si»: una inyeccion en cualquier texto
del turno siguiente podia hacer que el modelo la llamase. Ahora el si lo
reconoce el codigo, aqui, y el token nunca entra en el contexto.

Es deliberadamente estricto: solo frases cortas que son claramente un si o
un no. Cualquier otra cosa cancela la propuesta, y si el usuario la queria,
la vuelve a pedir. Mejor pedir dos veces que ejecutar una vez de mas.
"""

from __future__ import annotations

import re
import unicodedata

_PRIMERAS_AFIRMATIVAS = frozenset({
    "si", "vale", "ok", "okay", "confirmo", "confirmado", "confirma", "confirmar",
    "adelante", "hazlo", "dale", "venga", "procede", "afirmativo", "correcto",
})
_FRASES_AFIRMATIVAS = frozenset({
    "de acuerdo", "por supuesto", "claro", "claro que si", "que si", "esta bien",
    "me parece bien", "si por favor", "si claro", "si hazlo", "si adelante",
    "si confirmo", "si dale", "si venga", "vale hazlo", "ok hazlo",
})
_PRIMERAS_NEGATIVAS = frozenset({
    "no", "cancela", "cancelar", "cancelalo", "dejalo", "para", "nada", "olvidalo",
    "negativo", "anula", "anulalo",
})
_FRASES_NEGATIVAS = frozenset({"mejor no", "no gracias", "no lo hagas", "que no", "ni hablar"})

MAX_PALABRAS = 4


def _normalizar(texto: str) -> list[str]:
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9 ]+", " ", sin_acentos).split()


def _es(texto: str, primeras: frozenset[str], frases: frozenset[str]) -> bool:
    palabras = _normalizar(texto)
    if not palabras or len(palabras) > MAX_PALABRAS:
        return False
    return " ".join(palabras) in frases or palabras[0] in primeras


def es_afirmacion(texto: str) -> bool:
    return _es(texto, _PRIMERAS_AFIRMATIVAS, _FRASES_AFIRMATIVAS) and not es_negacion(texto)


def es_negacion(texto: str) -> bool:
    return _es(texto, _PRIMERAS_NEGATIVAS, _FRASES_NEGATIVAS)
