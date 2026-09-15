"""Lo que comparten los canales con botones.

No el transporte: el teclado de Telegram y el interactivo de la Cloud API de
Meta son dos APIs de terceros que evolucionan por separado, y acoplarlas haria
que un cambio en una tuviera que negociar con la otra. Lo que sube aqui es lo
que no es de ninguna de las dos: como se codifica la pulsacion y como se
trocea un texto largo.
"""

from __future__ import annotations

# Prefijos del identificador que viaja en el boton. Telegram limita su
# `callback_data` a 64 bytes y WhatsApp el `id` de la respuesta a 256; con un
# token de 22 caracteres sobra en los dos.
CONFIRMAR = "ok:"
CANCELAR = "no:"


def codificar(accion: str, token: str) -> str:
    """Identificador del boton: la accion y el token, sin separador aparte."""
    return f"{accion}{token}"


def decodificar(dato: str) -> tuple[str, str] | None:
    """Devuelve `(accion, token)`, o None si el identificador no es nuestro.

    El largo del prefijo se deduce de la constante: estaba escrito a mano como
    un 3 en los dos canales, asi que cambiarlo cortaba el token por la mitad.
    """
    for accion in (CONFIRMAR, CANCELAR):
        if dato.startswith(accion):
            return accion, dato[len(accion):]
    return None


def partir(texto: str, limite: int) -> list[str]:
    """Trocea respetando saltos de linea, que es lo que evita cortar una cifra.

    WhatsApp rebanaba a pelo cada 4000 caracteres: mismo problema, distinto
    comportamiento, y el suyo podia partir «3.240 W» en dos mensajes.
    """
    if len(texto) <= limite:
        return [texto or "Hecho."]
    trozos: list[str] = []
    resto = texto
    while resto:
        if len(resto) <= limite:
            trozos.append(resto)
            break
        corte = resto.rfind("\n", 0, limite)
        if corte < limite // 2:
            corte = limite
        trozos.append(resto[:corte])
        resto = resto[corte:].lstrip("\n")
    return trozos
