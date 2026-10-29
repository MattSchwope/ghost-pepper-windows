"""Mic capture for the dictation service: stream into memory between start() and stop().

Cold (default): the device opens on start() and closes on stop().
Warm: the device stays open and keeps the last `preroll_ms` of audio, so a first
word spoken during a slow key press survives. Windows shows the mic as in use
the whole time.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Any

import numpy as np

from .recorder import pick_input_device

logger = logging.getLogger(__name__)


class MicError(RuntimeError):
    pass


class MicTimeout(MicError):
    """The device never finished opening. Retrying would only hang again."""


def _load_sounddevice() -> Any:
    import sounddevice

    return sounddevice


class MicCapture:
    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        device: int | str | None = None,
        open_timeout: float = 3.0,
        max_seconds: float = 600.0,
        warm: bool = False,
        preroll_ms: int = 300,
        sd: Any = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device
        self.open_timeout = open_timeout
        self.max_frames = int(max_seconds * sample_rate)
        self.warm = warm
        self.preroll_frames = sample_rate * preroll_ms // 1000
        self.sd = sd or _load_sounddevice()
        self.mic_name = ""
        self.capped = False
        self._lock = threading.Lock()
        self._stream: Any = None
        self._recording = False
        self._blocks: list[np.ndarray] = []
        self._frames = 0
        self._ring: collections.deque[np.ndarray] = collections.deque()
        self._ring_frames = 0
        self._overflows = 0
        self._stuck_open = False

    # -- PortAudio callback (runs on the audio thread) --
    def _callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        if status:
            self._overflows += 1
        block = np.array(indata, dtype=np.float32, copy=True)
        with self._lock:
            if self._recording:
                if self._frames + len(block) > self.max_frames:
                    self.capped = True
                    return
                self._blocks.append(block)
                self._frames += len(block)
            elif self.warm:
                self._ring.append(block)
                self._ring_frames += len(block)
                while self._ring and self._ring_frames - len(self._ring[0]) >= self.preroll_frames:
                    self._ring_frames -= len(self._ring.popleft())

    def _open(self) -> None:
        """Open and start the input stream, giving up after `open_timeout` (a Bluetooth headset once hung forever)."""
        if self._stuck_open:
            raise MicTimeout("The mic is still stuck opening from last time. Restart Ghost Pepper or pick another mic.")
        index = pick_input_device(self.device, self.sd.query_devices(), self.sd.default.hostapi)
        info = self.sd.query_devices(index, "input") if index is not None else self.sd.query_devices(kind="input")
        name = str(info["name"])
        guard = threading.Lock()
        done = threading.Event()
        result: dict[str, Any] = {}

        def worker() -> None:
            stream, error = None, None
            try:
                stream = self.sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype="float32",
                    device=index,
                    latency="low",
                    callback=self._callback,
                )
                stream.start()
            except Exception as exc:  # reported to the caller below
                error = exc
                if stream is not None:
                    stream.close()
                stream = None
            with guard:
                if result.get("abandoned"):
                    self._stuck_open = False
                    if stream is not None:
                        stream.close()
                    logger.warning("Mic %s finished opening after the timeout; closed it", name)
                    return
                if error is not None:
                    result["error"] = error
                else:
                    result["stream"] = stream
                done.set()

        started = time.perf_counter()
        threading.Thread(target=worker, name="gpw-mic-open", daemon=True).start()
        done.wait(self.open_timeout)
        with guard:
            if not done.is_set():
                result["abandoned"] = True
                self._stuck_open = True
                raise MicTimeout(
                    f"The mic '{name}' did not open within {self.open_timeout:g} s. "
                    "Pick another mic with recording.input_device."
                )
        if "error" in result:
            raise MicError(f"Could not open the mic '{name}': {result['error']}") from result["error"]
        self._stream = result["stream"]
        self.mic_name = name
        logger.info("Mic open: %s took=%.3fs", name, time.perf_counter() - started)

    def open_warm(self) -> None:
        if self.warm and self._stream is None:
            self._open()

    def start(self) -> None:
        with self._lock:
            if self._recording:
                raise MicError("Already recording")
        if self._stream is None:
            self._open()
        with self._lock:
            self._blocks = list(self._ring) if self.warm else []
            self._frames = sum(len(b) for b in self._blocks)
            self._ring.clear()
            self._ring_frames = 0
            self.capped = False
            self._overflows = 0
            self._recording = True

    def stop(self) -> np.ndarray:
        """Stop recording and return mono float32 audio."""
        if not self.warm and self._stream is not None:
            stream, self._stream = self._stream, None
            try:
                stream.stop()  # waits for buffered audio, so the last word is kept
            finally:
                stream.close()
        with self._lock:
            self._recording = False
            blocks, self._blocks, self._frames = self._blocks, [], 0
        if self._overflows:
            logger.warning("Mic dropped audio %s times during this recording", self._overflows)
        if not blocks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(blocks)
        return audio.mean(axis=1, dtype=np.float32) if audio.ndim == 2 else audio.astype(np.float32)

    def cancel(self) -> None:
        self.stop()

    def refresh_devices(self) -> None:
        """Re-read the device list so a mic plugged in after startup is seen. Only while the mic is closed."""
        if self.warm or self._stream is not None:
            return
        terminate = getattr(self.sd, "_terminate", None)
        initialize = getattr(self.sd, "_initialize", None)
        if terminate and initialize:
            terminate()
            initialize()

    def close(self) -> None:
        with self._lock:
            self._recording = False
        if self._stream is not None:
            stream, self._stream = self._stream, None
            stream.abort()
            stream.close()
