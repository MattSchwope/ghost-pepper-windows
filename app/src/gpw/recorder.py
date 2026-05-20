from __future__ import annotations

from datetime import datetime
from pathlib import Path
import logging

import numpy as np
import sounddevice as sd
import soundfile as sf

logger = logging.getLogger(__name__)


class RecordingError(RuntimeError):
    """Raised when microphone recording fails before a WAV is saved."""


def record_wav(
    audio_dir: Path,
    duration_seconds: int,
    sample_rate: int = 16000,
    channels: int = 1,
) -> Path:
    if duration_seconds <= 0:
        raise ValueError("Recording duration must be greater than zero seconds.")
    if sample_rate <= 0:
        raise ValueError("Recording sample rate must be greater than zero.")
    if channels <= 0:
        raise ValueError("Recording channel count must be greater than zero.")

    audio_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_path = audio_dir / f"dictation_{stamp}.wav"

    logger.info("Recording audio: duration=%ss sample_rate=%s channels=%s", duration_seconds, sample_rate, channels)
    try:
        audio = sd.rec(
            frames=int(duration_seconds * sample_rate),
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
        )
        sd.wait()
    except Exception as exc:
        raise RecordingError(
            "Unable to record from the default microphone. Check the Windows input device and microphone permissions."
        ) from exc

    audio = np.asarray(audio)
    try:
        sf.write(str(output_path), audio, sample_rate)
    except Exception as exc:
        raise RecordingError(f"Unable to save recorded audio to {output_path}.") from exc

    logger.info("Saved audio: %s", output_path)
    return output_path
