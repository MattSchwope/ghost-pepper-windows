"""Audio cleanup and silence-aware chunking with ffmpeg."""

from __future__ import annotations

import re
import shutil
import subprocess
import wave
from pathlib import Path

# Filter chains for phone recordings of presentations.
#   highpass   cuts rumble, HVAC hum, mic handling thumps
#   dynaudnorm evens out volume as the speaker moves or turns away
#   afftdn     spectral noise reduction (strong only; can smear words if pushed)
CLEAN_FILTERS: dict[str, list[str]] = {
    "off": [],
    "light": [
        "highpass=f=80",
        "dynaudnorm=f=250:g=15:p=0.9:m=20",
    ],
    "strong": [
        "highpass=f=100",
        "afftdn=nr=12:nf=-35:tn=1",
        "dynaudnorm=f=150:g=11:p=0.9:m=30",
    ],
}

SAMPLE_RATE = 16_000
CHUNK_BITRATE = "64k"  # mono speech; ~0.5 MB per minute, far under the 25 MB API cap


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise SystemExit("ffmpeg not found. Run: pip install imageio-ffmpeg")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr[-1500:]}")
    return result


def clean_to_wav(ffmpeg: str, src: Path, dst: Path, level: str) -> None:
    """Decode any audio/video file to 16 kHz mono WAV, applying the cleanup chain."""
    cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE)]
    filters = CLEAN_FILTERS[level]
    if filters:
        cmd += ["-af", ",".join(filters)]
    cmd += ["-c:a", "pcm_s16le", str(dst)]
    _run(cmd)


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def detect_silences(ffmpeg: str, src: Path, min_len: float = 0.3, below_mean_db: float = 10.0) -> list[tuple[float, float]]:
    """Find pauses in the ORIGINAL recording.

    Runs on the source, not the cleaned WAV: volume leveling lifts room noise in
    pauses, which hides them. The threshold is relative to the recording's mean
    loudness, so it adapts to quiet or noisy phone audio.
    """
    probe = _run([
        ffmpeg, "-hide_banner", "-nostats", "-i", str(src), "-vn", "-ac", "1",
        "-af", "highpass=f=100,volumedetect", "-f", "null", "-",
    ])
    m = re.search(r"mean_volume: (-?[\d.]+) dB", probe.stderr)
    threshold = max(-60.0, (float(m.group(1)) if m else -30.0) - below_mean_db)
    result = _run([
        ffmpeg, "-hide_banner", "-nostats", "-i", str(src), "-vn", "-ac", "1",
        "-af", f"highpass=f=100,silencedetect=noise={threshold:.1f}dB:d={min_len}", "-f", "null", "-",
    ])
    starts = [max(0.0, float(x)) for x in re.findall(r"silence_start: (-?[\d.]+)", result.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", result.stderr)]
    return list(zip(starts, ends))


def plan_cuts(duration: float, silences: list[tuple[float, float]], chunk_sec: float, window: float = 60.0) -> list[float]:
    """Return cut points [0, ..., duration], placing each cut in a pause near the target."""
    cuts = [0.0]
    while duration - cuts[-1] > chunk_sec * 1.15:  # avoid a tiny final chunk
        target = cuts[-1] + chunk_sec
        candidates = [
            (end - start, (start + end) / 2)
            for start, end in silences
            if abs((start + end) / 2 - target) <= window and (start + end) / 2 > cuts[-1] + 30
        ]
        if candidates:
            # Favor longer pauses; mild penalty for drifting from the target.
            _, cut = max(candidates, key=lambda c: c[0] - 0.5 * abs(c[1] - target) / window)
        else:
            cut = target
        cuts.append(round(cut, 3))
    cuts.append(duration)
    return cuts


def export_chunk(ffmpeg: str, wav: Path, start: float, end: float, dst: Path) -> None:
    _run([
        ffmpeg, "-hide_banner", "-y", "-i", str(wav),
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-c:a", "libmp3lame", "-b:a", CHUNK_BITRATE, str(dst),
    ])
