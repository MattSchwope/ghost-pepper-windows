"""Cue sounds and tray icons for the hotkey script, generated at service start.

Generated rather than shipped, so the repo carries no binary files and nothing
depends on which Windows sounds are installed.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import soundfile as sf

SOUND_RATE = 22050

# (frequencies played one after another in Hz, milliseconds each). Kept short and soft:
# the start cue plays while the mic is already listening.
SOUNDS: dict[str, tuple[list[float], int]] = {
    "start": ([660, 880], 40),
    "stop": ([880, 660], 40),
    "handsfree": ([880, 0, 880], 45),
    "cancel": ([440, 330], 50),
    "empty": ([330], 90),
    "error": ([220, 0, 220], 80),
}

ICONS: dict[str, tuple[int, int, int]] = {
    "idle": (67, 160, 71),  # green: ready
    "recording": (229, 57, 53),  # red: listening
    "handsfree": (216, 27, 96),  # magenta: hands-free listening
    "busy": (255, 179, 0),  # amber: transcribing
}


def tone(freqs: list[float], ms_each: int, sample_rate: int = SOUND_RATE, volume: float = 0.2) -> np.ndarray:
    """A sequence of sine blips with 5 ms fades (no clicks). A frequency of 0 is a gap."""
    n = sample_rate * ms_each // 1000
    t = np.arange(n) / sample_rate
    fade = min(n // 2, sample_rate * 5 // 1000)
    envelope = np.ones(n)
    envelope[:fade] = np.linspace(0, 1, fade)
    envelope[n - fade :] = np.linspace(1, 0, fade)
    parts = [volume * envelope * np.sin(2 * np.pi * f * t) if f else np.zeros(n) for f in freqs]
    return np.concatenate(parts).astype(np.float32)


def ico_bytes(rgb: tuple[int, int, int], size: int = 32) -> bytes:
    """A filled, anti-aliased circle as a 32-bit .ico file."""
    yy, xx = np.mgrid[0:size, 0:size] + 0.5
    center, radius = size / 2, size / 2 - 1
    alpha = np.clip(radius - np.hypot(xx - center, yy - center) + 0.5, 0, 1)
    bgra = np.zeros((size, size, 4), dtype=np.uint8)
    bgra[..., 0], bgra[..., 1], bgra[..., 2] = rgb[2], rgb[1], rgb[0]
    bgra[..., 3] = np.round(alpha * 255).astype(np.uint8)
    pixels = bgra[::-1].tobytes()  # BMP rows run bottom-up
    and_mask = bytes(((size + 31) // 32) * 4 * size)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, len(pixels) + len(and_mask), 0, 0, 0, 0)
    image = header + pixels + and_mask
    return struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), 22) + image


def write_assets(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name, (freqs, ms) in SOUNDS.items():
        sf.write(str(folder / f"{name}.wav"), tone(freqs, ms), SOUND_RATE, subtype="PCM_16")
    for name, rgb in ICONS.items():
        (folder / f"{name}.ico").write_bytes(ico_bytes(rgb))
    return folder
