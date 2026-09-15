"""Transcripcion de notas de voz, en local.

Se usa faster-whisper en la propia maquina en lugar de un servicio en la nube
por dos motivos: las notas de voz de casa no salen de casa, y funciona aunque
no haya internet (el agente sigue pudiendo apagar luces o mirar la bateria).

El modelo se carga una sola vez y de forma perezosa: cargarlo al arrancar
retrasaria el arranque del backend varios segundos sin necesidad.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from .settings import Settings

log = logging.getLogger(__name__)

_modelo: Any = None
_lock = asyncio.Lock()


class TranscripcionError(RuntimeError):
    pass


async def _cargar(settings: Settings) -> Any:
    global _modelo
    async with _lock:
        if _modelo is not None:
            return _modelo
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise TranscripcionError(
                "La transcripcion de voz no esta instalada. Es un extra opcional "
                "porque pesa bastante: instalalo con `pip install -e \".[voz]\"` "
                "(o reconstruye la imagen de Docker con CON_VOZ=true). Mientras "
                "tanto, escribeme por texto y funciona igual."
            ) from e
        log.info("Cargando modelo de voz %s...", settings.whisper_modelo)
        _modelo = await asyncio.to_thread(
            WhisperModel,
            settings.whisper_modelo,
            device=settings.whisper_dispositivo,
            compute_type=settings.whisper_compute_type,
        )
        return _modelo


async def transcribir(ruta: Path, settings: Settings) -> str:
    """Transcribe un fichero de audio a texto en espanol."""
    modelo = await _cargar(settings)

    def _hacer() -> str:
        segmentos, _info = modelo.transcribe(
            str(ruta),
            language="es",
            beam_size=5,
            # Filtrar silencios evita que un mensaje con ruido de fondo
            # produzca transcripciones inventadas.
            vad_filter=True,
        )
        return " ".join(s.text.strip() for s in segmentos).strip()

    texto = await asyncio.to_thread(_hacer)
    if not texto:
        raise TranscripcionError("No he entendido nada en la nota de voz.")
    return texto
