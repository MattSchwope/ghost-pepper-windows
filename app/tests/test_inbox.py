"""The long-recording inbox (Phase 4): routing, waiting for files to finish arriving, the pipeline
from file to note, failures and resume, the watcher inside the service, its endpoint and `gpw inbox`.
OpenAI and ffmpeg are faked; nothing leaves the machine."""

import http.client
import json
import os
import threading
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from gpw import cli, logging_setup
from gpw.config import INBOX_PROFILES, parse_config
from gpw.dictionary import DictionaryStore
from gpw.inbox import (
    DONE,
    FAILED,
    InboxManager,
    InboxResult,
    Ledger,
    RecordingProcessor,
    SettleTracker,
    cache_key,
    ensure_folders,
    ledger_for,
    listing,
    move_unique,
    plan_pieces,
    prep_for,
    prep_marker,
    profile_for,
    prune_work,
    recording_date,
    render_transcript,
    retry_file,
    skip_reason,
)
from gpw.server import make_server
from gpw.service import DictationService
from meeting_fakes import APIConnectionError, FakeSummaryModel
from test_service import FakeEngine, FakeMic

SR = 16_000
NOW = datetime(2026, 11, 19, 9, 0, 0)
RECORDED = datetime(2026, 11, 18, 12, 0, 0).timestamp()
SUMMARY = "Title: Zero trust roadmap\n\n## Overview\nThe agency plans a recompete.\n\n## Follow-ups\n- [ ] Call the program office"
# A 5-minute talk with pauses at 1:58 and 4:02; the fake ffmpeg reports them as the real one would.
LENGTH = 300.0
PAUSES = [(118.0, 119.0), (241.5, 243.5)]


def make_config(tmp_path, **sections):
    raw = {"app": {"data_dir": str(tmp_path / "data")}, "transcription": {"languages": ["en"]},
           "dictation": {"prewarm_connection": False}, "inbox": {"parallel_requests": 1}}
    for name, values in sections.items():
        raw.setdefault(name, {}).update(values)
    return parse_config(raw)


def dictionary(tmp_path):
    path = tmp_path / "dictionary.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('terms: [Kavaliro]\nreplace:\n  "cavaliro": "Kavaliro"\n', encoding="utf-8")
    return DictionaryStore(path)


def talk(seconds=LENGTH, pauses=PAUSES, amp=0.2):
    t = np.arange(int(seconds * SR)) / SR
    audio = (amp * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    for a, b in pauses:
        audio[int(a * SR):int(b * SR)] = 0
    return audio


def drop(folder: Path, name: str, audio=None, mtime: float = RECORDED) -> Path:
    """A recording as a WAV file (the fake ffmpeg reads it whatever its name says)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    sf.write(str(path), talk() if audio is None else audio, SR, format="WAV", subtype="PCM_16")
    os.utime(path, (mtime, mtime))
    return path


class FakeFfmpeg:
    def __init__(self, pauses=PAUSES):
        self.pauses = pauses
        self.cleaned: list[tuple[str, str]] = []
        self.silence_calls = 0

    def clean(self, src, dst, level):
        self.cleaned.append((src.name, level))
        data, rate = sf.read(str(src), dtype="float32")
        sf.write(str(dst), data, rate, format="WAV", subtype="PCM_16")

    def silences(self, src):
        self.silence_calls += 1
        return list(self.pauses)


def words_for(seconds: float) -> str:
    return f"We discussed the cavaliro program for {round(seconds)} seconds. It went well."


class PartEngine:
    """Says how long each part was, so the transcript shows which parts were sent."""

    model = "gpt-transcribe"

    def __init__(self, fail_on=None, text=None, gate=None):
        self.calls: list[dict] = []
        self.fail_on, self.text, self.gate = fail_on, text, gate
        self._lock = threading.Lock()

    def transcribe(self, path, prompt, keywords, languages):
        seconds = sf.info(str(path)).duration
        with self._lock:
            n = len(self.calls)
            self.calls.append({"seconds": seconds, "prompt": prompt, "keywords": keywords, "languages": languages})
        if self.gate is not None:
            self.gate.wait(10)
        if self.fail_on is not None and n == self.fail_on:
            raise APIConnectionError("network down")
        return words_for(seconds) if self.text is None else self.text


def processor(tmp_path, engine=None, model=None, ffmpeg=None, **sections):
    config = make_config(tmp_path, **sections)
    proc = RecordingProcessor(
        config, engine or PartEngine(), model or FakeSummaryModel(SUMMARY), dictionary(tmp_path),
        ffmpeg=ffmpeg or FakeFfmpeg(), now=lambda: NOW,
    )
    return proc, config


# -- which files, which profile --

def test_the_folder_picks_the_profile(tmp_path):
    inbox = tmp_path / "inbox"
    assert profile_for(inbox, inbox / "talk.m4a") == "meeting"  # the inbox itself
    for profile in INBOX_PROFILES:
        assert profile_for(inbox, inbox / profile / "talk.m4a") == profile
    assert profile_for(inbox, inbox / "BD-Call" / "call.m4a") == "bd-call"  # Windows folder names ignore case
    assert profile_for(inbox, inbox / DONE / "talk.m4a") is None
    assert profile_for(inbox, inbox / FAILED / "talk.m4a") is None
    assert profile_for(inbox, inbox / "other" / "talk.m4a") is None
    assert profile_for(inbox, inbox / "presentation" / "nested" / "talk.m4a") is None
    assert set(INBOX_PROFILES) == {"meeting", "presentation", "bd-call", "candidate-screen", "client-intake", "hm-debrief"}


def test_every_profile_has_a_prompt_with_a_title_line():
    from gpw.config import PROMPT_DIR

    for prompt in INBOX_PROFILES.values():
        text = (PROMPT_DIR / f"{prompt}.md").read_text(encoding="utf-8")
        assert "Title: <" in text and "Do not invent" in text, prompt


@pytest.mark.parametrize("name, reason", [
    ("talk.m4a", None), ("Call 2026-11-18.MP3", None), ("screen.mp4", None), ("memo.wav", None),
    ("~$talk.m4a", "temporary or hidden file"), ("~talk.m4a", "temporary or hidden file"),
    (".talk.m4a", "temporary or hidden file"),
    ("talk.m4a.tmp", "partial download"), ("talk.m4a.partial", "partial download"), ("talk.part", "partial download"),
    ("talk.m4a.crdownload", "partial download"), ("talk.partial.m4a", "partial download"),
    ("notes.txt", "not an audio or video file"), ("desktop.ini", "not an audio or video file"),
])
def test_temp_partial_and_other_files_are_left_alone(name, reason):
    assert skip_reason(Path(name)) == reason


def test_audio_prep_comes_from_the_name_then_the_profile(tmp_path):
    config = make_config(tmp_path, inbox={"audio_prep": "light", "profile_prep": {"presentation": "strong"}})
    assert prep_marker("talk.strong.m4a") == "strong"
    assert prep_marker("talk [Strong].m4a") == "strong"
    assert prep_marker("talk.off.wav") == "off"
    assert prep_marker("strongman talk.m4a") is None
    assert prep_for(config.inbox, "meeting", "call.m4a") == "light"
    assert prep_for(config.inbox, "presentation", "talk.m4a") == "strong"
    assert prep_for(config.inbox, "presentation", "talk.light.m4a") == "light"
    assert prep_for(config.inbox, "bd-call", "call [strong].m4a") == "strong"


def test_config_for_the_inbox(tmp_path):
    config = make_config(tmp_path)
    assert config.inbox_dir == tmp_path / "data" / "inbox"
    assert (config.inbox.enabled, config.inbox.audio_prep, config.inbox.settle_seconds) == (True, "light", 10.0)
    moved = parse_config({"app": {"data_dir": str(tmp_path)}, "files": {"inbox": "D:/OneDrive/Recordings"}})
    assert moved.inbox_dir == Path("D:/OneDrive/Recordings")
    with pytest.raises(ValueError, match="not an inbox profile"):
        parse_config({"inbox": {"profile_prep": {"podcast": "strong"}}})
    with pytest.raises(ValueError, match="inbox.profile_prep.presentation"):
        parse_config({"inbox": {"profile_prep": {"presentation": "max"}}})
    with pytest.raises(ValueError, match="settle_seconds"):
        parse_config({"inbox": {"settle_seconds": 0}})
    assert parse_config({"inbox": {"audio_prep": False}}).inbox.audio_prep == "off"  # YAML reads a bare off as false


def test_recording_date_from_the_name_or_the_file_time():
    mtime = datetime(2026, 11, 2, 8, 0).timestamp()
    today = date(2026, 11, 19)
    assert recording_date("Call 2026-11-18.m4a", mtime, today) == date(2026, 11, 18)
    assert recording_date("20261117_140501.m4a", mtime, today) == date(2026, 11, 17)
    assert recording_date("New Recording 12.m4a", mtime, today) == date(2026, 11, 2)
    assert recording_date("plan 2027-01-01.m4a", mtime, today) == date(2026, 11, 2)  # not in the future
    assert recording_date("2026-13-40 odd.m4a", mtime, today) == date(2026, 11, 2)


# -- waiting for a file to finish arriving --

def test_a_file_is_ready_once_its_size_stops_changing():
    tracker = SettleTracker(10.0)
    assert not tracker.update("a", 100, 1, now=0.0)  # first sight
    assert not tracker.update("a", 100, 1, now=9.0)
    assert not tracker.update("a", 250, 2, now=9.5)  # still growing (OneDrive syncing): the wait starts over
    assert not tracker.update("a", 250, 2, now=19.0)
    assert tracker.update("a", 250, 2, now=19.5)
    assert not tracker.update("empty", 0, 1, now=0.0) and not tracker.update("empty", 0, 1, now=60.0)
    assert tracker.waiting == 2
    tracker.keep_only({"a"})
    assert tracker.waiting == 1


def test_the_scanner_queues_a_settled_file_once(tmp_path):
    config = make_config(tmp_path, inbox={"settle_seconds": 10})
    ensure_folders(config.inbox_dir)
    clock = [100.0]
    writing = [True]
    m = InboxManager(config, None, clock=lambda: clock[0], writer_done=lambda path: not writing[0], watch=False)
    path = drop(config.inbox_dir / "bd-call", "call.m4a", talk(5, []))
    (config.inbox_dir / "bd-call" / "call.m4a.tmp").write_bytes(b"x" * 10)
    (config.inbox_dir / "bd-call" / "~$call.m4a").write_bytes(b"x" * 10)
    (config.inbox_dir / DONE / "old.m4a").write_bytes(b"x" * 10)

    assert m.scan_once() == 0
    clock[0] += 5
    with open(path, "ab") as f:  # more of it arrived
        f.write(b"\0" * 64)
    assert m.scan_once() == 0
    clock[0] += 10
    assert m.scan_once() == 0  # settled, but still open for writing
    writing[0] = False
    assert m.scan_once() == 1
    job = m._queue.get_nowait()
    assert (job.path.name, job.profile) == ("call.m4a", "bd-call")
    clock[0] += 60
    assert m.scan_once() == 0  # already queued: never twice
    assert m.status()["queued"] == 1


def test_a_file_open_for_writing_is_not_ready(tmp_path):
    from gpw.inbox import is_cloud_only, writer_done

    path = tmp_path / "copying.m4a"
    with open(path, "wb") as f:
        f.write(b"\0" * 1024)
        f.flush()
        if os.name == "nt":
            assert not writer_done(path)  # a copy or a sync still has it open
    assert writer_done(path)
    assert not is_cloud_only(path)
    assert not writer_done(tmp_path / "missing.m4a") or os.name != "nt"


# -- one file to one note --

def test_a_recording_becomes_a_note_and_moves_to_done(tmp_path):
    engine, ffmpeg = PartEngine(), FakeFfmpeg()
    proc, config = processor(tmp_path, engine, ffmpeg=ffmpeg)
    src = drop(config.inbox_dir / "presentation", "talk1.wav")
    steps = []
    result = proc.process(src, "presentation", steps.append)

    assert result.status == "done", result.message
    assert result.note == config.notes_dir / "presentation" / "2026-11-18 Zero trust roadmap.md"
    assert result.source == config.inbox_dir / DONE / "talk1.wav" and result.source.is_file() and not src.exists()
    parts = [118.5, 124.0, 57.5]  # cut in the pauses, nothing dropped
    assert [round(c["seconds"], 1) for c in engine.calls] == parts
    fixed = [words_for(s).replace("cavaliro", "Kavaliro") for s in parts]  # dictionary fix; no light cleanup
    assert result.note.read_text(encoding="utf-8") == (
        "---\ntype: recording\nprofile: presentation\ndate: 2026-11-18\nduration: \"00:05:00\"\nsource: talk1.wav\n"
        "engine: gpt-transcribe\naudio_prep: light\nsummary_model: gpt-6.1-sol\n---\n"
        "# Zero trust roadmap\n\n## Summary\n\n### Overview\nThe agency plans a recompete.\n\n"
        "### Follow-ups\n- [ ] Call the program office\n\n## Transcript\n\n"
        f"[00:00:00] {fixed[0]}\n\n[00:01:58] {fixed[1]}\n\n[00:04:02] {fixed[2]}\n"
    )
    # GPT-Transcribe got the dictionary keywords and the profile as context; the summary got the profile's prompt.
    assert all(c["keywords"] == ["Kavaliro"] and c["languages"] == ["en"] for c in engine.calls)
    assert engine.calls[0]["prompt"] == "A recorded presentation."
    instructions, text = proc.text_model.inputs[0]
    assert instructions.startswith("You summarize the transcript of a recorded presentation")
    assert text.startswith("Recording: talk1.wav\nKind: presentation\nDate: 2026-11-18\nLength: 00:05:00\n\n[00:00:00]")
    assert ffmpeg.cleaned == [("talk1.wav", "light")]
    assert not [p for p in (config.work_dir / "inbox").iterdir() if p.is_dir()]  # cached parts are gone once the note is written
    entry = ledger_for(config).last_for("_done/talk1.wav")
    assert (entry["status"], entry["profile"], entry["note"]) == ("done", "presentation", str(result.note))
    assert steps[0] == "preparing the audio" and steps[-1] == "writing the summary"


def test_a_file_dropped_in_the_inbox_itself_is_a_meeting(tmp_path):
    proc, config = processor(tmp_path, model=FakeSummaryModel("Title: Weekly sync\n\n### Overview\nShort."))
    src = drop(config.inbox_dir, "sync.m4a", talk(20, []))
    result = proc.process(src, profile_for(config.inbox_dir, src))
    assert result.note == config.notes_dir / "meeting" / "2026-11-18 Weekly sync.md"
    assert "profile: meeting\n" in result.note.read_text(encoding="utf-8")
    assert proc.text_model.inputs[0][0].startswith("You summarize the transcript of a recorded meeting")


def test_a_summary_without_a_title_is_named_after_the_file(tmp_path):
    proc, config = processor(tmp_path, model=FakeSummaryModel("### Account summary\nAcme is hiring."))
    result = proc.process(drop(config.inbox_dir / "bd-call", "Acme_call.strong.m4a", talk(10, [])), "bd-call")
    assert result.note.name == "2026-11-18 Acme call.md"
    assert "# Acme call\n" in result.note.read_text(encoding="utf-8")


def test_strong_prep_per_profile_or_per_file(tmp_path):
    ffmpeg = FakeFfmpeg()
    proc, config = processor(tmp_path, ffmpeg=ffmpeg, inbox={"profile_prep": {"presentation": "strong"}})
    a = proc.process(drop(config.inbox_dir / "presentation", "keynote.m4a", talk(10, [])), "presentation")
    b = proc.process(drop(config.inbox_dir / "bd-call", "call [strong].m4a", talk(10, [])), "bd-call")
    c = proc.process(drop(config.inbox_dir / "bd-call", "call2.m4a", talk(10, [])), "bd-call")
    assert ffmpeg.cleaned == [("keynote.m4a", "strong"), ("call [strong].m4a", "strong"), ("call2.m4a", "light")]
    assert "audio_prep: strong\n" in a.note.read_text(encoding="utf-8")
    assert "audio_prep: light\n" in c.note.read_text(encoding="utf-8")
    assert b.status == "done"


def test_a_short_file_is_one_part_and_parts_with_no_sound_are_skipped(tmp_path):
    engine, ffmpeg = PartEngine(), FakeFfmpeg(pauses=[])
    proc, config = processor(tmp_path, engine, ffmpeg=ffmpeg)
    audio = talk(400, [])
    audio[120 * SR:240 * SR] = 0  # two minutes of nothing at all
    result = proc.process(drop(config.inbox_dir, "long.m4a", audio), "meeting")
    assert result.status == "done"
    assert [round(c["seconds"]) for c in engine.calls] == [120, 120, 40]  # the silent part was not sent
    assert "[00:02:00]" not in result.note.read_text(encoding="utf-8")

    engine2, ffmpeg2 = PartEngine(), FakeFfmpeg()
    proc2, config2 = processor(tmp_path / "b", engine2, ffmpeg=ffmpeg2)
    proc2.process(drop(config2.inbox_dir, "short.m4a", talk(30, [])), "meeting")
    assert len(engine2.calls) == 1 and ffmpeg2.silence_calls == 0  # no pause search for one part


def test_pieces_cover_the_whole_recording():
    assert plan_pieces(30.0, []) == [(0.0, 30.0)]
    pieces = plan_pieces(LENGTH, PAUSES)
    assert pieces == [(0.0, 118.5), (118.5, 242.5), (242.5, 300.0)]
    assert plan_pieces(0.0, []) == []
    no_pauses = plan_pieces(1000.0, [])
    assert no_pauses[0][0] == 0.0 and no_pauses[-1][1] == 1000.0
    assert all(a[1] == b[0] for a, b in zip(no_pauses, no_pauses[1:]))
    assert max(b - a for a, b in no_pauses) <= 138.0


def test_transcript_rendering():
    long = " ".join(f"Sentence {i}." for i in range(7))
    assert render_transcript([(0.0, "Hello there."), (118.5, long)]) == (
        "[00:00:00] Hello there.\n\n"
        "[00:01:58] Sentence 0. Sentence 1. Sentence 2. Sentence 3. Sentence 4.\n\nSentence 5. Sentence 6.\n"
    )
    assert render_transcript([]) == ""


# -- failures and resume --

def test_a_failure_moves_the_file_to_failed_and_a_redrop_resumes_from_cache(tmp_path, caplog):
    ffmpeg = FakeFfmpeg()
    failing = PartEngine(fail_on=1)
    # Batches of about 2 minutes: parts 1 and 2 go together; the second fails, so part 3 is never sent.
    proc, config = processor(tmp_path, failing, ffmpeg=ffmpeg, transcription={"chunk_minutes": 2})
    src = drop(config.inbox_dir / "presentation", "talk1.wav")
    with caplog.at_level("INFO", logger="gpw"):
        result = proc.process(src, "presentation")

    assert result.status == "failed" and result.stage == "transcription"
    assert result.message == "Transcription failed: Could not reach OpenAI."
    assert result.source == config.inbox_dir / FAILED / "talk1.wav" and result.source.is_file()
    assert not list(config.notes_dir.glob("**/*.md"))
    assert "Inbox talk1.wav: transcription failed (APIConnectionError: Could not reach OpenAI)" in caplog.text
    assert "cavaliro" not in caplog.text.lower()  # no transcript text in the log
    entry = ledger_for(config).last_for("_failed/talk1.wav")
    assert (entry["status"], entry["stage"], entry["error"], entry["profile"]) == (
        "failed", "transcription", "Could not reach OpenAI.", "presentation")

    # `gpw inbox retry` (or dragging it back) puts it in its profile folder again.
    path, profile = retry_file(config, "talk1.wav")
    assert (path, profile) == (config.inbox_dir / "presentation" / "talk1.wav", "presentation")
    engine = PartEngine()
    proc2 = RecordingProcessor(config, engine, FakeSummaryModel(SUMMARY), dictionary(tmp_path), ffmpeg=ffmpeg, now=lambda: NOW)
    result = proc2.process(path, "presentation")
    assert result.status == "done"
    assert len(failing.calls) == 2 and len(engine.calls) == 2  # the first part came from the cache
    assert ffmpeg.cleaned == [("talk1.wav", "light")] and ffmpeg.silence_calls == 1  # prep and cut plan reused too
    assert "[00:00:00] We discussed the Kavaliro program for 118 seconds" in result.note.read_text(encoding="utf-8")


def test_a_renamed_redrop_still_finds_its_cached_parts(tmp_path):
    ffmpeg = FakeFfmpeg()
    proc, config = processor(tmp_path, PartEngine(fail_on=1), ffmpeg=ffmpeg, transcription={"chunk_minutes": 2})
    drop(config.inbox_dir / FAILED, "talk1.wav", talk(10, []))  # a different file already has the name
    result = proc.process(drop(config.inbox_dir / "presentation", "talk1.wav"), "presentation")
    assert result.source.name == "talk1 (2).wav"
    redropped = move_unique(result.source, config.inbox_dir / "presentation")
    engine = PartEngine()
    proc.engine = engine
    assert proc.process(redropped, "presentation").status == "done"
    assert len(engine.calls) == 2 and len(ffmpeg.cleaned) == 1


def test_a_summary_failure_retries_without_transcribing_again(tmp_path):
    engine = PartEngine()
    proc, config = processor(tmp_path, engine, model=FakeSummaryModel(error=APIConnectionError("down")))
    result = proc.process(drop(config.inbox_dir / "hm-debrief", "debrief.m4a", talk(30, [])), "hm-debrief")
    assert (result.status, result.stage) == ("failed", "summary")
    path, profile = retry_file(config, "debrief.m4a")
    assert profile == "hm-debrief" and path.parent.name == "hm-debrief"
    proc.text_model = FakeSummaryModel(SUMMARY)
    assert proc.process(path, profile).status == "done"
    assert len(engine.calls) == 1


@pytest.mark.parametrize("audio, text, message", [
    (np.zeros(SR * 20, dtype=np.float32), None, "The file is silent: no sound was found in it."),
    (talk(20, []), "", "OpenAI heard no words in the file."),
])
def test_a_file_with_nothing_to_hear_fails_with_a_plain_reason(tmp_path, audio, text, message):
    proc, config = processor(tmp_path, PartEngine(text=text))
    result = proc.process(drop(config.inbox_dir, "blank.m4a", audio), "meeting")
    assert result.status == "failed" and result.message.endswith(message)
    assert (config.inbox_dir / FAILED / "blank.m4a").is_file()


def test_retry_can_change_the_profile_and_refuses_unknown_files(tmp_path):
    config = make_config(tmp_path)
    ensure_folders(config.inbox_dir)
    drop(config.inbox_dir / FAILED, "call.m4a", talk(2, []))
    with pytest.raises(FileNotFoundError, match="gpw inbox status"):
        retry_file(config, "nope.m4a")
    with pytest.raises(ValueError, match="not an inbox profile"):
        retry_file(config, "call.m4a", "podcast")
    path, profile = retry_file(config, "call.m4a", "client-intake")
    assert path == config.inbox_dir / "client-intake" / "call.m4a" and profile == "client-intake"
    drop(config.inbox_dir / FAILED, "memo.m4a", talk(2, []))
    assert retry_file(config, "memo.m4a")[0] == config.inbox_dir / "memo.m4a"  # no ledger entry: meeting


def test_cache_key_ignores_the_name_and_old_work_is_pruned(tmp_path):
    assert cache_key(Path("a.M4A"), 10, 5, "m", "light") == cache_key(Path("b (2).m4a"), 10, 5, "m", "light")
    assert cache_key(Path("a.m4a"), 10, 5, "m", "light") != cache_key(Path("a.m4a"), 10, 6, "m", "light")
    assert cache_key(Path("a.m4a"), 10, 5, "m", "light") != cache_key(Path("a.m4a"), 10, 5, "m", "strong")
    root = tmp_path / "work"
    (root / "old").mkdir(parents=True)
    (root / "new").mkdir()
    old = time.time() - 20 * 86400
    os.utime(root / "old", (old, old))
    assert prune_work(root, 14) == 1 and [p.name for p in root.iterdir()] == ["new"]


def test_the_ledger_keeps_the_newest_entries(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json")
    for i in range(205):
        ledger.add(file=f"_done/{i}.m4a", status="done")
    entries = ledger.entries()
    assert len(entries) == 200 and entries[-1]["file"] == "_done/204.m4a"
    assert ledger.last_for("_DONE\\204.m4a")["status"] == "done"
    assert ledger.last_for("_done/0.m4a") is None


# -- the watcher inside the service --

class StubProcessor:
    def __init__(self, root: Path, gate=None, fail=False, move=True):
        self.root, self.gate, self.fail, self.move = root, gate, fail, move
        self.calls: list[tuple[str, str]] = []

    def process(self, path, profile, progress=None):
        self.calls.append((path.name, profile))
        if progress:
            progress("transcribing, batch 1 of 2")
        if self.gate is not None:
            self.gate.wait(10)
        if self.fail:
            dest = move_unique(path, self.root / FAILED) if self.move else path
            return InboxResult("failed", dest, profile, message="Transcription failed: Could not reach OpenAI.")
        dest = move_unique(path, self.root / DONE)
        return InboxResult("done", dest, profile, note=self.root.parent / "notes" / profile / "x.md")


def wait_for(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def managers():
    made = []
    yield made
    for m in made:
        m.close()


def test_files_already_in_the_inbox_are_picked_up_at_start(tmp_path, managers):
    config = make_config(tmp_path, inbox={"settle_seconds": 0.05})
    ensure_folders(config.inbox_dir)
    drop(config.inbox_dir, "a.m4a", talk(1, []))
    drop(config.inbox_dir / "candidate-screen", "b.m4a", talk(1, []))
    stub = StubProcessor(config.inbox_dir)
    m = InboxManager(config, lambda: stub, watch=False)
    managers.append(m)
    m.open()
    assert wait_for(lambda: len(stub.calls) == 2) and m.wait_idle()
    assert sorted(stub.calls) == [("a.m4a", "meeting"), ("b.m4a", "candidate-screen")]
    assert sorted(p.name for p in (config.inbox_dir / DONE).iterdir()) == ["a.m4a", "b.m4a"]
    status = m.status()
    assert (status["done_seq"], status["failed_seq"], status["processing"]) == (2, 0, "")


def test_open_creates_the_folders_and_can_be_turned_off(tmp_path, managers):
    config = make_config(tmp_path, inbox={"enabled": False})
    m = InboxManager(config, None, watch=False)
    managers.append(m)
    m.open()
    assert not config.inbox_dir.exists()
    config = make_config(tmp_path / "on")
    m = InboxManager(config, None, watch=False)
    managers.append(m)
    m.open()
    assert sorted(p.name for p in config.inbox_dir.iterdir()) == sorted([*INBOX_PROFILES, DONE, FAILED])


def test_a_dropped_file_wakes_the_watcher(tmp_path, managers):
    pytest.importorskip("watchdog")
    config = make_config(tmp_path, inbox={"settle_seconds": 0.05})
    stub = StubProcessor(config.inbox_dir)
    m = InboxManager(config, lambda: stub)
    managers.append(m)
    m.open()
    time.sleep(0.3)  # the first scan found nothing; the next rescan is 30 s away
    drop(config.inbox_dir / "presentation", "talk.m4a", talk(1, []))
    assert wait_for(lambda: stub.calls == [("talk.m4a", "presentation")], timeout=8)


def test_a_failed_file_that_could_not_be_moved_is_not_retried_until_it_changes(tmp_path, managers, monkeypatch):
    import gpw.inbox as inbox_module

    monkeypatch.setattr(inbox_module, "POLL_IDLE", 0.05)
    monkeypatch.setattr(inbox_module, "POLL_BUSY", 0.05)
    config = make_config(tmp_path, inbox={"settle_seconds": 0.05})
    ensure_folders(config.inbox_dir)
    path = drop(config.inbox_dir, "locked.m4a", talk(1, []))
    stub = StubProcessor(config.inbox_dir, fail=True, move=False)
    m = InboxManager(config, lambda: stub, watch=False)
    managers.append(m)
    m.open()
    assert wait_for(lambda: m.status()["failed_seq"] == 1)
    time.sleep(0.5)
    assert len(stub.calls) == 1
    with open(path, "ab") as f:
        f.write(b"\0" * 32)
    assert wait_for(lambda: len(stub.calls) == 2)
    status = m.status()
    assert status["failed_file"] == "locked.m4a" and status["failed_message"].endswith("Could not reach OpenAI.")


def test_dictation_keeps_working_while_a_recording_is_processed(tmp_path, managers):
    gate = threading.Event()
    config = make_config(tmp_path, inbox={"settle_seconds": 0.05})
    ensure_folders(config.inbox_dir)
    engine = PartEngine(gate=gate)  # the inbox's OpenAI call hangs until the end of the test
    proc = RecordingProcessor(config, engine, FakeSummaryModel(SUMMARY), ffmpeg=FakeFfmpeg(), now=lambda: NOW)
    drop(config.inbox_dir / "bd-call", "call.m4a", talk(10, []))
    m = InboxManager(config, lambda: proc, watch=False)
    managers.append(m)
    m.open()
    assert wait_for(lambda: engine.calls)
    service = DictationService(config, FakeEngine(), FakeMic())
    try:
        for _ in range(3):
            started = time.perf_counter()
            service.start()
            result = service.stop()
            assert result.status == "ok" and time.perf_counter() - started < 1.0
        status = m.status()
        assert (status["processing"], status["profile"]) == ("call.m4a", "bd-call")
        assert status["step"].startswith("transcribing")
    finally:
        gate.set()
        service.close()
    assert m.wait_idle() and m.status()["done_seq"] == 1


def test_inbox_status_over_http(tmp_path):
    config = make_config(tmp_path)
    service = DictationService(config, FakeEngine(), FakeMic())
    server = make_server(service, 0)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def post(path):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        conn.request("POST", path, headers={"X-GPW-Client": "test"})
        response = conn.getresponse()
        return response.status, json.loads(response.read() or b"{}")

    try:
        assert post("/inbox/status")[0] == 404
        service.inbox = InboxManager(config, None, watch=False)
        code, body = post("/inbox/status")
        assert code == 200 and body["ok"] and body["folder"] == config.inbox_dir.as_posix()
        assert body["failed_folder"] == (config.inbox_dir / FAILED).as_posix()
        assert (body["processing"], body["done_seq"], body["failed_seq"]) == ("", 0, 0)
    finally:
        server.shutdown()
        server.server_close()
        service.close()


def test_dry_run_processing_needs_no_network(tmp_path):
    config = make_config(tmp_path)
    proc = cli._inbox_processor(config, dry_run=True)
    proc._ffmpeg = FakeFfmpeg()
    result = proc.process(drop(config.inbox_dir / "presentation", "talk.m4a", talk(15, [])), "presentation")
    assert result.status == "done" and "Dry run: heard" in result.note.read_text(encoding="utf-8")


def test_real_ffmpeg_output_is_read_correctly(tmp_path):
    """The one test that runs the bundled ffmpeg, locally: its WAV must be what the pipeline expects."""
    pytest.importorskip("imageio_ffmpeg")
    from gpw.inbox import BundledFfmpeg
    from gpw.meeting_capture import TrackFile

    src = tmp_path / "stereo.wav"
    t = np.arange(3 * 44_100) / 44_100
    tone = 0.3 * np.sin(2 * np.pi * 440 * t)
    sf.write(str(src), np.stack([tone, tone], axis=1), 44_100)
    dst = tmp_path / "clean.wav"
    BundledFfmpeg().clean(src, dst, "light")
    track = TrackFile(dst)
    assert track.sample_rate == 16_000 and abs(track.seconds - 3.0) < 0.05
    assert float(np.abs(track.read_seconds(1.0, 2.0)).max()) > 0.05


# -- gpw inbox --

@pytest.fixture
def cli_config(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_setup, "configure_logging", lambda logs_dir: None)
    data = tmp_path / "data"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f'app:\n  data_dir: "{data.as_posix()}"\ninbox:\n  profile_prep:\n    presentation: strong\n', encoding="utf-8")
    return cfg, data / "inbox"


def run_cli(*args):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(list(args))
    return exit_info.value.code


def test_gpw_inbox_status_and_retry(cli_config, capsys):
    cfg, root = cli_config
    assert run_cli("--config", str(cfg), "inbox", "status") == 0
    out = capsys.readouterr().out
    assert f"Inbox: {root}" in out and "Nothing waiting." in out and "No failed files." in out
    assert "presentation" in out and "audio prep: strong" in out
    assert (root / "hm-debrief").is_dir() and (root / FAILED).is_dir()

    config = parse_config({"app": {"data_dir": str(root.parent)}})
    drop(root / "bd-call", "waiting.m4a", talk(1, []))
    drop(root / FAILED, "call.m4a", talk(1, []))
    ledger_for(config).add(file="_failed/call.m4a", source="call.m4a", profile="bd-call", status="failed",
                           stage="summary", error="Could not reach OpenAI.")
    ledger_for(config).add(file="_done/talk.m4a", source="talk.m4a", profile="presentation", status="done",
                           note="C:/notes/presentation/2026-11-18 Zero trust roadmap.md")
    assert run_cli("--config", str(cfg), "inbox", "status") == 0
    out = capsys.readouterr().out
    assert "  bd-call           waiting.m4a" in out
    assert 'call.m4a  [bd-call] summary failed: Could not reach OpenAI. Run: gpw inbox retry "call.m4a"' in out
    assert "[presentation] C:/notes/presentation/2026-11-18 Zero trust roadmap.md" in out

    assert run_cli("--config", str(cfg), "inbox", "retry") == 1
    assert run_cli("--config", str(cfg), "inbox", "retry", "call.m4a") == 0
    assert "Moved call.m4a back to" in capsys.readouterr().out
    assert (root / "bd-call" / "call.m4a").is_file() and not (root / FAILED / "call.m4a").exists()
    assert run_cli("--config", str(cfg), "inbox", "retry", "call.m4a") == 1  # not in _failed any more

    drop(root / FAILED, "x.m4a", talk(1, []))
    drop(root / FAILED, "y.m4a", talk(1, []))
    assert run_cli("--config", str(cfg), "inbox", "retry", "--all", "--profile", "presentation") == 0
    assert sorted(p.name for p in (root / "presentation").iterdir()) == ["x.m4a", "y.m4a"]
    assert [n for n, _ in listing(config).failed] == []
