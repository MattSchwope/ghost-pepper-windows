"""Synthetic two-track meetings for the meeting tests. No audio device, no network.

Me speaks in a 300 Hz tone and Them in a 500 Hz tone. ToneEngine "transcribes" a clip by its
loudest frequency, so a Me clip that really holds the call's echo comes back with Them's words.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

from gpw.meeting_capture import WavAppender

SR = 16_000
ME_HZ, THEM_HZ = 300, 500
ME_WORDS = "I can send the cavaliro contract on Friday."
THEM_WORDS = "Great, we will review the contract next week with legal."


def tone(seconds: float, freq: int, amp: float) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def track(length: float, parts: list[tuple[float, float, int, float]]) -> np.ndarray:
    """A track `length` seconds long with (start, end, freq, amp) tones laid in."""
    audio = np.zeros(int(length * SR), dtype=np.float32)
    for start, end, freq, amp in parts:
        a = int(start * SR)
        piece = tone(end - start, freq, amp)
        audio[a:a + len(piece)] += piece
    return audio


def write_track(path: Path, audio: np.ndarray) -> None:
    w = WavAppender(path, SR)
    w.write(audio)
    w.close()


# Me at 1-3 s and 9-10.5 s. Them at 4-7 s and 12-14 s. On laptop speakers the mic also hears
# Them's 12-14 s, quieter: that is the echo that must not become a Me line.
ME_PARTS = [(1.0, 3.0, ME_HZ, 0.3), (9.0, 10.5, ME_HZ, 0.3)]
THEM_PARTS = [(4.0, 7.0, THEM_HZ, 0.3), (12.0, 14.0, THEM_HZ, 0.3)]
ECHO_PARTS = [(12.0, 14.0, THEM_HZ, 0.05)]
LENGTH = 16.0


def make_meeting(root: Path, name: str = "2026-11-18 1405", *, echo: bool = True, mic: bool = True,
                 status: str = "stopped", pid: int = 0, app: str = "Teams") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    write_track(folder / "them.wav", track(LENGTH, THEM_PARTS))
    if mic:
        write_track(folder / "me.wav", track(LENGTH, ME_PARTS + (ECHO_PARTS if echo else [])))
    meta = {"name": name, "status": status, "pid": pid, "started": "2026-11-18T14:05:00", "app": app,
            "capture_mic": mic, "seconds": LENGTH}
    (folder / "capture.json").write_text(json.dumps(meta), encoding="utf-8")
    return folder


class APIConnectionError(Exception):
    """Named like the OpenAI SDK's, which the service turns into a short reason."""


class ToneEngine:
    model = "gpt-transcribe"

    def __init__(self, fail_on: int | None = None, error: Exception | None = None):
        self.calls: list[dict] = []
        self.fail_on = fail_on
        self.error = error or APIConnectionError("network down")
        self._lock = threading.Lock()

    def transcribe(self, path, prompt, keywords, languages):
        audio, rate = sf.read(str(path), dtype="float32")
        spectrum = np.abs(np.fft.rfft(audio))
        freq = np.fft.rfftfreq(len(audio), 1 / rate)[int(np.argmax(spectrum))]
        with self._lock:
            n = len(self.calls)
            self.calls.append({"seconds": len(audio) / rate, "prompt": prompt, "keywords": keywords, "languages": languages})
        if self.fail_on is not None and n == self.fail_on:
            raise self.error
        return ME_WORDS if abs(freq - ME_HZ) < abs(freq - THEM_HZ) else THEM_WORDS


class FakeSummaryModel:
    model = "gpt-6.1-sol"

    def __init__(self, answer: str | None = None, error: Exception | None = None):
        self.answer = answer or (
            "Title: Contract review: Acme / Kavaliro\n\n## Overview\nThe contract goes to legal.\n\n"
            "## Action items\n- [ ] Me: send the contract (due Friday)"
        )
        self.error = error
        self.inputs: list[tuple[str, str]] = []

    def complete(self, instructions, text):
        self.inputs.append((instructions, text))
        if self.error is not None:
            raise self.error
        return self.answer
