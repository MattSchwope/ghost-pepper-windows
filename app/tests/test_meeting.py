"""Meeting capture from two tracks to a note, the manager the service drives, its HTTP endpoints and
`gpw meeting`. OpenAI and the audio devices are faked; nothing leaves the machine."""

import http.client
import json
import logging
import threading
import time
from datetime import datetime

import numpy as np
import pytest

from gpw import cli, logging_setup
from gpw.config import parse_config
from gpw.dictionary import DictionaryStore
from gpw.meeting import (
    MeetingBusy,
    MeetingManager,
    MeetingProcessor,
    MeetingResult,
    NotCapturing,
    app_label,
    effective_status,
    find_meeting,
    list_meetings,
    new_folder,
    read_meta,
)
from gpw.meeting_capture import CaptureError, Opened, TrackFile
from gpw.server import make_server
from gpw.service import DictationService
from meeting_fakes import APIConnectionError, ME_WORDS, THEM_WORDS, FakeSummaryModel, ToneEngine, make_meeting
from test_service import FakeEngine, FakeMic

SR = 16_000
NOW = datetime(2026, 11, 18, 14, 5, 0)


def make_config(tmp_path, **sections):
    raw = {"app": {"data_dir": str(tmp_path / "data")}, "transcription": {"languages": ["en"]},
           "dictation": {"prewarm_connection": False}}
    for name, values in sections.items():
        raw.setdefault(name, {}).update(values)
    return parse_config(raw)


def dictionary(tmp_path):
    path = tmp_path / "dictionary.yaml"
    path.write_text('terms: [Kavaliro]\nreplace:\n  "cavaliro": "Kavaliro"\n', encoding="utf-8")
    return DictionaryStore(path)


def processor(tmp_path, engine=None, model=None, **sections):
    config = make_config(tmp_path, **sections)
    return MeetingProcessor(config, engine or ToneEngine(), model or FakeSummaryModel(), dictionary(tmp_path), workers=1), config


# -- from two tracks to a note --

def test_two_tracks_become_a_labeled_note(tmp_path):
    proc, config = processor(tmp_path)
    folder = make_meeting(config.meetings_dir)
    result = proc.process(folder)

    assert result.status == "done"
    assert result.note == config.notes_dir / "meetings" / "2026-11-18 1405 Contract review Acme Kavaliro.md"
    note = result.note.read_text(encoding="utf-8")
    fixed = ME_WORDS.replace("cavaliro", "Kavaliro")  # dictionary fix; no light cleanup
    assert note == (
        '---\ntype: meeting\ndate: 2026-11-18\nstart: "14:05"\nduration: "00:00:16"\napp: Teams\n'
        "engine: gpt-transcribe\nsummary_model: gpt-6.1-sol\nspeakers: [Me, Them]\n---\n"
        "# Contract review: Acme / Kavaliro\n\n## Summary\n\n### Overview\nThe contract goes to legal.\n\n"
        "### Action items\n- [ ] Me: send the contract (due Friday)\n\n## Transcript\n\n"
        f"[00:00:00] Me: {fixed}\n\n[00:00:03] Them: {THEM_WORDS}\n\n"
        f"[00:00:08] Me: {fixed}\n\n[00:00:11] Them: {THEM_WORDS}\n"
    )  # the echo of Them at 00:00:11 on the mic track is gone
    meta = read_meta(folder)
    assert (meta["status"], meta["note"]) == ("done", str(result.note))
    assert not (folder / "them.wav").exists() and not (folder / "me.wav").exists() and not (folder / "work").exists()


def test_summary_gets_the_transcript_and_context(tmp_path):
    model = FakeSummaryModel()
    proc, config = processor(tmp_path, model=model)
    proc.process(make_meeting(config.meetings_dir))
    instructions, text = model.inputs[0]
    assert "Title:" in instructions and "Them" in instructions
    assert text.startswith("Date: 2026-11-18 14:05\nLength: 00:00:16\nRecorded from: Teams\n\n[00:00:00] Me:")


def test_labels_are_right_on_every_line(tmp_path):
    """Done-when check, run on synthetic audio: every line carries the right Me or Them label."""
    proc, config = processor(tmp_path)
    note = proc.process(make_meeting(config.meetings_dir, echo=False)).note.read_text(encoding="utf-8")
    lines = [line for line in note.split("## Transcript")[1].splitlines() if line.startswith("[")]
    assert len(lines) == 4
    for line in lines:
        speaker, text = line[11:].split(": ", 1)
        assert (speaker, text) in {("Me", ME_WORDS.replace("cavaliro", "Kavaliro")), ("Them", THEM_WORDS)}


def test_call_audio_only(tmp_path):
    model = FakeSummaryModel()
    proc, config = processor(tmp_path, model=model)
    result = proc.process(make_meeting(config.meetings_dir, mic=False, app=""))
    note = result.note.read_text(encoding="utf-8")
    assert "speakers: [Them]\n" in note and "app: unknown\n" in note and "] Me:" not in note.split("## Transcript")[1]
    assert "Only the call audio was recorded" in model.inputs[0][1]


def test_keep_audio(tmp_path):
    proc, config = processor(tmp_path, meetings={"keep_audio": True})
    folder = make_meeting(config.meetings_dir)
    assert proc.process(folder).status == "done"
    assert (folder / "them.wav").is_file() and (folder / "me.wav").is_file() and not (folder / "work").exists()


def test_failure_keeps_audio_and_retry_finishes_from_the_cache(tmp_path):
    proc, config = processor(tmp_path, engine=ToneEngine(fail_on=2))
    folder = make_meeting(config.meetings_dir, echo=False)
    result = proc.process(folder)
    assert result.status == "failed" and result.message == "Meeting transcription failed: Could not reach OpenAI."
    meta = read_meta(folder)
    assert (meta["status"], meta["stage"]) == ("failed", "transcription")
    assert (folder / "them.wav").is_file() and len(list((folder / "work").glob("*.txt"))) >= 2

    engine = ToneEngine()
    proc.engine = engine
    result = proc.process(folder)
    assert result.status == "done" and len(engine.calls) <= 2  # only the turns that never came back
    assert not (folder / "them.wav").exists()


def test_summary_failure_is_retried_without_new_transcription(tmp_path):
    proc, config = processor(tmp_path, model=FakeSummaryModel(error=APIConnectionError("down")))
    folder = make_meeting(config.meetings_dir)
    assert proc.process(folder).message == "Meeting summary failed: Could not reach OpenAI."
    proc.text_model = FakeSummaryModel()
    engine = ToneEngine()
    proc.engine = engine
    assert proc.process(folder).status == "done" and engine.calls == []


def test_silence_writes_no_note_and_keeps_the_audio(tmp_path):
    from meeting_fakes import write_track

    proc, config = processor(tmp_path)
    folder = make_meeting(config.meetings_dir)
    write_track(folder / "them.wav", np.zeros(SR * 5, dtype=np.float32))
    write_track(folder / "me.wav", np.zeros(SR * 5, dtype=np.float32))
    result = proc.process(folder)
    assert result.status == "empty" and result.note is None and "No speech" in result.message
    assert read_meta(folder)["status"] == "empty" and (folder / "them.wav").is_file()


def test_an_interrupted_capture_becomes_a_note(tmp_path):
    """The service was killed mid-call: the track headers were never finished, the audio is all there."""
    proc, config = processor(tmp_path)
    folder = make_meeting(config.meetings_dir, status="recording", pid=999_999_999)
    for track in ("them.wav", "me.wav"):
        data = bytearray((folder / track).read_bytes())
        data[40:44] = b"\0\0\0\0"  # the header as it is before the first flush
        (folder / track).write_bytes(bytes(data))
    assert TrackFile(folder / "them.wav").seconds == pytest.approx(16.0)
    assert list_meetings(config.meetings_dir)[0].status == "interrupted"
    assert proc.process(folder).status == "done"


def test_no_transcript_text_or_title_in_the_log(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    proc, config = processor(tmp_path)
    proc.process(make_meeting(config.meetings_dir))
    log = caplog.text.lower()
    assert "Meeting 2026-11-18 1405: note written" in caplog.text and "echo_dropped=1" in caplog.text
    for secret in ("contract", "kavaliro", "legal", "friday", "acme"):
        assert secret not in log


# -- folders and statuses --

def test_app_label():
    assert app_label("ms-teams.exe") == "Teams" and app_label("Zoom.exe") == "Zoom"
    assert app_label("msedge.exe") == "Edge" and app_label("Notepad.exe") == "Notepad" and app_label("") == ""


def test_new_folder_names_are_unique(tmp_path):
    a, b = new_folder(tmp_path, NOW), new_folder(tmp_path, NOW)
    assert (a.name, b.name) == ("2026-11-18 1405", "2026-11-18 1405 (2)")


def test_effective_status():
    alive = lambda pid: pid == 7  # noqa: E731
    assert effective_status({"status": "recording", "pid": 7}, alive) == "recording"
    assert effective_status({"status": "recording", "pid": 8}, alive) == "interrupted"
    assert effective_status({"status": "processing", "pid": 8}, alive) == "stopped"
    assert effective_status({"status": "done"}, alive) == "done"


def test_find_meeting(tmp_path):
    folder = make_meeting(tmp_path)
    assert find_meeting(tmp_path, "2026-11-18 1405") == folder == find_meeting(tmp_path, str(folder))
    with pytest.raises(FileNotFoundError, match="gpw meeting list"):
        find_meeting(tmp_path, "nope")


# -- the manager the service drives --

class FakeBackend:
    """Loopback and mic that hand out tones in real time."""

    def __init__(self, loopback_error=None):
        self.loopback_error = loopback_error

    def output_id(self):
        return "spk"

    def _recorder(self, value):
        class Rec:
            def record(self, frames):
                time.sleep(frames / SR)
                return np.full(frames, value, dtype=np.float32)

            def close(self):
                pass

        return Rec()

    def open_loopback(self, sample_rate):
        if self.loopback_error:
            raise self.loopback_error
        return Opened("spk", "Speakers", self._recorder(0.3))

    def open_mic(self, sample_rate):
        return Opened("mic", "USB Mic", self._recorder(0.2))


class FakeProcessor:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.folders = []

    def process(self, folder):
        self.folders.append(folder.name)
        status = self.results.pop(0) if self.results else "done"
        if status == "failed":
            return MeetingResult("failed", folder, message="Meeting summary failed: Could not reach OpenAI.")
        return MeetingResult(status, folder, note=folder.parent / f"{folder.name} note.md")


@pytest.fixture
def manager(tmp_path):
    made = []

    def factory(backend=None, results=None, **sections):
        config = make_config(tmp_path, **sections)
        fake = FakeProcessor(results)
        m = MeetingManager(config, lambda: backend or FakeBackend(), lambda: fake, now=lambda: NOW,
                           thread_init=None, first_open_wait=2.0)
        m.fake = fake
        made.append(m)
        return m

    yield factory
    for m in made:
        if m.capturing:
            m.stop()
        m.close()


def test_start_mute_stop_and_the_note_is_announced(manager):
    m = manager()
    started = m.start("ms-teams.exe")
    assert started == {"folder": "2026-11-18 1405", "capture_mic": True, "max_hours": 4.0}
    folder = m.root / "2026-11-18 1405"
    meta = read_meta(folder)
    assert (meta["status"], meta["app"], meta["devices"]) == ("recording", "Teams", {"them": "Speakers", "me": "USB Mic"})
    with pytest.raises(MeetingBusy):
        m.start()
    time.sleep(0.3)
    assert m.set_mute(True) is True
    status = m.status()
    assert status["capturing"] and status["muted"] and status["mic_track"] and status["folder"] == "2026-11-18 1405"
    time.sleep(0.3)
    stopped = m.stop()
    assert stopped["folder"] == "2026-11-18 1405" and stopped["seconds"] > 0.4
    assert m.wait_idle()
    status = m.status()
    assert not status["capturing"] and status["done_seq"] == 1 and status["done_status"] == "done"
    assert status["done_note"].endswith("2026-11-18 1405 note.md") and status["stopped_reason"] == "hotkey"
    assert m.fake.folders == ["2026-11-18 1405"]
    me = TrackFile(folder / "me.wav")
    assert np.all(me.read(me.frames - 1600, 1600) == 0)  # muted at the end
    with pytest.raises(NotCapturing):
        m.stop()


def test_dictation_stays_out_of_the_mic_track(manager):
    m = manager()
    m.start()
    time.sleep(0.3)
    m.set_dictating(True)
    time.sleep(0.4)
    m.set_dictating(False)
    time.sleep(0.3)
    m.stop()
    me = TrackFile(m.root / "2026-11-18 1405" / "me.wav").read(0, 10**9)
    silent = np.flatnonzero(me == 0)
    assert len(silent) == pytest.approx(0.4 * SR, abs=0.15 * SR) and me[-1] != 0


def test_call_audio_only_has_no_mic_track(manager):
    m = manager(meetings={"capture_mic": False})
    assert m.start()["capture_mic"] is False
    assert not m.status()["mic_track"]
    m.stop()
    assert not (m.root / "2026-11-18 1405" / "me.wav").exists()
    with pytest.raises(NotCapturing):
        m.set_mute(True)


def test_a_start_that_cannot_record_the_call_leaves_nothing(manager):
    m = manager(backend=FakeBackend(loopback_error=OSError("no speaker")))
    with pytest.raises(CaptureError, match="call audio"):
        m.start()
    assert not m.capturing and not any(m.root.iterdir())


def test_stops_on_its_own_at_max_hours(manager):
    m = manager(meetings={"max_hours": 0.0002})  # 0.72 s
    m.start()
    deadline = time.monotonic() + 5
    while m.capturing and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not m.capturing and m.wait_idle()
    status = m.status()
    assert status["stopped_reason"] == "max_hours" and status["done_seq"] == 1
    assert read_meta(m.root / "2026-11-18 1405")["stop_reason"] == "max_hours"


def test_failure_is_shown_and_can_be_retried(manager):
    m = manager(results=["failed", "done"])
    m.start()
    m.stop()
    assert m.wait_idle()
    status = m.status()
    assert status["failed_seq"] == 1 and status["retry"] == "2026-11-18 1405"
    assert status["failed_message"] == "Meeting summary failed: Could not reach OpenAI."
    assert status["failed_folder"].endswith("/2026-11-18 1405")
    assert m.retry() == "2026-11-18 1405"
    assert m.wait_idle()
    status = m.status()
    assert status["done_seq"] == 1 and status["retry"] == "" and m.fake.folders == ["2026-11-18 1405"] * 2


def test_startup_finishes_stopped_captures_and_offers_interrupted_ones(manager, tmp_path):
    m = manager()
    make_meeting(m.root, "2026-11-17 0900", status="stopped")
    make_meeting(m.root, "2026-11-17 1000", status="processing", pid=999_999_999)  # cut off while processing
    make_meeting(m.root, "2026-11-17 1100", status="recording", pid=999_999_999)  # service killed mid-call
    m.open()
    assert m.wait_idle()
    assert sorted(m.fake.folders) == ["2026-11-17 0900", "2026-11-17 1000"]
    assert m.status()["retry"] == "2026-11-17 1100" and m.status()["failed_seq"] == 0  # offered, not announced
    assert m.retry("2026-11-17 1100") == "2026-11-17 1100"
    assert m.wait_idle()


def test_retry_refuses_finished_or_unknown_captures(manager):
    m = manager()
    folder = make_meeting(m.root, status="done")
    with pytest.raises(ValueError, match="already has its note"):
        m.retry(folder.name)
    with pytest.raises(FileNotFoundError):
        m.retry("nope")
    with pytest.raises(FileNotFoundError, match="no failed meeting"):
        m.retry()


def test_shutdown_mid_call_keeps_the_capture_for_the_next_start(manager):
    m = manager()
    m.start()
    time.sleep(0.3)
    m.close()
    meta = read_meta(m.root / "2026-11-18 1405")
    assert (meta["status"], meta["stop_reason"]) == ("stopped", "shutdown") and m.fake.folders == []


# -- the dictation service --

class FakeMeetings:
    def __init__(self):
        self.capturing = True
        self.holds = []
        self.closed = False

    def set_dictating(self, on):
        self.holds.append(on)

    def open(self):
        pass

    def close(self):
        self.closed = True


def test_dictation_marks_its_audio_and_skips_rescans_during_a_capture(tmp_path):
    meetings = FakeMeetings()
    mic = FakeMic()
    service = DictationService(make_config(tmp_path), FakeEngine(), mic, meetings=meetings)
    service._last_refresh = -1e9  # a rescan is due
    service.start()
    assert service.stop().status == "ok"
    service.start()
    service.cancel()
    service.close()
    assert meetings.holds == [True, False, True, False]
    assert "refresh" not in mic.events and meetings.closed
    assert service.health()["meeting_key"] == "^!m"


# -- HTTP --

def request(server, path, body="", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    conn.request("POST", path, body=body, headers={"X-GPW-Client": "test", "Content-Type": "application/x-www-form-urlencoded", **(headers or {})})
    response = conn.getresponse()
    data = json.loads(response.read() or b"{}")
    conn.close()
    return response.status, data


def test_meeting_endpoints(manager, tmp_path):
    m = manager()
    service = DictationService(make_config(tmp_path), FakeEngine(), FakeMic(), meetings=m)
    server = make_server(service, 0)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        assert request(server, "/meeting/start", "app=", {"X-GPW-Client": ""})[0] == 403
        assert request(server, "/meeting/stop")[0] == 409
        status, body = request(server, "/meeting/start", "app=Zoom.exe")
        assert status == 200 and body["folder"] == "2026-11-18 1405"
        assert request(server, "/meeting/start")[0] == 409
        assert request(server, "/meeting/mute", "on=1")[1]["muted"] is True
        status, body = request(server, "/meeting/status")
        assert status == 200 and body["capturing"] and body["muted"]
        assert request(server, "/meeting/stop")[0] == 200
        assert m.wait_idle()
        assert request(server, "/meeting/status")[1]["done_seq"] == 1
        assert request(server, "/meeting/retry")[0] == 400
        assert request(server, "/meeting/nope")[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        service.close()


def test_meeting_endpoints_without_meetings(tmp_path):
    service = DictationService(make_config(tmp_path), FakeEngine(), FakeMic())
    server = make_server(service, 0)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        assert request(server, "/meeting/status")[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        service.close()


# -- gpw meeting --

@pytest.fixture
def cli_config(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_setup, "configure_logging", lambda logs_dir: None)
    data = tmp_path / "data"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f'app:\n  data_dir: "{data.as_posix()}"\n', encoding="utf-8")
    engine = ToneEngine()
    monkeypatch.setattr(
        cli, "_meeting_processor",
        lambda config, dry_run=False, client=None: MeetingProcessor(config, engine, FakeSummaryModel(), workers=1),
    )
    return cfg, data / "meetings", engine


def run_cli(*args):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(list(args))
    return exit_info.value.code


def test_gpw_meeting_list_and_retry(cli_config, capsys):
    cfg, root, engine = cli_config
    assert run_cli("--config", str(cfg), "meeting", "list") == 0
    assert "No meeting captures" in capsys.readouterr().out

    make_meeting(root, "2026-11-18 1405", status="recording", pid=999_999_999)
    failed = make_meeting(root, "2026-11-18 1500", status="failed")
    meta = read_meta(failed)
    meta.update(stage="summary", error="Could not reach OpenAI.", started="2026-11-18T15:00:00")
    (failed / "capture.json").write_text(json.dumps(meta), encoding="utf-8")
    assert run_cli("--config", str(cfg), "meeting", "list") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[2].startswith("2026-11-18 1500      failed       00:00:16  summary failed: Could not reach OpenAI. Run: gpw")
    assert out[3].startswith("2026-11-18 1405      interrupted  00:00:16  cut off while recording")
    assert 'gpw meeting retry "2026-11-18 1405"' in out[3]

    assert run_cli("--config", str(cfg), "meeting", "retry", "2026-11-18 1405") == 0
    printed = capsys.readouterr().out
    assert printed.startswith("Note written: ") and "2026-11-18 1405 Contract review" in printed
    assert run_cli("--config", str(cfg), "meeting", "retry", "2026-11-18 1405") == 0
    assert "already has its note" in capsys.readouterr().out
    assert run_cli("--config", str(cfg), "meeting", "list") == 0
    assert "done" in capsys.readouterr().out.splitlines()[3]


def test_gpw_meeting_retry_refuses_a_live_capture(cli_config, capsys):
    import os

    cfg, root, engine = cli_config
    make_meeting(root, status="recording", pid=os.getpid())
    assert run_cli("--config", str(cfg), "meeting", "retry", "2026-11-18 1405") == 1
    assert "is recording in the running service" in capsys.readouterr().out and engine.calls == []


def test_dry_run_processing_needs_no_network(tmp_path):
    proc = cli._meeting_processor(make_config(tmp_path), dry_run=True)
    result = proc.process(make_meeting(proc.config.meetings_dir))
    note = result.note.read_text(encoding="utf-8")
    assert result.note.name == "2026-11-18 1405 Dry run meeting.md" and "Dry run: heard" in note
