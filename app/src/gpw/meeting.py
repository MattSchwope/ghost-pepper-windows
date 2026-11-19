"""Meeting capture from start to note.

    files.meetings/<YYYY-MM-DD HHMM>/
        capture.json   status, times, the app in front at start; never any transcript text
        them.wav       the call (speaker loopback), written while it records
        me.wav         your mic, unless meetings.capture_mic is false
        work/          finished turns while processing, so a retry pays only for what is missing

Statuses: recording -> stopped -> processing -> done | empty | failed. A capture the service
never finished reads as "interrupted" (recording, but its process is gone); one whose processing
was cut off reads as "stopped" and is picked up again when the service starts.

MeetingManager is what the service drives from the hotkey and tray. MeetingProcessor turns a
folder into a note and is also what `gpw meeting retry` runs.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import AppConfig
from .dictionary import DictionaryStore, apply_replacements
from .engines import estimate_cost
from .longform import ChunkCache, Engine, fmt_ts
from .meeting_capture import TRACKS, Backend, CaptureError, MeetingCapture, TrackFile, com_thread_init
from .meeting_transcript import (
    SPEAKERS,
    build_turns,
    detect_speech,
    drop_echo,
    merge_lines,
    render_lines,
    to_lines,
    transcribe_turns,
)
from .notes import file_title, render_note, write_note
from .summary import TextModel, load_profile, summarize

logger = logging.getLogger(__name__)

META = "capture.json"
PROFILE = "meeting_capture"
NOTES_SUBFOLDER = "meetings"

APP_LABELS = {
    "ms-teams.exe": "Teams", "teams.exe": "Teams", "msteams.exe": "Teams",
    "zoom.exe": "Zoom", "zoomus.exe": "Zoom",
    "webexmta.exe": "Webex", "ciscocollabhost.exe": "Webex", "atmgr.exe": "Webex",
    "slack.exe": "Slack", "discord.exe": "Discord",
    "chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox", "brave.exe": "Brave",
    "opera.exe": "Opera", "arc.exe": "Arc", "vivaldi.exe": "Vivaldi",
}


class MeetingBusy(RuntimeError):
    pass


class NotCapturing(RuntimeError):
    pass


def app_label(exe: str) -> str:
    """The program in front at start as a short name: "ms-teams.exe" -> "Teams"."""
    exe = exe.strip()
    if not exe:
        return ""
    known = APP_LABELS.get(exe.lower())
    if known:
        return known
    stem = exe[:-4] if exe.lower().endswith(".exe") else exe
    return stem[:40]


# -- folders and capture.json --

def new_folder(root: Path, when: datetime) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    base = f"{when:%Y-%m-%d %H%M}"
    folder, n = root / base, 2
    while folder.exists():
        folder, n = root / f"{base} ({n})", n + 1
    folder.mkdir()
    return folder


def read_meta(folder: Path) -> dict[str, Any]:
    try:
        return json.loads((folder / META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_meta(folder: Path, meta: dict[str, Any]) -> None:
    meta["updated"] = datetime.now().isoformat(timespec="seconds")
    tmp = folder / (META + ".tmp")
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    tmp.replace(folder / META)


def update_meta(folder: Path, **changes: Any) -> dict[str, Any]:
    meta = read_meta(folder)
    meta.update(changes)
    write_meta(folder, meta)
    return meta


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def effective_status(meta: dict[str, Any], alive: Callable[[int], bool] = pid_alive) -> str:
    status = str(meta.get("status") or "unknown")
    owner = int(meta.get("pid") or 0)
    if status == "recording" and not alive(owner):
        return "interrupted"
    if status == "processing" and not alive(owner):
        return "stopped"
    return status


@dataclass(frozen=True)
class MeetingInfo:
    folder: Path
    status: str
    meta: dict[str, Any]

    @property
    def name(self) -> str:
        return self.folder.name

    @property
    def seconds(self) -> float:
        if self.meta.get("seconds"):
            return float(self.meta["seconds"])
        lengths = []
        for track in TRACKS:
            path = self.folder / f"{track}.wav"
            if path.is_file():
                try:
                    lengths.append(TrackFile(path).seconds)
                except (OSError, ValueError):
                    pass
        return max(lengths, default=0.0)

    @property
    def has_audio(self) -> bool:
        return any((self.folder / f"{t}.wav").is_file() for t in TRACKS)


def list_meetings(root: Path, alive: Callable[[int], bool] = pid_alive) -> list[MeetingInfo]:
    """Every capture folder, newest first."""
    if not root.is_dir():
        return []
    out = []
    for folder in root.iterdir():
        if folder.is_dir() and (folder / META).is_file():
            meta = read_meta(folder)
            out.append(MeetingInfo(folder, effective_status(meta, alive), meta))
    return sorted(out, key=lambda m: (str(m.meta.get("started", "")), m.name), reverse=True)


def find_meeting(root: Path, name: str) -> Path:
    """A capture folder from its name ("2026-11-18 1405") or its path."""
    path = Path(name)
    for candidate in (path, root / name):
        if candidate.is_dir() and (candidate / META).is_file():
            return candidate
    raise FileNotFoundError(f"No meeting capture called '{name}' in {root}. `gpw meeting list` shows them.")


# -- after stop: from two tracks to a note --

@dataclass(frozen=True)
class MeetingResult:
    status: str  # done | empty | failed
    folder: Path
    note: Path | None = None
    message: str = ""


def friendly_failure(exc: Exception) -> str:
    """A short reason for the tray and `gpw meeting list`. Exception text can echo request details."""
    name = type(exc).__name__
    if name in ("APIConnectionError", "APITimeoutError", "ConnectError", "TimeoutException"):
        return "Could not reach OpenAI."
    if name in ("AuthenticationError", "PermissionDeniedError"):
        return "OpenAI rejected the API key."
    if name == "RateLimitError":
        return "OpenAI rate limit or quota reached."
    if isinstance(exc, (OSError, ValueError)) and not hasattr(exc, "status_code"):
        return f"{name}: {exc}"[:200]
    return f"{name}."


class MeetingProcessor:
    def __init__(
        self,
        config: AppConfig,
        engine: Engine,
        text_model: TextModel,
        dictionary: DictionaryStore | None = None,
        *,
        prompt_dir: Path | None = None,
        workers: int = 4,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        from .config import PROMPT_DIR

        self.config = config
        self.engine = engine
        self.text_model = text_model
        self.dictionary = dictionary
        self.prompt_dir = prompt_dir or PROMPT_DIR
        self.workers = workers
        self.now = now

    @property
    def notes_dir(self) -> Path:
        return self.config.notes_dir / NOTES_SUBFOLDER

    def process(self, folder: Path) -> MeetingResult:
        name = folder.name
        started = time.perf_counter()
        meta = update_meta(folder, status="processing", pid=os.getpid(), error="", stage="")
        stage = "start"
        try:
            stage = "voice detection"
            tracks = {t: TrackFile(folder / f"{t}.wav") for t in TRACKS if (folder / f"{t}.wav").is_file()}
            if not tracks:
                raise FileNotFoundError(f"No audio in {folder}")
            speech = {t: detect_speech(f) for t, f in tracks.items()}
            seconds = max(s.seconds for s in speech.values())
            logger.info(
                "Meeting %s: voice detection took=%.2fs length=%s %s", name, time.perf_counter() - started, fmt_ts(seconds),
                " ".join(f"{t}_speech={sum(u.seconds for u in s.utterances):.0f}s/{len(s.utterances)}" for t, s in speech.items()),
            )
            turns = build_turns({t: s.utterances for t, s in speech.items()})
            if not turns:
                update_meta(folder, status="empty", seconds=round(seconds, 1), stage="", error="")
                logger.info("Meeting %s: no speech on either track; no note written, audio kept", name)
                return MeetingResult("empty", folder, message="No speech was heard on either track, so no note was written.")

            stage = "transcription"
            t_stt = time.perf_counter()
            dictionary = self.dictionary.current() if self.dictionary else None
            keywords = self.dictionary.keywords() if self.dictionary else []
            app = str(meta.get("app") or "")
            context = f"A {app} call" if app else "A call"
            cost = estimate_cost(self.engine.model, sum(t.speech_seconds for t in turns) / 60)
            logger.info(
                "Meeting %s: turns=%s speech=%.0fs est_cost=%s", name, len(turns),
                sum(t.speech_seconds for t in turns), f"${cost:.3f}" if cost is not None else "unknown",
            )
            texts, stats = transcribe_turns(
                turns, tracks, self.engine, ChunkCache(folder / "work"), folder / "work" / "audio",
                keywords=keywords, languages=self.config.languages, context=context,
                batch_seconds=self.config.chunk_minutes * 60, workers=self.workers,
                on_batch=lambda b, n: logger.info("Meeting %s: batch %s/%s done", name, b, n),
            )
            logger.info(
                "Meeting %s: transcribed requests=%s cached=%s batches=%s keywords=%s%s took=%.2fs",
                name, stats.requests, stats.cached, stats.batches, len(keywords),
                " (dropped)" if stats.keywords_dropped else "", time.perf_counter() - t_stt,
            )
            # Dictionary fixes; light cleanup is not run, so the transcript stays word for word.
            # Preferred name spellings (Phase 2b's `prefer:`) join here once that lands.
            replace = dictionary.replace if dictionary else {}
            lines = to_lines(turns, texts, lambda text: apply_replacements(text, replace))
            lines, echoes = drop_echo(lines)
            lines = merge_lines(lines)
            logger.info(
                "Meeting %s: lines=%s me=%s them=%s echo_dropped=%s", name, len(lines),
                sum(line.speaker == SPEAKERS["me"] for line in lines),
                sum(line.speaker == SPEAKERS["them"] for line in lines), echoes,
            )
            if not lines:
                update_meta(folder, status="empty", seconds=round(seconds, 1), stage="", error="")
                logger.info("Meeting %s: OpenAI heard no words; no note written, audio kept", name)
                return MeetingResult("empty", folder, message="OpenAI heard no words, so no note was written.")
            transcript = render_lines(lines)

            stage = "summary"
            when = _started(meta, folder)
            context_lines = [f"Date: {when:%Y-%m-%d %H:%M}", f"Length: {fmt_ts(seconds)}"]
            if app:
                context_lines.append(f"Recorded from: {app}")
            if not tracks.get("me"):
                context_lines.append("Only the call audio was recorded; the user's own voice is not in it.")
            summary = summarize(self.text_model, load_profile(self.prompt_dir, PROFILE), transcript, "\n".join(context_lines))

            stage = "note"
            fields: dict[str, Any] = {
                "type": "meeting",
                "date": when.date(),
                "start": f"{when:%H:%M}",
                "duration": fmt_ts(seconds),
                "app": app or "unknown",
                "engine": self.engine.model,
                "summary_model": getattr(self.text_model, "model", ""),
                "speakers": [SPEAKERS[t] for t in ("me", "them") if t in tracks],
            }
            note = write_note(
                self.notes_dir, f"{when:%Y-%m-%d %H%M} {file_title(summary.title)}",
                render_note(fields, summary.title, summary.body, transcript),
            )
            self._clean_up(folder)
            update_meta(
                folder, status="done", note=str(note), seconds=round(seconds, 1), stage="", error="",
                finished=self.now().isoformat(timespec="seconds"),
            )
            # The note's name comes from the summary, so only its folder is logged.
            logger.info(
                "Meeting %s: note written to %s total=%.2fs audio=%s", name, note.parent, time.perf_counter() - started,
                "kept" if self.config.meetings.keep_audio else "deleted",
            )
            return MeetingResult("done", folder, note)
        except Exception as exc:
            message = friendly_failure(exc)
            logger.error("Meeting %s: %s failed (%s); audio kept for `gpw meeting retry`", name, stage, type(exc).__name__)
            try:
                update_meta(folder, status="failed", stage=stage, error=message)
            except OSError:
                pass
            return MeetingResult("failed", folder, message=f"Meeting {stage} failed: {message}")

    def _clean_up(self, folder: Path) -> None:
        shutil.rmtree(folder / "work", ignore_errors=True)  # cached turn text
        if not self.config.meetings.keep_audio:
            for track in TRACKS:
                (folder / f"{track}.wav").unlink(missing_ok=True)


def _started(meta: dict[str, Any], folder: Path) -> datetime:
    try:
        return datetime.fromisoformat(str(meta["started"]))
    except (KeyError, ValueError):
        try:
            return datetime.strptime(folder.name[:15], "%Y-%m-%d %H%M")
        except ValueError:
            return datetime.fromtimestamp(folder.stat().st_mtime)


# -- the service side: start, stop, mute, status --

class MeetingManager:
    """One capture at a time; processing runs on its own thread, so dictation and a new capture
    keep working meanwhile."""

    def __init__(
        self,
        config: AppConfig,
        backend: Callable[[], Backend],
        processor: Callable[[], MeetingProcessor] | None,
        *,
        now: Callable[[], datetime] = datetime.now,
        clock: Callable[[], float] = time.monotonic,
        thread_init: Callable[[], None] | None = com_thread_init,
        first_open_wait: float | None = None,
    ) -> None:
        self.config = config
        self.root = config.meetings_dir
        self.backend = backend
        self.processor = processor
        self.now = now
        self.clock = clock
        self.thread_init = thread_init
        self.first_open_wait = first_open_wait
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self.capture: MeetingCapture | None = None
        self.folder: Path | None = None
        self._timer: threading.Timer | None = None
        self._queue: queue.Queue[Path | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._processing: set[str] = set()
        self._processor: MeetingProcessor | None = None
        self.retry_target: Path | None = None
        self.done_seq = 0
        self.done: MeetingResult | None = None
        self.failed_seq = 0
        self.failed: MeetingResult | None = None
        self.stopped_seq = 0
        self.stopped_reason = ""

    # -- lifecycle --
    def open(self) -> None:
        """Pick up captures a previous run left: finish ones that were stopped, offer a retry for the rest."""
        me = os.getpid()
        alive = lambda pid: pid != me and pid_alive(pid)  # noqa: E731
        for info in reversed(list_meetings(self.root, alive)):
            if info.status == "stopped":
                logger.info("Meeting %s: stopped before it was processed; processing it now", info.name)
                self._submit(info.folder)
            elif info.status in ("failed", "interrupted"):
                self.retry_target = info.folder
        if self.retry_target is not None:
            logger.info("Meeting %s can be retried (%s)", self.retry_target.name, effective_status(read_meta(self.retry_target), alive))

    def close(self) -> None:
        """Service shutdown: a capture in progress is stopped and processed when the service next starts."""
        with self._lock:
            capture, folder = self.capture, self.folder
            self.capture, self.folder = None, None
            self._cancel_timer()
        if capture is not None and folder is not None:
            seconds = capture.stop()
            update_meta(folder, status="stopped", stopped=self.now().isoformat(timespec="seconds"),
                        seconds=round(seconds, 1), stop_reason="shutdown")
            logger.info("Meeting %s: stopped by shutdown after %s; it is processed at the next start", folder.name, fmt_ts(seconds))
        self._queue.put(None)

    # -- capture --
    @property
    def capturing(self) -> bool:
        return self.capture is not None

    def start(self, app_exe: str = "") -> dict[str, Any]:
        with self._lock:
            if self.capture is not None:
                raise MeetingBusy("A meeting is already being captured.")
            m = self.config.meetings
            folder = new_folder(self.root, self.now())
            app = app_label(app_exe)
            write_meta(folder, {
                "name": folder.name, "status": "recording", "pid": os.getpid(),
                "started": self.now().isoformat(timespec="seconds"), "app": app,
                "capture_mic": m.capture_mic, "keep_audio": m.keep_audio,
            })
            kwargs: dict[str, Any] = {"clock": self.clock, "thread_init": self.thread_init}
            if self.first_open_wait is not None:
                kwargs["first_open_wait"] = self.first_open_wait
            capture = MeetingCapture(folder, self.backend(), m.capture_mic, **kwargs)
            self.capture, self.folder = capture, folder  # before the tracks open, so dictation skips mic rescans
            try:
                capture.start()
            except Exception as exc:
                self.capture, self.folder = None, None
                shutil.rmtree(folder, ignore_errors=True)
                logger.error("Meeting: could not start (%s)", exc)
                raise CaptureError(str(exc) if isinstance(exc, CaptureError) else f"Could not start the capture ({type(exc).__name__}).") from exc
            update_meta(folder, devices=capture.device_names())
            self._timer = threading.Timer(m.max_hours * 3600, self._auto_stop, args=(folder,))
            self._timer.daemon = True
            self._timer.start()
        logger.info("Meeting %s: capture started mic_track=%s max_hours=%g", folder.name, m.capture_mic, m.max_hours)
        return {"folder": folder.name, "capture_mic": m.capture_mic, "max_hours": m.max_hours}

    def stop(self, reason: str = "hotkey") -> dict[str, Any]:
        with self._lock:
            capture, folder = self.capture, self.folder
            if capture is None or folder is None:
                raise NotCapturing("No meeting is being captured.")
            self._cancel_timer()
            seconds = capture.stop()
            update_meta(folder, status="stopped", stopped=self.now().isoformat(timespec="seconds"),
                        seconds=round(seconds, 1), stop_reason=reason)
            logger.info("Meeting %s: capture stopped reason=%s length=%s", folder.name, reason, fmt_ts(seconds))
            with self._state_lock:
                self.stopped_seq += 1
                self.stopped_reason = reason
            self._submit(folder)  # queued before the capture is let go, so status never shows neither
            self.capture, self.folder = None, None
        return {"folder": folder.name, "seconds": round(seconds, 1)}

    def _auto_stop(self, folder: Path) -> None:
        if self.folder == folder:
            logger.info("Meeting %s: reached meetings.max_hours; stopping", folder.name)
            try:
                self.stop("max_hours")
            except NotCapturing:
                pass

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def set_mute(self, on: bool) -> bool:
        capture = self.capture
        if capture is None:
            raise NotCapturing("No meeting is being captured.")
        capture.muted = on
        logger.info("Meeting %s: mic track %s", self.folder.name if self.folder else "-", "muted" if on else "unmuted")
        return on

    def set_dictating(self, on: bool) -> None:
        """Dictation is recording: keep it out of the Me track."""
        capture = self.capture
        if capture is not None:
            capture.dictating = on

    # -- processing --
    def retry(self, name: str = "") -> str:
        folder = find_meeting(self.root, name) if name else self.retry_target
        if folder is None:
            raise FileNotFoundError("There is no failed meeting to retry.")
        if folder.name in self._processing or folder == self.folder:
            raise MeetingBusy(f"Meeting {folder.name} is already being captured or processed.")
        status = effective_status(read_meta(folder), lambda pid: pid != os.getpid() and pid_alive(pid))
        if status in ("recording", "processing"):
            raise MeetingBusy(f"Meeting {folder.name} is {status} in another process.")
        if status == "done":
            raise ValueError(f"Meeting {folder.name} already has its note.")
        self._submit(folder)
        return folder.name

    def _submit(self, folder: Path) -> None:
        with self._state_lock:
            self._processing.add(folder.name)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="gpw-meeting-process", daemon=True)
                self._worker.start()
        self._queue.put(folder)

    def _work(self) -> None:
        while True:
            folder = self._queue.get()
            if folder is None:
                return
            try:
                result = self._process(folder)
            except Exception as exc:  # a bug, not an API failure: keep the worker alive
                logger.exception("Meeting %s: processing crashed", folder.name)
                result = MeetingResult("failed", folder, message=f"Processing crashed ({type(exc).__name__}).")
            with self._state_lock:
                self._processing.discard(folder.name)
                if result.status == "failed":
                    self.failed_seq += 1
                    self.failed = result
                    self.retry_target = folder
                else:
                    self.done_seq += 1
                    self.done = result
                    if self.retry_target == folder:
                        self.retry_target = None

    def _process(self, folder: Path) -> MeetingResult:
        if self.processor is None:
            return MeetingResult("failed", folder, message="Meeting processing is not set up.")
        if self._processor is None:
            self._processor = self.processor()
        return self._processor.process(folder)

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """For tests and shutdown: wait until nothing is queued or processing."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._state_lock:
                if not self._processing:
                    return True
            time.sleep(0.02)
        return False

    def status(self) -> dict[str, Any]:
        capture, folder = self.capture, self.folder
        with self._state_lock:
            done, failed = self.done, self.failed
            out: dict[str, Any] = {
                "capturing": capture is not None,
                "folder": folder.name if folder else "",
                "elapsed": int(capture.elapsed()) if capture else 0,
                "muted": bool(capture.muted) if capture else False,
                "mic_track": bool(capture and "me" in capture.tracks),
                "no_call_audio": bool(capture and capture.no_call_audio()),
                "processing": len(self._processing),
                "stopped_seq": self.stopped_seq,
                "stopped_reason": self.stopped_reason,
                "done_seq": self.done_seq,
                "done_status": done.status if done else "",
                "done_note": done.note.as_posix() if done and done.note else "",
                "done_message": done.message if done else "",
                "failed_seq": self.failed_seq,
                "failed_message": failed.message if failed else "",
                "failed_folder": failed.folder.as_posix() if failed else "",
                "retry": self.retry_target.name if self.retry_target else "",
            }
        return out
