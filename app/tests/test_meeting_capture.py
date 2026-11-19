"""Meeting capture tracks with fake devices and a fake clock. No audio device is opened."""

import struct
import threading

import numpy as np
import pytest

from gpw.meeting_capture import (
    CaptureError,
    MeetingCapture,
    Opened,
    TrackFile,
    TrackRecorder,
    WavAppender,
    align,
    peak_dbfs,
    to_mono,
    wav_header,
)

SR = 16_000
BLOCK = SR // 10


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeRecorder:
    """Each record() takes block/SR seconds of the fake clock, like a real device."""

    def __init__(self, clock, value=0.25, stereo=False, errors=None, on_block=None):
        self.clock, self.value, self.stereo = clock, value, stereo
        self.errors = list(errors or [])
        self.on_block = on_block
        self.blocks = 0
        self.closed = False

    def record(self, frames):
        self.clock.t += frames / SR
        self.blocks += 1
        if self.on_block:
            self.on_block(self)
        if self.errors and self.errors[0] == self.blocks:
            self.errors.pop(0)
            raise OSError("device unplugged")
        block = np.full(frames, self.value, dtype=np.float32)
        return np.stack([block, block], axis=1) if self.stereo else block

    def close(self):
        self.closed = True


def test_wav_header_is_16_bit_mono():
    header = wav_header(SR, SR)
    assert len(header) == 44 and header[:4] == b"RIFF" and header[8:16] == b"WAVEfmt "
    assert struct.unpack("<HHIIHH", header[20:36]) == (1, 1, SR, SR * 2, 2, 16)
    assert struct.unpack("<I", header[40:44])[0] == SR * 2


def test_appender_round_trip_through_trackfile(tmp_path):
    path = tmp_path / "me.wav"
    w = WavAppender(path, SR, Clock())
    w.write(np.full(SR, 0.5, dtype=np.float32))
    w.write_silence(SR // 2)
    w.close()
    track = TrackFile(path)
    assert track.frames == SR + SR // 2 and track.seconds == pytest.approx(1.5)
    assert track.read(0, 10) == pytest.approx(np.full(10, 0.5), abs=1e-4)
    assert np.all(track.read_seconds(1.0, 1.5) == 0)
    assert sum(len(b) for b in track.blocks(SR // 3)) == track.frames


def test_a_killed_capture_keeps_everything_written(tmp_path):
    """The service was killed mid-call: the header still says 0 frames, but the audio is all there."""
    clock = Clock()
    path = tmp_path / "them.wav"
    w = WavAppender(path, SR, clock)
    w.write(np.full(SR * 3, 0.1, dtype=np.float32))
    w._f.flush()  # the OS has the bytes; no flush() of ours ever patched the header
    assert struct.unpack("<I", path.read_bytes()[40:44])[0] == 0
    track = TrackFile(path)
    assert track.seconds == pytest.approx(3.0)
    w.close()


def test_appender_patches_the_header_every_second(tmp_path):
    clock = Clock()
    path = tmp_path / "them.wav"
    w = WavAppender(path, SR, clock)
    clock.t += 1.5
    w.write(np.zeros(SR, dtype=np.float32))
    assert struct.unpack("<I", path.read_bytes()[40:44])[0] == SR * 2
    w.close()


def test_trackfile_refuses_other_files(tmp_path):
    bad = tmp_path / "x.wav"
    bad.write_bytes(b"not audio")
    with pytest.raises(ValueError):
        TrackFile(bad)


def test_align():
    tol = SR // 2
    assert align(0, 6400, 1600, tol, first=True) == (4800, 0)  # the device took 0.3 s to open
    assert align(4800, 6400, 1600, tol) == (0, 0)  # on time: the block starts where the track ends
    assert align(4800, 6400 + tol - 1, 1600, tol) == (0, 0)  # small drift is left alone
    assert align(4800, 6400 + SR, 1600, tol) == (SR, 0)  # a one-second gap becomes silence
    assert align(4800 + SR, 6400, 1600, tol) == (0, 1600)  # far ahead of the clock: drop, never more than the block
    assert align(4800 + 2000, 6400, 1600, 1000) == (0, 1600)


def test_helpers():
    assert peak_dbfs(np.zeros(5)) == -120.0
    assert peak_dbfs(np.array([0.5])) == pytest.approx(-6.02, abs=0.01)
    assert to_mono(np.array([[0.2, 0.4]], dtype=np.float32)).tolist() == pytest.approx([0.3])


def run_track(tmp_path, opens, *, current_device=None, hold=lambda: False, stop_after_blocks=30):
    """Run a TrackRecorder on this thread until it has read `stop_after_blocks` blocks in all."""
    clock = Clock()
    t0 = clock.t
    writer = WavAppender(tmp_path / "t.wav", SR, clock)
    opened = []
    counter = {"blocks": 0}

    def open_device():
        spec = opens[min(len(opened), len(opens) - 1)]
        clock.t += spec.get("open_seconds", 0.0)
        if spec.get("error"):
            opened.append(None)
            raise OSError("no device")
        rec = FakeRecorder(clock, spec.get("value", 0.25), spec.get("stereo", False), spec.get("errors"),
                           on_block=lambda r: tick())
        opened.append(rec)
        return Opened(spec["id"], f"Device {spec['id']}", rec)

    def tick():
        counter["blocks"] += 1
        if counter["blocks"] >= stop_after_blocks:
            track._stop.set()

    track = TrackRecorder("them", open_device, writer, t0, current_device=current_device, hold=hold, clock=clock, sample_rate=SR)
    track._stop.wait = lambda timeout=None: None  # no real waiting between reopen attempts
    track._run()
    return track, TrackFile(tmp_path / "t.wav"), clock.t - t0, opened


def test_track_lines_up_with_the_clock_from_the_first_block(tmp_path):
    track, wav, elapsed, opened = run_track(tmp_path, [{"id": "A", "open_seconds": 0.3, "stereo": True}], stop_after_blocks=10)
    assert wav.frames == pytest.approx(elapsed * SR, abs=1)
    audio = wav.read(0, wav.frames)
    assert np.all(audio[: int(0.3 * SR)] == 0)  # silence while the device opened
    assert audio[-1] == pytest.approx(0.25, abs=1e-3)
    assert track.device_name == "Device A" and opened[0].closed
    assert track.loudest_dbfs == pytest.approx(peak_dbfs(np.array([0.25])))


def test_new_default_device_is_followed_and_the_gap_is_silence(tmp_path):
    """A headset plugged in mid-call: the loopback reopens on it and the track stays on time."""
    clock_ids = {"n": 0}

    def current():
        clock_ids["n"] += 1
        return "A" if clock_ids["n"] < 2 else "B"

    track, wav, elapsed, opened = run_track(
        tmp_path, [{"id": "A"}, {"id": "B", "open_seconds": 0.8, "value": 0.5}], current_device=current, stop_after_blocks=60,
    )
    assert [o.closed for o in opened] == [True, True] and track.reopens == 1
    assert track.device_name == "Device B"
    assert wav.frames == pytest.approx(elapsed * SR, abs=BLOCK)
    audio = wav.read(0, wav.frames)
    silent = np.flatnonzero(audio == 0)
    assert len(silent) == pytest.approx(0.8 * SR, abs=BLOCK)  # the gap while B opened
    assert audio[-1] == pytest.approx(0.5, abs=1e-3)


def test_device_error_reopens(tmp_path):
    track, wav, elapsed, opened = run_track(tmp_path, [{"id": "A", "errors": [5]}, {"id": "A"}], stop_after_blocks=20)
    assert len(opened) == 2 and track.reopens == 1
    assert wav.frames == pytest.approx(elapsed * SR, abs=BLOCK)


def test_hold_writes_silence_and_is_not_counted_as_loud(tmp_path):
    track, wav, _, _ = run_track(tmp_path, [{"id": "A"}], hold=lambda: True, stop_after_blocks=5)
    assert np.all(wav.read(0, wav.frames) == 0) and track.loudest_dbfs == -120.0


class FakeBackend:
    def __init__(self, loopback_error=None, mic_value=0.2, them_value=0.3):
        self.loopback_error = loopback_error
        self.mic_value, self.them_value = mic_value, them_value
        self.opened = []

    def output_id(self):
        return "spk"

    def _rec(self, value):
        backend = self

        class SleepyRecorder:
            def record(self, frames):
                threading.Event().wait(frames / SR)
                return np.full(frames, value, dtype=np.float32)

            def close(self):
                backend.opened.append("closed")

        return SleepyRecorder()

    def open_loopback(self, sample_rate):
        if self.loopback_error:
            raise self.loopback_error
        self.opened.append("loopback")
        return Opened("spk", "Speakers", self._rec(self.them_value))

    def open_mic(self, sample_rate):
        self.opened.append("mic")
        return Opened("mic", "USB Mic", self._rec(self.mic_value))


def test_capture_writes_two_tracks_on_one_clock(tmp_path):
    capture = MeetingCapture(tmp_path / "m", FakeBackend(), capture_mic=True, thread_init=None)
    capture.start()
    threading.Event().wait(0.5)
    capture.muted = True
    threading.Event().wait(0.3)
    seconds = capture.stop()
    them, me = TrackFile(tmp_path / "m" / "them.wav"), TrackFile(tmp_path / "m" / "me.wav")
    assert abs(them.seconds - me.seconds) < 0.25 and seconds == pytest.approx(max(them.seconds, me.seconds))
    tail = me.read(me.frames - 1600, 1600)
    assert np.all(tail == 0)  # muted at the end
    assert capture.device_names() == {"them": "Speakers", "me": "USB Mic"}
    assert capture.heard_call_audio()


def test_call_audio_only(tmp_path):
    capture = MeetingCapture(tmp_path / "m", FakeBackend(), capture_mic=False, thread_init=None)
    capture.start()
    capture.stop()
    assert (tmp_path / "m" / "them.wav").is_file() and not (tmp_path / "m" / "me.wav").exists()


def test_no_loopback_device_fails_the_start(tmp_path):
    capture = MeetingCapture(tmp_path / "m", FakeBackend(loopback_error=OSError("no speaker")), thread_init=None)
    with pytest.raises(CaptureError, match="call audio"):
        capture.start()


def test_no_call_audio_after_two_minutes(tmp_path):
    clock = Clock()
    capture = MeetingCapture(tmp_path / "m", FakeBackend(), clock=clock)
    capture.t0 = clock.t

    class Quiet:
        loudest_dbfs = -90.0

    capture.tracks = {"them": Quiet()}
    clock.t += 119
    assert not capture.no_call_audio()
    clock.t += 2
    assert capture.no_call_audio()
    capture.tracks["them"].loudest_dbfs = -20.0
    assert not capture.no_call_audio()
