from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
import logging

import numpy as np
import sounddevice as sd
import soundfile as sf

logger = logging.getLogger(__name__)

# Normal speech peaks well above this. Below it, the mic is muted, too far away or the wrong one.
QUIET_PEAK_DBFS = -35.0


def pick_input_device(
    spec: int | str | None,
    devices: Sequence[Mapping[str, Any]],
    default_hostapi: int,
) -> int | None:
    """Turn the `recording.input_device` setting into a device index. None means the Windows default mic."""
    if spec is None or spec == "":
        return None
    inputs = [i for i, d in enumerate(devices) if d["max_input_channels"] > 0]
    if isinstance(spec, int):
        if spec not in inputs:
            raise ValueError(f"recording.input_device {spec} is not a microphone")
        return spec
    matches = [i for i in inputs if spec.lower() in devices[i]["name"].lower()]
    if not matches:
        names = sorted({devices[i]["name"] for i in inputs})
        raise ValueError(f"No microphone matches recording.input_device '{spec}'. Microphones: {'; '.join(names)}")
    # The same mic appears once per Windows audio API; use the copy the default mic uses.
    preferred = [i for i in matches if devices[i]["hostapi"] == default_hostapi]
    return (preferred or matches)[0]


def peak_dbfs(audio: np.ndarray) -> float:
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    return 20 * np.log10(peak) if peak > 0 else -120.0


def record_wav(
    audio_dir: Path,
    duration_seconds: int,
    sample_rate: int = 16000,
    channels: int = 1,
    device: int | str | None = None,
    on_start: Callable[[], None] | None = None,
) -> Path:
    audio_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_path = audio_dir / f"dictation_{stamp}.wav"

    index = pick_input_device(device, sd.query_devices(), sd.default.hostapi)
    mic = sd.query_devices(index, "input") if index is not None else sd.query_devices(kind="input")
    logger.info(
        "Recording audio: mic=%s duration=%ss sample_rate=%s channels=%s",
        mic["name"], duration_seconds, sample_rate, channels,
    )
    if on_start:
        on_start()
    audio = sd.rec(
        frames=int(duration_seconds * sample_rate),
        samplerate=sample_rate,
        channels=channels,
        dtype="float32",
        device=index,
    )
    sd.wait()

    audio = np.asarray(audio)
    sf.write(str(output_path), audio, sample_rate)
    level = peak_dbfs(audio)
    logger.info("Saved audio: %s peak=%.1fdBFS", output_path, level)
    if level < QUIET_PEAK_DBFS:
        logger.warning(
            "The mic barely heard anything (loudest sound %.1f dB). Check it is not muted, speak closer, "
            "or pick another mic with recording.input_device.",
            level,
        )
    return output_path
