"""DictationService with a fake mic and a fake engine. No audio device, no network."""

import io
import json
import logging
import threading
import time
from datetime import datetime

import numpy as np
import pytest
import soundfile as sf

from gpw.config import parse_config
from gpw.engines import TimedText
from gpw.mic import MicError, MicTimeout
from gpw.service import DictationService, ServiceBusy

SR = 16_000
SECRET = "Meet Kavaliro at noon."  # stands in for private dictation text


def speech(seconds=1.0, amp=0.2):
    t = np.arange(int(seconds * SR)) / SR
    voiced = (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    pad = np.zeros(SR // 2, dtype=np.float32)
    return np.concatenate([pad, voiced, pad])


class FakeMic:
    sample_rate = SR

    def __init__(self, audio=None):
        self.audio = speech() if audio is None else audio
        self.mic_name = ""
        self.capped = False
        self.start_errors = []
        self.events = []

    def start(self):
        self.events.append("start")
        if self.start_errors:
            raise self.start_errors.pop(0)
        self.mic_name = "Microphone (USB Condenser Micro"

    def stop(self):
        self.events.append("stop")
        return self.audio

    def cancel(self):
        self.events.append("cancel")

    def refresh_devices(self):
        self.events.append("refresh")

    def open_warm(self):
        pass

    def close(self):
        self.events.append("close")


class FakeEngine:
    model = "gpt-transcribe"

    def __init__(self, text=SECRET, error=None, gate=None):
        self.text, self.error, self.gate = text, error, gate
        self.calls = []
        self.prewarms = 0

    def transcribe_clip(self, filename, data, keywords, languages, timeout):
        self.calls.append({"filename": filename, "data": data, "keywords": keywords, "languages": languages, "timeout": timeout})
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        return TimedText(self.text, 0.42, 0.31)

    def prewarm(self):
        self.prewarms += 1


def make_config(tmp_path, **sections):
    raw = {
        "app": {"data_dir": str(tmp_path / "data")},
        "transcription": {"keywords": ["Kavaliro", "SOCOM"], "languages": ["en"]},
        "dictation": {"prewarm_connection": False},
    }
    for name, values in sections.items():
        raw.setdefault(name, {}).update(values)
    return parse_config(raw)


@pytest.fixture
def make_service(tmp_path):
    services = []

    def factory(mic=None, engine=None, clean=None, **sections):
        config = make_config(tmp_path, **sections)
        service = DictationService(
            config, engine or FakeEngine(), mic or FakeMic(), clean=clean,
            now=lambda: datetime(2026, 10, 28, 9, 15, 0),
        )
        services.append(service)
        return service

    yield factory
    for service in services:
        service.close()


def latency_records(service):
    path = service.latency_log
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_start_stop_returns_text_and_sends_prepped_flac(make_service, caplog):
    caplog.set_level(logging.INFO)
    engine = FakeEngine()
    service = make_service(engine=engine)

    assert service.start() == {"id": 1, "mic": "Microphone (USB Condenser Micro"}
    assert service.state == "recording"
    result = service.stop()

    assert (result.status, result.dictation_id, result.text) == ("ok", 1, SECRET)
    assert service.state == "idle"
    call = engine.calls[0]
    assert call["filename"] == "dictation.flac"
    assert call["keywords"] == ["Kavaliro", "SOCOM"] and call["languages"] == ["en"]
    sent, rate = sf.read(io.BytesIO(call["data"]))
    assert rate == SR and len(sent) / SR == pytest.approx(1.6, abs=0.05)  # trimmed to speech + 0.3 s pads
    assert call["timeout"] == pytest.approx(31.6, abs=0.1)
    assert "Dictation id=1 ready mode=hold" in caplog.text
    assert "api=0.42s server=0.31s" in caplog.text


def test_transcript_never_reaches_the_log_by_default(make_service, caplog):
    caplog.set_level(logging.DEBUG)
    service = make_service()
    service.start()
    service.stop()
    service.pasted(1, 1300, 40, True)
    service.wait_for_background()
    assert "Kavaliro at noon" not in caplog.text
    assert "Kavaliro at noon" not in service.latency_log.read_text(encoding="utf-8")


def test_debug_log_transcripts_logs_the_text(make_service, caplog):
    caplog.set_level(logging.INFO)
    service = make_service(privacy={"debug_log_transcripts": True})
    service.start()
    service.stop()
    assert SECRET in caplog.text


def test_silent_clip_skips_the_api(make_service):
    engine = FakeEngine()
    service = make_service(mic=FakeMic(np.zeros(2 * SR, dtype=np.float32)), engine=engine)
    service.start()
    result = service.stop()
    assert result.status == "skipped"
    assert "Nothing was sent" in result.message
    assert engine.calls == []
    record = latency_records(service)[-1]
    assert record["status"] == "skipped" and record["reason"] == "quiet"


def test_empty_transcript_is_skipped(make_service):
    service = make_service(engine=FakeEngine(text="  "))
    service.start()
    result = service.stop()
    assert result.status == "skipped"
    service.wait_for_background()
    assert not (service.config.notes_dir / "dictation").exists()


def test_cancel_while_recording(make_service):
    engine, mic = FakeEngine(), FakeMic()
    service = make_service(mic=mic, engine=engine)
    service.start()
    assert service.cancel() == "cancelled"
    assert mic.events[-1] == "cancel"
    assert service.stop().status == "error"
    assert engine.calls == []
    assert service.cancel() == "idle"


def test_cancel_while_transcribing_drops_the_result(make_service):
    gate = threading.Event()
    service = make_service(engine=FakeEngine(gate=gate))
    service.start()
    results = []
    worker = threading.Thread(target=lambda: results.append(service.stop()))
    worker.start()
    while service.state != "processing":
        time.sleep(0.005)
    with pytest.raises(ServiceBusy):
        service.start()
    assert service.cancel() == "cancelled"
    gate.set()
    worker.join(5)
    assert results[0].status == "cancelled" and results[0].text == ""
    service.wait_for_background()
    assert not (service.config.notes_dir / "dictation").exists()
    assert service.pasted(results[0].dictation_id, 1000, 10, True) is False


def test_start_while_recording_starts_over(make_service):
    mic = FakeMic()
    service = make_service(mic=mic)
    service.start()
    assert service.start()["id"] == 2
    assert mic.events == ["start", "cancel", "start"]


def test_paste_report_completes_the_latency_record(make_service):
    service = make_service()
    service.start()
    result = service.stop(mode="handsfree")
    assert service.pasted(result.dictation_id, 1380, 35, True)
    record = latency_records(service)[-1]
    assert record["status"] == "pasted"
    assert record["mode"] == "handsfree"
    assert record["model"] == "gpt-transcribe"  # so `gpw latency` can leave dry runs out
    assert record["release_to_paste"] == 1.38 and record["paste"] == 0.035
    assert record["clipboard_restored"] is True
    assert record["api"] == 0.42 and record["server"] == 0.31
    assert record["audio"] == pytest.approx(2.0)
    for stage in ("mic_open", "record_end", "prep", "encode", "service"):
        assert stage in record
    assert "text" not in record
    assert service.pasted(result.dictation_id, 1380, 35, True) is False  # only once


def test_daily_log_gets_every_dictation(make_service):
    service = make_service()
    for _ in range(2):
        service.start()
        service.stop()
    service.wait_for_background()
    text = (service.config.notes_dir / "dictation" / "2026-10-28.md").read_text(encoding="utf-8")
    assert text.count("type: dictation-log") == 1
    assert text.count(SECRET) == 2
    assert "## 09:15:00 (2 s)" in text


def test_daily_log_can_be_turned_off(make_service):
    service = make_service(dictation={"daily_log": False})
    service.start()
    service.stop()
    service.wait_for_background()
    assert not (service.config.notes_dir / "dictation").exists()


def test_audio_kept_unless_delete_after_success(make_service):
    keep = make_service()
    keep.start()
    keep.stop()
    keep.wait_for_background()
    saved = list(keep.config.audio_dir.glob("*.flac"))
    assert [p.name for p in saved] == ["dictation_2026-10-28_09-15-00_1.flac"]
    assert sf.info(str(saved[0])).duration == pytest.approx(2.0)


def test_delete_audio_after_success_keeps_nothing(tmp_path):
    config = make_config(tmp_path, privacy={"delete_audio_after_success": True})
    service = DictationService(config, FakeEngine(), FakeMic())
    service.start()
    service.stop()
    service.wait_for_background()
    service.close()
    assert not config.audio_dir.exists() or list(config.audio_dir.iterdir()) == []


def test_api_failure_is_reported_and_audio_kept_for_retry(tmp_path, caplog):
    class APIConnectionError(Exception):
        pass

    config = make_config(tmp_path, privacy={"delete_audio_after_success": True})
    service = DictationService(config, FakeEngine(error=APIConnectionError("boom")), FakeMic())
    service.start()
    result = service.stop()
    service.wait_for_background()
    service.close()
    assert result.status == "error"
    assert result.message == "Could not reach OpenAI. Check the internet connection."
    assert len(list(config.audio_dir.glob("*.flac"))) == 1
    record = json.loads(service.latency_log.read_text(encoding="utf-8").splitlines()[-1])
    assert record["status"] == "error" and record["error"] == "APIConnectionError"


def test_cleanup_runs_and_raw_transcript_is_kept(make_service):
    service = make_service(clean=lambda text: text.upper())
    service.start()
    result = service.stop()
    assert result.text == SECRET.upper()
    service.wait_for_background()
    text = (service.config.notes_dir / "dictation" / "2026-10-28.md").read_text(encoding="utf-8")
    assert f"**Raw transcript:** {SECRET}" in text


def test_cleanup_failure_falls_back_to_raw(make_service):
    def broken(text):
        raise RuntimeError("model down")

    service = make_service(clean=broken)
    service.start()
    assert service.stop().text == SECRET


def test_prewarm_runs_on_key_down_when_idle(tmp_path):
    engine = FakeEngine()
    config = make_config(tmp_path, dictation={"prewarm_connection": True})
    service = DictationService(config, engine, FakeMic())
    service.start()
    deadline = time.time() + 2
    while engine.prewarms == 0 and time.time() < deadline:
        time.sleep(0.01)
    assert engine.prewarms == 1
    service.stop()
    service.start()  # OpenAI was just used, so the connection is still warm
    time.sleep(0.05)
    assert engine.prewarms == 1
    service.close()


def test_mic_error_rescans_devices_and_retries_once(make_service):
    mic = FakeMic()
    mic.start_errors = [MicError("Invalid device")]
    service = make_service(mic=mic)
    assert service.start()["id"] == 1
    assert mic.events == ["start", "refresh", "start"]


def test_mic_timeout_is_not_retried(make_service):
    mic = FakeMic()
    mic.start_errors = [MicTimeout("did not open")]
    service = make_service(mic=mic)
    with pytest.raises(MicTimeout):
        service.start()
    assert mic.events == ["start"]
    assert service.state == "idle"


def test_unknown_mic_name_becomes_a_mic_error(make_service):
    mic = FakeMic()
    mic.start_errors = [ValueError("No microphone matches"), ValueError("No microphone matches")]
    service = make_service(mic=mic)
    with pytest.raises(MicError, match="No microphone matches"):
        service.start()


def test_watchdog_closes_a_forgotten_recording(make_service):
    mic = FakeMic()
    service = make_service(mic=mic)
    service.start()
    service._watchdog_fired(1)
    assert service.state == "idle"
    assert mic.events[-1] == "cancel"


def test_health_reports_settings_for_the_hotkey_script(make_service):
    service = make_service(hotkeys={"dictate": "F13", "tap_ms": 200}, dictation={"hands_free_max_minutes": 3, "sounds": False})
    health = service.health()
    assert health["service"] == "gpw" and health["state"] == "idle"
    assert health["dictate_key"] == "F13" and health["cancel_key"] == "Esc"
    assert health["tap_ms"] == 200 and health["double_tap_ms"] == 400
    assert health["max_seconds"] == 180 and health["sounds"] is False
    assert health["assets_dir"].endswith("data/assets")
    json.dumps(health)
