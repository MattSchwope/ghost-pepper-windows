from __future__ import annotations

from datetime import datetime
from pathlib import Path
import logging

import numpy as np
import sounddevice as sd
import soundfile as sf

logger = logging.getLogger(__name__)


def record_wav(
    audio_dir: Path,
    duration_seconds: int,
    sample_rate: int = 16000,
    channels: int = 1,
) -> Path:
    audio_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_path = audio_dir / f"dictation_{stamp}.wav"

    logger.info("Recording audio: duration=%ss sample_rate=%s channels=%s", duration_seconds, sample_rate, channels)
    audio = sd.rec(
        frames=int(duration_seconds * sample_rate),
        samplerate=sample_rate,
        channels=channels,
        dtype="float32",
    )
    sd.wait()

    audio = np.asarray(audio)
    sf.write(str(output_path), audio, sample_rate)
    logger.info("Saved audio: %s", output_path)
    return output_path
