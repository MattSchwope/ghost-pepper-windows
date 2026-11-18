"""In-process clip prep for dictation: skip near-silent clips, trim, highpass, level.

Plain numpy, so a short clip adds milliseconds and no ffmpeg process. The light
chain mirrors audio.CLEAN_FILTERS["light"] (highpass=f=80, then
dynaudnorm=f=250:g=15:p=0.9:m=20) so `gpw eval` results carry over.
"""

from __future__ import annotations

import io
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from . import audio
from .recorder import peak_dbfs

# Strong prep still runs ffmpeg (spectral denoise has no cheap numpy twin).
StrongFilter = Callable[[np.ndarray, int], tuple[np.ndarray, int]]

ASSESS_FRAME_MS = 20
TRIM_PAD_MS = 300  # keeps soft word onsets ("f", "h", "s") that sit under the loudness line


@dataclass(frozen=True)
class Assessment:
    peak_dbfs: float
    speech_ms: int
    skip_reason: str | None  # "quiet", "too_little_speech" or None


@dataclass(frozen=True)
class PreparedClip:
    audio: np.ndarray
    sample_rate: int
    raw_seconds: float
    peak_dbfs: float
    speech_ms: int
    skip_reason: str | None

    @property
    def seconds(self) -> float:
        return len(self.audio) / self.sample_rate


def _frames(x: np.ndarray, frame_len: int) -> np.ndarray:
    count = -(-len(x) // frame_len)
    padded = np.zeros(count * frame_len, dtype=np.float32)
    padded[: len(x)] = x
    return padded.reshape(count, frame_len)


def frame_peaks(x: np.ndarray, frame_len: int) -> np.ndarray:
    if not x.size:
        return np.zeros(0, dtype=np.float32)
    return np.abs(_frames(x, frame_len)).max(axis=1)


def _amplitude(dbfs: float) -> float:
    return float(10 ** (dbfs / 20))


def assess(x: np.ndarray, sample_rate: int, threshold_dbfs: float = -35.0, min_speech_ms: int = 200) -> Assessment:
    """Decide whether a clip holds speech. Skipping silence avoids phantom "Thank you." transcripts.

    A clip is skipped when nothing reaches the threshold, or when too little of it
    does (a key click or the start beep, not a word).
    """
    frame_len = max(1, sample_rate * ASSESS_FRAME_MS // 1000)
    loud = frame_peaks(x, frame_len) >= _amplitude(threshold_dbfs)
    speech_ms = int(loud.sum()) * ASSESS_FRAME_MS
    peak = peak_dbfs(x)
    if peak < threshold_dbfs:
        reason = "quiet"
    elif speech_ms < min_speech_ms:
        reason = "too_little_speech"
    else:
        reason = None
    return Assessment(peak_dbfs=round(peak, 1), speech_ms=speech_ms, skip_reason=reason)


def trim(x: np.ndarray, sample_rate: int, threshold_dbfs: float = -35.0, pad_ms: int = TRIM_PAD_MS) -> np.ndarray:
    """Cut leading and trailing silence, keeping `pad_ms` around the first and last loud frame."""
    frame_len = max(1, sample_rate * ASSESS_FRAME_MS // 1000)
    loud = np.flatnonzero(frame_peaks(x, frame_len) >= _amplitude(threshold_dbfs))
    if not loud.size:
        return x
    pad = sample_rate * pad_ms // 1000
    start = max(0, int(loud[0]) * frame_len - pad)
    end = min(len(x), (int(loud[-1]) + 1) * frame_len + pad)
    return x[start:end]


def _next_fast_len(n: int) -> int:
    """Smallest 2^a * 3^b * 5^c >= n, so the FFT stays fast on any clip length."""
    best = 1 << max(0, n - 1).bit_length()
    p5 = 1
    while p5 < best:
        p35 = p5
        while p35 < best:
            p = p35
            while p < n:
                p *= 2
            best = min(best, p)
            p35 *= 3
        p5 *= 5
    return best


def highpass(x: np.ndarray, sample_rate: int, cutoff_hz: float = 80.0, order: int = 2) -> np.ndarray:
    """Butterworth highpass applied in the frequency domain (same magnitude as ffmpeg's 2-pole highpass)."""
    if not x.size:
        return x.astype(np.float32)
    pad = sample_rate // 10  # zero padding keeps the end of the clip from wrapping into the start
    nfft = _next_fast_len(len(x) + 2 * pad)
    buf = np.zeros(nfft, dtype=np.float32)
    buf[pad : pad + len(x)] = x
    freqs = np.fft.rfftfreq(nfft, 1 / sample_rate)
    gain = np.zeros_like(freqs)
    gain[1:] = 1 / np.sqrt(1 + (cutoff_hz / freqs[1:]) ** (2 * order))
    out = np.fft.irfft(np.fft.rfft(buf) * gain, nfft)
    return out[pad : pad + len(x)].astype(np.float32)


def level(
    x: np.ndarray,
    sample_rate: int,
    frame_ms: int = 250,
    target_peak: float = 0.9,
    max_gain: float = 20.0,
    window_frames: int = 15,
) -> np.ndarray:
    """Even out loudness like ffmpeg dynaudnorm: per-frame gain, minimum filter, then Gaussian smoothing.

    Like dynaudnorm's default boundary mode, frames past either end count as gain 1.0,
    so the gain fades in over the first ~2 seconds and out over the last.
    """
    if not x.size:
        return x.astype(np.float32)
    frame_len = max(1, sample_rate * frame_ms // 1000)
    gains = np.minimum(target_peak / np.maximum(frame_peaks(x, frame_len), 1e-9), max_gain)
    half = window_frames // 2
    padded = np.pad(gains, half, constant_values=1.0)
    mins = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1).min(axis=1)
    sigma = ((window_frames / 2.0) - 1.0) / 3.0 + 1.0 / 3.0  # dynaudnorm's own choice
    offsets = np.arange(-half, half + 1)
    kernel = np.exp(-(offsets**2) / (2 * sigma**2))
    smooth = np.convolve(np.pad(mins, half, constant_values=1.0), kernel / kernel.sum(), mode="valid")
    centers = (np.arange(len(smooth)) + 0.5) * frame_len
    per_sample = np.interp(np.arange(len(x)), centers, smooth)
    return np.clip(x * per_sample, -1.0, 1.0).astype(np.float32)


def prepare_clip(
    x: np.ndarray,
    sample_rate: int,
    prep: str = "light",
    threshold_dbfs: float = -35.0,
    min_speech_ms: int = 200,
    strong: StrongFilter | None = None,
) -> PreparedClip:
    """Assess, then trim and clean a dictation clip. Skipped clips come back empty."""
    audio.filter_chain(prep)  # fail fast on a bad level
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    raw_seconds = len(x) / sample_rate
    check = assess(x, sample_rate, threshold_dbfs, min_speech_ms)
    if check.skip_reason:
        return PreparedClip(x[:0], sample_rate, raw_seconds, check.peak_dbfs, check.speech_ms, check.skip_reason)

    out, out_rate = x, sample_rate
    if prep != "off":
        out = trim(x, sample_rate, threshold_dbfs)
        if prep == "light":
            out = level(highpass(out, sample_rate, 80.0), sample_rate)
        else:
            if strong is None:
                raise ValueError("Strong prep needs an ffmpeg filter")
            out, out_rate = strong(out, sample_rate)
    return PreparedClip(out, out_rate, raw_seconds, check.peak_dbfs, check.speech_ms, None)


def ffmpeg_filter(x: np.ndarray, sample_rate: int, level_name: str, work_dir: Path) -> tuple[np.ndarray, int]:
    """Run one of the file-path ffmpeg chains on an in-memory clip. Output is 16 kHz mono."""
    work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work_dir) as tmp:
        src, dst = Path(tmp) / "in.wav", Path(tmp) / "out.wav"
        sf.write(str(src), x, sample_rate, subtype="PCM_16")
        audio.clean_to_wav(audio.find_ffmpeg(), src, dst, level_name)
        out, rate = sf.read(str(dst), dtype="float32")
    return out, int(rate)


def encode(x: np.ndarray, sample_rate: int, fmt: str = "flac") -> bytes:
    """Encode in memory. FLAC is about half the size of WAV, so the upload is faster."""
    buf = io.BytesIO()
    sf.write(buf, x, sample_rate, format=fmt.upper(), subtype="PCM_16")
    return buf.getvalue()
