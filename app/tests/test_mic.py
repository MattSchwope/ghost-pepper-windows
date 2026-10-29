"""MicCapture against a fake sounddevice module. No real audio device is opened."""

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from gpw.mic import MicCapture, MicError, MicTimeout

DEVICES = [
    {"name": "Microsoft Sound Mapper - Input", "max_input_channels": 2, "hostapi": 0},
    {"name": "Microphone (USB Condenser Micro", "max_input_channels": 1, "hostapi": 0},
    {"name": "Headset Microphone (Poly BT700)", "max_input_channels": 1, "hostapi": 0},
]


class FakeStream:
    def __init__(self, sd, callback, channels, device, **kwargs):
        self.sd, self.callback, self.channels, self.device = sd, callback, channels, device
        self.started = self.closed = self.stopped = self.aborted = False

    def start(self):
        if self.sd.hang is not None:
            self.sd.hang.wait()
        if self.sd.fail:
            raise RuntimeError("Error opening InputStream: Invalid device")
        self.started = True

    def stop(self):
        self.stopped = True

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True

    def feed(self, value, frames=160):
        self.callback(np.full((frames, self.channels), value, dtype=np.float32), frames, None, None)


class FakeSD:
    def __init__(self):
        self.default = SimpleNamespace(hostapi=0)
        self.streams = []
        self.hang = None
        self.fail = False
        self.rescans = 0

    def query_devices(self, index=None, kind=None):
        if index is None and kind is None:
            return DEVICES
        if index is None:
            return DEVICES[1]
        return DEVICES[index]

    def InputStream(self, **kwargs):
        stream = FakeStream(self, **kwargs)
        self.streams.append(stream)
        return stream

    def _terminate(self):
        self.rescans += 1

    def _initialize(self):
        pass


@pytest.fixture
def sd():
    return FakeSD()


def test_cold_mic_opens_on_start_and_closes_on_stop(sd):
    mic = MicCapture(sample_rate=16_000, sd=sd)
    assert sd.streams == []
    mic.start()
    stream = sd.streams[0]
    assert stream.started and mic.mic_name == "Microphone (USB Condenser Micro"
    stream.feed(0.1)
    stream.feed(0.2)
    audio = mic.stop()
    assert stream.stopped and stream.closed
    assert audio.shape == (320,) and audio.dtype == np.float32
    assert audio[0] == pytest.approx(0.1) and audio[-1] == pytest.approx(0.2)


def test_named_mic_is_used(sd):
    mic = MicCapture(device="Poly BT700", sd=sd)
    mic.start()
    assert sd.streams[0].device == 2
    assert mic.mic_name == "Headset Microphone (Poly BT700)"


def test_stereo_mic_is_mixed_to_mono(sd):
    mic = MicCapture(channels=2, sd=sd)
    mic.start()
    sd.streams[0].callback(np.array([[0.2, 0.4]] * 10, dtype=np.float32), 10, None, None)
    assert mic.stop().tolist() == pytest.approx([0.3] * 10)


def test_recording_stops_growing_at_the_cap(sd):
    mic = MicCapture(sample_rate=1000, max_seconds=0.5, sd=sd)
    mic.start()
    for _ in range(10):
        sd.streams[0].feed(0.1, frames=100)
    assert mic.capped
    assert len(mic.stop()) == 500


def test_warm_mic_keeps_preroll_and_stays_open(sd):
    mic = MicCapture(sample_rate=1000, warm=True, preroll_ms=300, sd=sd)
    mic.open_warm()
    stream = sd.streams[0]
    for value in (0.1, 0.2, 0.3, 0.4, 0.5):  # 100 ms blocks before the key press
        stream.feed(value, frames=100)
    mic.start()
    stream.feed(0.9, frames=100)
    audio = mic.stop()
    # The last 300 ms before the press survive, then the recording itself.
    assert audio.tolist() == pytest.approx([0.3] * 100 + [0.4] * 100 + [0.5] * 100 + [0.9] * 100)
    assert not stream.closed and len(sd.streams) == 1
    mic.start()
    assert len(sd.streams) == 1  # reused, not reopened


def test_mic_that_hangs_on_open_times_out(sd):
    sd.hang = threading.Event()
    mic = MicCapture(open_timeout=0.2, sd=sd)
    started = time.perf_counter()
    with pytest.raises(MicTimeout, match="did not open within 0.2 s"):
        mic.start()
    assert time.perf_counter() - started < 1.0
    with pytest.raises(MicTimeout, match="still stuck"):
        mic.start()

    sd.hang.set()  # the driver finally answers: the late stream is closed, and the mic works again
    deadline = time.time() + 2
    while not sd.streams[0].closed and time.time() < deadline:
        time.sleep(0.01)
    assert sd.streams[0].closed
    sd.hang = None
    mic.start()
    assert sd.streams[-1].started


def test_mic_open_failure_is_a_clear_error(sd):
    sd.fail = True
    mic = MicCapture(sd=sd)
    with pytest.raises(MicError, match="Could not open the mic 'Microphone \\(USB Condenser Micro'"):
        mic.start()
    assert sd.streams[0].closed


def test_cancel_discards_audio(sd):
    mic = MicCapture(sd=sd)
    mic.start()
    sd.streams[0].feed(0.5)
    mic.cancel()
    assert sd.streams[0].closed
    mic.start()
    assert mic.stop().size == 0


def test_refresh_devices_only_while_closed(sd):
    mic = MicCapture(sd=sd)
    mic.refresh_devices()
    assert sd.rescans == 1
    mic.start()
    mic.refresh_devices()
    assert sd.rescans == 1
    mic.stop()
    warm = MicCapture(warm=True, sd=sd)
    warm.refresh_devices()
    assert sd.rescans == 1
