"""Meeting capture: the call audio and your mic as two tracks, written to disk while they record.

Them is a WASAPI loopback of the default output device, read through `soundcard`. It asks
Windows for the current default speaker each time, so a headset plugged in mid-call is
followed by reopening the loopback, and it returns silence while nothing plays.
Me is the dictation mic, read through `sounddevice` like dictation. soundcard's own mic path
refuses mics whose shared format isn't WAVE_FORMAT_EXTENSIBLE, such as the USB Condenser mic.

Both tracks are kept on one wall clock: each block is placed where the time since start says
it belongs, so a device gap or a slow open becomes silence instead of shifting the track.
Nothing here logs audio or text.
"""

from __future__ import annotations

import logging
import os
import struct
import threading
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
BLOCK_SECONDS = 0.1
DEVICE_CHECK_SECONDS = 2.0  # how often the Them track asks Windows for the default output device
REOPEN_WAIT_SECONDS = 1.0  # after a device error, wait this long before opening again
ALIGN_TOLERANCE_SECONDS = 0.5  # drift between a track and the wall clock allowed before it is corrected
FLUSH_SECONDS = 1.0  # a crash loses at most this much of the track
FIRST_OPEN_WAIT_SECONDS = 5.0
CALL_AUDIO_DBFS = -50.0  # louder than this on the Them track counts as call audio
NO_CALL_AUDIO_SECONDS = 120.0

TRACKS = ("them", "me")


class CaptureError(RuntimeError):
    pass


class Recorder(Protocol):
    def record(self, frames: int) -> np.ndarray: ...  # (frames,) or (frames, channels) float32

    def close(self) -> None: ...


@dataclass(frozen=True)
class Opened:
    device_id: str
    name: str
    recorder: Recorder


class Backend(Protocol):
    def output_id(self) -> str | None: ...  # the default output device right now

    def open_loopback(self, sample_rate: int) -> Opened: ...

    def open_mic(self, sample_rate: int) -> Opened: ...


# -- files --

def wav_header(frames: int, sample_rate: int = SAMPLE_RATE) -> bytes:
    """A 44-byte header for 16-bit mono PCM."""
    data = frames * 2
    return (
        b"RIFF" + struct.pack("<I", 36 + data) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b"data" + struct.pack("<I", data)
    )


def to_pcm16(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()


class WavAppender:
    """A WAV file written as it records. The header is patched on every flush, and TrackFile
    trusts the file size over the header, so a killed service keeps the audio up to the kill."""

    def __init__(self, path: Path, sample_rate: int = SAMPLE_RATE, clock: Callable[[], float] = time.monotonic) -> None:
        self.path = path
        self.sample_rate = sample_rate
        self.clock = clock
        self.frames = 0
        self._f = open(path, "wb")
        self._f.write(wav_header(0, sample_rate))
        self._last_flush = clock()

    def write(self, samples: np.ndarray) -> None:
        if len(samples) == 0:
            return
        self._f.write(to_pcm16(samples))
        self.frames += len(samples)
        if self.clock() - self._last_flush >= FLUSH_SECONDS:
            self.flush()

    def write_silence(self, frames: int) -> None:
        while frames > 0:
            n = min(frames, self.sample_rate * 10)
            self.write(np.zeros(n, dtype=np.float32))
            frames -= n

    def flush(self) -> None:
        self._f.seek(0)
        self._f.write(wav_header(self.frames, self.sample_rate))
        self._f.seek(0, os.SEEK_END)
        self._f.flush()
        self._last_flush = self.clock()

    def close(self) -> None:
        if self._f.closed:
            return
        self.flush()
        self._f.close()

    @property
    def seconds(self) -> float:
        return self.frames / self.sample_rate


class TrackFile:
    """Read a track written by WavAppender, even one whose header was never finished."""

    def __init__(self, path: Path) -> None:
        self.path = path
        with open(path, "rb") as f:
            head = f.read(12)
            if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                raise ValueError(f"{path.name} is not a WAV file")
            self.sample_rate = SAMPLE_RATE
            channels, bits = 1, 16
            while True:
                chunk = f.read(8)
                if len(chunk) < 8:
                    raise ValueError(f"{path.name} has no audio data")
                cid, size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
                if cid == b"fmt ":
                    fmt = f.read(size)
                    _, channels, self.sample_rate, _, _, bits = struct.unpack("<HHIIHH", fmt[:16])
                elif cid == b"data":
                    self.offset = f.tell()
                    break
                else:
                    f.seek(size + (size & 1), os.SEEK_CUR)
        if channels != 1 or bits != 16:
            raise ValueError(f"{path.name} is not 16-bit mono")
        self.frames = max(0, (path.stat().st_size - self.offset) // 2)

    @property
    def seconds(self) -> float:
        return self.frames / self.sample_rate

    def read(self, start: int, count: int) -> np.ndarray:
        start = max(0, min(start, self.frames))
        count = max(0, min(count, self.frames - start))
        if count == 0:
            return np.zeros(0, dtype=np.float32)
        with open(self.path, "rb") as f:
            f.seek(self.offset + start * 2)
            raw = np.frombuffer(f.read(count * 2), dtype="<i2")
        return raw.astype(np.float32) / 32768.0

    def read_seconds(self, start: float, end: float) -> np.ndarray:
        a = int(round(start * self.sample_rate))
        return self.read(a, int(round(end * self.sample_rate)) - a)

    def blocks(self, frames: int):
        for start in range(0, self.frames, frames):
            yield self.read(start, frames)


# -- one wall clock for both tracks --

def align(written: int, elapsed: int, block: int, tolerance: int, first: bool = False) -> tuple[int, int]:
    """Where a block that just arrived belongs. Returns (frames of silence to add first, frames to drop
    from its start). `elapsed` is the frames of wall-clock time since capture start, measured as the
    block arrived, so the block should start at elapsed - block. The first block always lines up,
    which covers the time the device took to open."""
    lag = elapsed - block - written
    if first:
        return max(0, lag), 0
    if lag > tolerance:
        return lag, 0
    if lag < -tolerance:
        return 0, min(block, -lag)
    return 0, 0


def peak_dbfs(samples: np.ndarray) -> float:
    peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
    return 20 * np.log10(peak) if peak > 0 else -120.0


def to_mono(block: np.ndarray) -> np.ndarray:
    block = np.asarray(block, dtype=np.float32)
    return block.mean(axis=1, dtype=np.float32) if block.ndim == 2 else block


class TrackRecorder:
    """One track: a thread that opens its device, reads blocks and writes them on the shared clock.
    A device error or a new default device closes it and opens again; the gap is written as silence."""

    def __init__(
        self,
        name: str,
        open_device: Callable[[], Opened],
        writer: WavAppender,
        t0: float,
        *,
        current_device: Callable[[], str | None] | None = None,
        hold: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.monotonic,
        sample_rate: int = SAMPLE_RATE,
        thread_init: Callable[[], None] | None = None,
    ) -> None:
        self.name = name
        self.open_device = open_device
        self.writer = writer
        self.t0 = t0
        self.current_device = current_device
        self.hold = hold
        self.clock = clock
        self.sample_rate = sample_rate
        self.block = int(sample_rate * BLOCK_SECONDS)
        self.tolerance = int(sample_rate * ALIGN_TOLERANCE_SECONDS)
        self.thread_init = thread_init
        self.device_name = ""
        self.loudest_dbfs = -120.0
        self.reopens = 0
        self.first_open = threading.Event()
        self.open_error: Exception | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._first_block = True

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"gpw-meeting-{self.name}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                logger.warning("Meeting %s track did not stop within %.0f s", self.name, timeout)

    def _elapsed_frames(self) -> int:
        return round((self.clock() - self.t0) * self.sample_rate)

    def _open(self) -> Opened | None:
        try:
            opened = self.open_device()
        except Exception as exc:
            if not self.first_open.is_set():
                self.open_error = exc
                self.first_open.set()
            logger.warning("Meeting %s track: could not open the device (%s: %s)", self.name, type(exc).__name__, exc)
            self._stop.wait(REOPEN_WAIT_SECONDS)
            return None
        self.device_name = opened.name
        self.open_error = None
        self.first_open.set()
        logger.info("Meeting %s track open: %s", self.name, opened.name)
        return opened

    def _run(self) -> None:
        if self.thread_init is not None:
            self.thread_init()
        opened: Opened | None = None
        last_check = self.clock()
        try:
            while not self._stop.is_set():
                if opened is None:
                    opened = self._open()
                    if opened is None:
                        continue
                    last_check = self.clock()
                try:
                    block = to_mono(opened.recorder.record(self.block))
                except Exception as exc:
                    logger.warning("Meeting %s track: device stopped (%s); opening again", self.name, type(exc).__name__)
                    self._close(opened)
                    opened = None
                    self.reopens += 1
                    continue
                self._place(block)
                if self.current_device is not None and self.clock() - last_check >= DEVICE_CHECK_SECONDS:
                    last_check = self.clock()
                    try:
                        now_id = self.current_device()
                    except Exception:
                        now_id = opened.device_id
                    if now_id is not None and now_id != opened.device_id:
                        logger.info("Meeting %s track: the default device changed; reopening", self.name)
                        self._close(opened)
                        opened = None
                        self.reopens += 1
        finally:
            if opened is not None:
                self._close(opened)
            self.writer.close()

    def _place(self, block: np.ndarray) -> None:
        if self.hold():
            block = np.zeros_like(block)
        else:
            self.loudest_dbfs = max(self.loudest_dbfs, peak_dbfs(block))
        pad, skip = align(self.writer.frames, self._elapsed_frames(), len(block), self.tolerance, self._first_block)
        if pad and not self._first_block:
            logger.info("Meeting %s track: %.2f s gap filled with silence", self.name, pad / self.sample_rate)
        elif skip:
            logger.info("Meeting %s track: %.2f s ahead of the clock; trimmed", self.name, skip / self.sample_rate)
        self._first_block = False
        self.writer.write_silence(pad)
        self.writer.write(block[skip:])

    def _close(self, opened: Opened) -> None:
        try:
            opened.recorder.close()
        except Exception as exc:
            logger.info("Meeting %s track: closing the device failed (%s)", self.name, type(exc).__name__)


class MeetingCapture:
    """The two tracks of one meeting, in `folder` as them.wav and me.wav."""

    def __init__(
        self,
        folder: Path,
        backend: Backend,
        capture_mic: bool = True,
        clock: Callable[[], float] = time.monotonic,
        sample_rate: int = SAMPLE_RATE,
        first_open_wait: float = FIRST_OPEN_WAIT_SECONDS,
        thread_init: Callable[[], None] | None = None,
    ) -> None:
        self.folder = folder
        self.backend = backend
        self.capture_mic = capture_mic
        self.clock = clock
        self.sample_rate = sample_rate
        self.first_open_wait = first_open_wait
        self.thread_init = thread_init
        self.muted = False  # "Mute my mic track" in the tray menu
        self.dictating = False  # a dictation is recording: keep it out of the Me track
        self.t0 = 0.0
        self.tracks: dict[str, TrackRecorder] = {}

    def start(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self.t0 = self.clock()
        sr = self.sample_rate
        them = TrackRecorder(
            "them", lambda: self.backend.open_loopback(sr), WavAppender(self.folder / "them.wav", sr, self.clock),
            self.t0, current_device=self.backend.output_id, clock=self.clock, sample_rate=sr,
            thread_init=self.thread_init,
        )
        self.tracks = {"them": them}
        if self.capture_mic:
            self.tracks["me"] = TrackRecorder(
                "me", lambda: self.backend.open_mic(sr), WavAppender(self.folder / "me.wav", sr, self.clock),
                self.t0, hold=lambda: self.muted or self.dictating, clock=self.clock, sample_rate=sr,
                thread_init=self.thread_init,
            )
        for track in self.tracks.values():
            track.start()
        them.first_open.wait(self.first_open_wait)
        if them.open_error is not None:
            self.stop()
            raise CaptureError(f"Could not record the call audio: {them.open_error}")
        if "me" in self.tracks:
            me = self.tracks["me"]
            me.first_open.wait(max(0.0, self.first_open_wait - (self.clock() - self.t0)))
            if not me.first_open.is_set() or me.open_error is not None:
                logger.warning("Meeting: the mic track is not open yet; it keeps trying and its gap is silence")

    def stop(self) -> float:
        for track in self.tracks.values():
            track.stop()
        return self.seconds

    @property
    def seconds(self) -> float:
        return max((t.writer.seconds for t in self.tracks.values()), default=0.0)

    def elapsed(self) -> float:
        return self.clock() - self.t0

    def heard_call_audio(self) -> bool:
        them = self.tracks.get("them")
        return them is not None and them.loudest_dbfs > CALL_AUDIO_DBFS

    def no_call_audio(self) -> bool:
        """True once two minutes have passed with nothing heard from the call."""
        return self.elapsed() >= NO_CALL_AUDIO_SECONDS and not self.heard_call_audio()

    def device_names(self) -> dict[str, str]:
        return {name: t.device_name for name, t in self.tracks.items()}


# -- the real devices --

_SOUNDCARD: Any = None
_SOUNDCARD_LOCK = threading.Lock()


def com_thread_init() -> None:
    """Join the COM multithreaded apartment, which WASAPI calls from soundcard need."""
    try:
        import ctypes

        ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED; S_FALSE if already joined
    except (ImportError, AttributeError, OSError):
        pass


def load_soundcard() -> Any:
    """Import soundcard on a thread of its own. Its import calls CoInitializeEx and treats COM being
    set up already on that thread (as comtypes does for auto-learn) as an error."""
    global _SOUNDCARD
    with _SOUNDCARD_LOCK:
        if _SOUNDCARD is None:
            result: dict[str, Any] = {}

            def worker() -> None:
                try:
                    import soundcard

                    result["module"] = soundcard
                except Exception as exc:  # reported below
                    result["error"] = exc

            thread = threading.Thread(target=worker, name="gpw-soundcard-import", daemon=True)
            thread.start()
            thread.join(20)
            if "module" not in result:
                error = result.get("error")
                raise CaptureError(f"Call audio capture is not available ({type(error).__name__ if error else 'timeout'}). "
                                   "Run: pip install soundcard")
            module = result["module"]
            warning = getattr(getattr(module, "mediafoundation", None), "SoundcardRuntimeWarning", None)
            if warning is not None:
                warnings.filterwarnings("ignore", category=warning)  # "data discontinuity" after a stall
            _SOUNDCARD = module
        return _SOUNDCARD


class _SoundcardRecorder:
    def __init__(self, recorder: Any) -> None:
        self._recorder = recorder
        recorder.__enter__()

    def record(self, frames: int) -> np.ndarray:
        return self._recorder.record(frames)

    def close(self) -> None:
        self._recorder.__exit__(None, None, None)


class _SounddeviceRecorder:
    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def record(self, frames: int) -> np.ndarray:
        data, _overflowed = self._stream.read(frames)
        return data

    def close(self) -> None:
        try:
            self._stream.stop()
        finally:
            self._stream.close()


class DeviceBackend:
    """soundcard for the loopback, sounddevice for the mic. `pa_lock` is the dictation service's
    PortAudio lock, held while the Me track picks its mic so that never overlaps a device rescan.
    The open itself runs without it: a mic that hangs on open (a Bluetooth headset did once) must
    not stall dictation. The service skips its rescans while a meeting is being captured."""

    def __init__(self, input_device: int | str | None = None, pa_lock: threading.Lock | None = None) -> None:
        self.input_device = input_device
        self.pa_lock = pa_lock or threading.Lock()

    def output_id(self) -> str | None:
        speaker = load_soundcard().default_speaker()
        return str(speaker.id) if speaker is not None else None

    def open_loopback(self, sample_rate: int) -> Opened:
        sc = load_soundcard()
        speaker = sc.default_speaker()
        if speaker is None:
            raise CaptureError("Windows has no default speaker or headset")
        loopback = sc.get_microphone(speaker.id, include_loopback=True)
        recorder = _SoundcardRecorder(loopback.recorder(samplerate=sample_rate))
        return Opened(str(speaker.id), str(speaker.name), recorder)

    def open_mic(self, sample_rate: int) -> Opened:
        import sounddevice as sd

        from .recorder import pick_input_device

        with self.pa_lock:
            index = pick_input_device(self.input_device, sd.query_devices(), sd.default.hostapi)
            info = sd.query_devices(index, "input") if index is not None else sd.query_devices(kind="input")
        stream = sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32", device=index)
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        return Opened(str(info["name"]), str(info["name"]), _SounddeviceRecorder(stream))
