"""Comando de verificacion.

Lo que se prueba aqui es la parte que TOCA ficheros: el escritor de `.env`.
Si se equivoca, corrompe la configuracion del usuario, y eso es peor que no
existir. El resto del comando es salida por pantalla y su valor esta en
ejecutarlo, no en fijar su texto con tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from casa_ai.verificar import _escribir_env, _preguntar


@pytest.fixture
def en_directorio(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_cambia_solo_la_clave_pedida(en_directorio: Path) -> None:
    env = en_directorio / ".env"
    env.write_text(
        "ANTHROPIC_API_KEY=sk-secreta\n"
        "SUNGROW_HOST=192.168.1.30\n"
        "SUNGROW_INVERTIR_SIGNO_RED=false\n"
        "TELEGRAM_TOKEN=123:abc\n",
        "utf-8",
    )

    _escribir_env("SUNGROW_INVERTIR_SIGNO_RED", "true")

    lineas = env.read_text("utf-8").splitlines()
    assert "SUNGROW_INVERTIR_SIGNO_RED=true" in lineas
    # Y no ha tocado nada mas: ni la clave de la API ni el resto.
    assert "ANTHROPIC_API_KEY=sk-secreta" in lineas
    assert "SUNGROW_HOST=192.168.1.30" in lineas
    assert "TELEGRAM_TOKEN=123:abc" in lineas
    assert len(lineas) == 4


def test_anade_la_clave_si_no_estaba(en_directorio: Path) -> None:
    env = en_directorio / ".env"
    env.write_text("SUNGROW_HOST=192.168.1.30\n", "utf-8")

    _escribir_env("SUNGROW_INVERTIR_SIGNO_RED", "true")

    lineas = env.read_text("utf-8").splitlines()
    assert lineas == ["SUNGROW_HOST=192.168.1.30", "SUNGROW_INVERTIR_SIGNO_RED=true"]


def test_respeta_comentarios_y_lineas_en_blanco(en_directorio: Path) -> None:
    """El .env de ejemplo esta lleno de comentarios que explican cada ajuste:
    perderlos dejaria al usuario sin la documentacion que tiene delante."""
    env = en_directorio / ".env"
    original = (
        "# --- Inversor Sungrow ---\n"
        "# IP del dongle WiNet-S\n"
        "SUNGROW_HOST=192.168.1.30\n"
        "\n"
        "# Ponlo en true si el diagnostico lo pide\n"
        "SUNGROW_INVERTIR_SIGNO_RED=false\n"
    )
    env.write_text(original, "utf-8")

    _escribir_env("SUNGROW_INVERTIR_SIGNO_RED", "true")

    texto = env.read_text("utf-8")
    assert "# --- Inversor Sungrow ---" in texto
    assert "# IP del dongle WiNet-S" in texto
    assert "# Ponlo en true si el diagnostico lo pide" in texto
    assert "\n\n" in texto  # la linea en blanco sigue ahi


def test_una_clave_duplicada_se_cambia_entera(en_directorio: Path) -> None:
    """Un .env con la clave duplicada es raro, pero si pasa, python-dotenv se
    queda con la ULTIMA: cambiar solo la primera dejaria ganando el valor
    viejo y el usuario creeria que lo ha cambiado."""
    env = en_directorio / ".env"
    env.write_text("X=1\nSUNGROW_HOST=a\nSUNGROW_HOST=b\n", "utf-8")

    _escribir_env("SUNGROW_HOST", "c")

    lineas = env.read_text("utf-8").splitlines()
    assert lineas == ["X=1", "SUNGROW_HOST=c", "SUNGROW_HOST=c"]


def test_una_clave_con_espacios_delante_se_reconoce(en_directorio: Path) -> None:
    env = en_directorio / ".env"
    env.write_text("  SUNGROW_HOST=viejo\n", "utf-8")

    _escribir_env("SUNGROW_HOST", "nuevo")

    assert env.read_text("utf-8").splitlines() == ["SUNGROW_HOST=nuevo"]


def test_no_confunde_una_clave_con_otra_que_empieza_igual(
    en_directorio: Path,
) -> None:
    env = en_directorio / ".env"
    env.write_text("SUNGROW_HOST=a\nSUNGROW_HOST_2=b\n", "utf-8")

    _escribir_env("SUNGROW_HOST", "c")

    lineas = env.read_text("utf-8").splitlines()
    assert lineas == ["SUNGROW_HOST=c", "SUNGROW_HOST_2=b"]


def test_sin_fichero_env_no_crea_uno_a_medias(
    en_directorio: Path, capsys
) -> None:
    """Crear un .env con una sola clave seria peor: el usuario creeria que esta
    configurado."""
    _escribir_env("SUNGROW_HOST", "1.2.3.4")

    assert not (en_directorio / ".env").exists()
    assert "No encuentro .env" in capsys.readouterr().out


def test_el_fichero_acaba_en_salto_de_linea(en_directorio: Path) -> None:
    env = en_directorio / ".env"
    env.write_text("X=1", "utf-8")  # sin salto final

    _escribir_env("X", "2")

    assert env.read_text("utf-8").endswith("\n")


async def test_preguntar_no_bloquea_el_bucle(monkeypatch) -> None:
    """`input()` en una corrutina bloquearia el bucle de eventos, asi que va a
    un hilo."""
    monkeypatch.setattr("builtins.input", lambda mensaje="": "si")
    assert await _preguntar("confirmas? ") == "si"
