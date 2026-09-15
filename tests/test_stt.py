"""La transcripcion de voz es opcional: su ausencia no puede romper el sistema."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from casa_ai.settings import Settings
from casa_ai.stt import TranscripcionError, transcribir


async def test_sin_la_dependencia_el_mensaje_explica_que_hacer(
    settings: Settings, tmp_path: Path, monkeypatch
) -> None:
    """Un equipo pequeno puede instalarse sin voz; el aviso debe ser util."""
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    monkeypatch.setattr("casa_ai.stt._modelo", None)

    audio = tmp_path / "nota.ogg"
    audio.write_bytes(b"no es audio de verdad")

    with pytest.raises(TranscripcionError) as excinfo:
        await transcribir(audio, settings)

    mensaje = str(excinfo.value)
    assert "[voz]" in mensaje          # dice como instalarlo
    assert "texto" in mensaje          # y que hacer mientras tanto


def test_el_nucleo_no_importa_faster_whisper() -> None:
    """Importar la aplicacion no debe arrastrar la dependencia pesada."""
    import casa_ai.app  # noqa: F401
    import casa_ai.main  # noqa: F401

    assert "faster_whisper" not in sys.modules
