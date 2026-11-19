"""Long recordings (Phase 4): drop an audio or video file in the inbox, get a Markdown note.

    files.inbox/
        <file>               uses the meeting profile
        meeting/  presentation/  bd-call/  candidate-screen/  client-intake/  hm-debrief/
        _done/               the source, once its note is written
        _failed/             the source, when it failed; the reason is in the service log and
                             `gpw inbox status`. Dropping it back in a profile folder (or
                             `gpw inbox retry <file>`) resumes from the parts already transcribed.

Pipeline for one file, run on a background thread inside `gpw serve`, one file at a time:
  1. Picked up once its size has not changed for inbox.settle_seconds and no program still has it
     open for writing (OneDrive still syncing, a copy in progress). Temp and partial files are skipped.
  2. ffmpeg prep to 16 kHz mono WAV: inbox.audio_prep, inbox.profile_prep, or a marker in the file
     name (`talk.strong.m4a` or `talk [strong].m4a`). ffmpeg runs at below-normal priority.
  3. Cut into parts of about two minutes at pauses found in the original audio. Nothing is dropped,
     so quiet speech far from the phone is still sent; only parts with no sound at all are skipped.
  4. GPT-Transcribe, one request per part, in batches of about transcription.chunk_minutes, with
     dictionary keywords, the profile as context and the end of the previous batch carried over
     (meeting_transcript.transcribe_turns). Every finished part lands in the chunk cache.
  5. Dictionary fixes, then the summary model with the profile's prompt (summary.py).
  6. notes/<profile>/YYYY-MM-DD <title>.md (notes.py), and the source moves to _done/.

Transcript text never goes to the log; file names, counts, stages and timings do.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from . import audio
from .config import INBOX_PROFILES, AppConfig, InboxConfig
from .dictionary import DictionaryStore, apply_replacements
from .engines import estimate_cost
from .longform import ChunkCache, Engine, fmt_ts, paragraphs, work_key
from .meeting import friendly_failure
from .meeting_capture import TrackFile
from .meeting_transcript import Turn, transcribe_turns
from .notes import file_title, render_note, write_note
from .summary import TextModel, load_profile, summarize
from .vad import MIN_THRESHOLD_DBFS, Span, frame_db

logger = logging.getLogger(__name__)

DEFAULT_PROFILE = "meeting"
DONE = "_done"
FAILED = "_failed"
TRACK = "audio"  # the one track of a recording, in the turn and cache keys
PIECE_SECONDS = 120.0  # one GPT-Transcribe request; a timestamp in the note for each
PIECE_WINDOW = 30.0  # how far from the target a cut may move to land in a pause
LEDGER_LIMIT = 200
POLL_BUSY = 1.0  # rescan this often while a file is settling
POLL_IDLE = 30.0  # and this often otherwise; watchdog events wake the scanner sooner

# What GPT-Transcribe and the summary are told about each profile.
PROFILE_LABELS = {
    "meeting": "meeting",
    "presentation": "presentation",
    "bd-call": "business development call",
    "candidate-screen": "candidate screening call",
    "client-intake": "client intake call",
    "hm-debrief": "hiring manager debrief",
}

TEMP_SUFFIXES = {
    ".tmp", ".temp", ".part", ".partial", ".crdownload", ".download", ".opdownload", ".!ut", ".dwl", ".dwl2",
}
PREP_MARKER = re.compile(r"(?:\.(off|light|strong)$|\[(off|light|strong)\])", re.IGNORECASE)
DATE_IN_NAME = re.compile(r"(?<!\d)(20\d{2})[-_. ]?(0[1-9]|1[0-2])[-_. ]?(0[1-9]|[12]\d|3[01])(?!\d)")

# Windows file attributes of a cloud file that is not on this PC yet (OneDrive "online-only").
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
CLOUD_ONLY = FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS


class InboxError(RuntimeError):
    """A failure with a reason meant for the user, such as a file with no sound."""


# -- which files, which profile --

def ensure_folders(inbox: Path) -> None:
    for name in [*INBOX_PROFILES, DONE, FAILED]:
        (inbox / name).mkdir(parents=True, exist_ok=True)


def profile_for(inbox: Path, path: Path) -> str | None:
    """The profile a file's folder picks: the inbox itself is the meeting profile; a profile
    subfolder is that profile (any case); anything else, such as _done/ or a nested folder, is None."""
    parent = path.parent
    if _same(parent, inbox):
        return DEFAULT_PROFILE
    if _same(parent.parent, inbox):
        name = parent.name.lower()
        if name in INBOX_PROFILES:
            return name
    return None


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def skip_reason(path: Path) -> str | None:
    """Why a file in a profile folder is left alone, or None to process it."""
    name = path.name
    if name.startswith(("~$", "~", ".")):
        return "temporary or hidden file"
    suffix = path.suffix.lower()
    if suffix in TEMP_SUFFIXES or name.lower().endswith(".partial.m4a"):
        return "partial download"
    if suffix not in audio.AUDIO_EXTS:
        return "not an audio or video file"
    return None


def prep_marker(name: str) -> str | None:
    """`talk.strong.m4a` or `talk [strong].m4a` -> "strong". None when the name has no marker."""
    stem = Path(name).stem
    m = PREP_MARKER.search(stem)
    if not m:
        return None
    return (m.group(1) or m.group(2)).lower()


def prep_for(inbox: InboxConfig, profile: str, name: str) -> str:
    """The audio prep level for a file: its name's marker, else its profile's, else inbox.audio_prep."""
    return prep_marker(name) or inbox.prep_for(profile)


def recording_date(name: str, mtime: float, today: date | None = None) -> date:
    """The day a recording was made: a date in its name (2026-11-18, 20261118_140501), else the
    file's modified time, which a move or a OneDrive sync keeps."""
    today = today or date.today()
    for m in DATE_IN_NAME.finditer(name):
        try:
            found = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if found <= today:
            return found
    return datetime.fromtimestamp(mtime).date()


def is_cloud_only(path: Path) -> bool:
    """True for a OneDrive file that is still only in the cloud. Reading it downloads it."""
    try:
        attributes = getattr(os.stat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & CLOUD_ONLY)


def writer_done(path: Path) -> bool:
    """False while another program has the file open for writing (a copy or a sync in progress).
    Opens it for reading while refusing to share write access, which Windows only allows when
    nobody is writing. Opening does not download an online-only OneDrive file."""
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING = 0x80000000, 0x1, 3
    FILE_FLAG_OPEN_NO_RECALL = 0x00100000
    handle = kernel32.CreateFileW(str(path), GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING, FILE_FLAG_OPEN_NO_RECALL, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        return False
    kernel32.CloseHandle(handle)
    return True


def hydrate(path: Path, block: int = 4 * 1024 * 1024) -> None:
    """Read an online-only OneDrive file through once, so OneDrive downloads it."""
    with open(path, "rb") as f:
        while f.read(block):
            pass


def move_unique(src: Path, folder: Path) -> Path:
    """Move a file into a folder without replacing anything there: "talk.m4a" -> "talk (2).m4a"."""
    folder.mkdir(parents=True, exist_ok=True)
    dest, n = folder / src.name, 2
    while dest.exists():
        dest, n = folder / f"{src.stem} ({n}){src.suffix}", n + 1
    try:
        src.rename(dest)  # keeps the modified time, so a re-drop finds its cached parts
    except OSError:
        shutil.move(str(src), str(dest))
    return dest


# -- waiting for a file to finish arriving --

@dataclass
class _Seen:
    size: int
    mtime_ns: int
    since: float


class SettleTracker:
    """A file is ready once its size and modified time have stayed the same for `settle_seconds`."""

    def __init__(self, settle_seconds: float) -> None:
        self.settle_seconds = settle_seconds
        self._seen: dict[str, _Seen] = {}

    def update(self, key: str, size: int, mtime_ns: int, now: float) -> bool:
        seen = self._seen.get(key)
        if seen is None or seen.size != size or seen.mtime_ns != mtime_ns:
            self._seen[key] = _Seen(size, mtime_ns, now)
            return False
        return size > 0 and now - seen.since >= self.settle_seconds

    def forget(self, key: str) -> None:
        self._seen.pop(key, None)

    def keep_only(self, keys: set[str]) -> None:
        for key in list(self._seen):
            if key not in keys:
                del self._seen[key]

    @property
    def waiting(self) -> int:
        return len(self._seen)


# -- the ledger: what happened to each file, for `gpw inbox status` and retry --

class Ledger:
    """A small JSON file in the work folder. Never holds transcript text."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def entries(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [e for e in data.get("entries", []) if isinstance(e, dict)] if isinstance(data, dict) else []

    def add(self, **entry: Any) -> None:
        entry["at"] = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            entries = self.entries()
            entries.append(entry)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"entries": entries[-LEDGER_LIMIT:]}, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def last_for(self, file: str) -> dict[str, Any] | None:
        """The newest entry for a file as it now sits, such as "_failed/talk.m4a"."""
        want = file.replace("\\", "/").lower()
        for entry in reversed(self.entries()):
            if str(entry.get("file", "")).replace("\\", "/").lower() == want:
                return entry
        return None


def ledger_for(config: AppConfig) -> Ledger:
    return Ledger(config.work_dir / "inbox" / "ledger.json")


# -- one file to one note --

class Ffmpeg(Protocol):
    def clean(self, src: Path, dst: Path, level: str) -> None: ...

    def silences(self, src: Path) -> list[tuple[float, float]]: ...


class BundledFfmpeg:
    """ffmpeg from imageio-ffmpeg, at below-normal priority."""

    def __init__(self) -> None:
        self.exe = audio.find_ffmpeg()

    def clean(self, src: Path, dst: Path, level: str) -> None:
        audio.clean_to_wav(self.exe, src, dst, level, background=True)

    def silences(self, src: Path) -> list[tuple[float, float]]:
        return audio.detect_silences(self.exe, src, background=True)


@dataclass(frozen=True)
class InboxResult:
    status: str  # done | failed
    source: Path  # where the source ended up: _done/, _failed/, or where it was if it could not be moved
    profile: str
    note: Path | None = None
    message: str = ""
    stage: str = ""


def plan_pieces(duration: float, silences: list[tuple[float, float]], piece: float = PIECE_SECONDS) -> list[tuple[float, float]]:
    """Parts of about `piece` seconds that cover the whole recording, cut in pauses where there are some."""
    if duration <= 0:
        return []
    cuts = audio.plan_cuts(duration, silences, piece, window=PIECE_WINDOW) if duration > piece * 1.15 else [0.0, duration]
    return [(a, b) for a, b in zip(cuts[:-1], cuts[1:]) if b - a > 0.05]


def silent(track: TrackFile, start: float, end: float) -> bool:
    """No sound at all: never louder than the quietest level voice detection would call speech."""
    db = frame_db([track.read_seconds(start, end)], track.sample_rate)
    return not len(db) or float(db.max()) < MIN_THRESHOLD_DBFS


def render_transcript(segments: list[tuple[float, str]]) -> str:
    """`[00:00:00] text` for each part, long parts broken into paragraphs."""
    out = []
    for start, text in segments:
        paras = paragraphs(text) or [text]
        out.append(f"[{fmt_ts(start)}] " + "\n\n".join(paras))
    return "\n\n".join(out) + ("\n" if out else "")


class RecordingProcessor:
    def __init__(
        self,
        config: AppConfig,
        engine: Engine,
        text_model: TextModel,
        dictionary: DictionaryStore | None = None,
        *,
        ffmpeg: Ffmpeg | Callable[[], Ffmpeg] | None = None,
        prompt_dir: Path | None = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        from .config import PROMPT_DIR

        self.config = config
        self.engine = engine
        self.text_model = text_model
        self.dictionary = dictionary
        self._ffmpeg = ffmpeg
        self.prompt_dir = prompt_dir or PROMPT_DIR
        self.now = now
        self.ledger = ledger_for(config)

    @property
    def inbox(self) -> Path:
        return self.config.inbox_dir

    @property
    def work_root(self) -> Path:
        return self.config.work_dir / "inbox"

    def ffmpeg(self) -> Ffmpeg:
        if self._ffmpeg is None:
            self._ffmpeg = BundledFfmpeg()
        elif callable(self._ffmpeg) and not hasattr(self._ffmpeg, "clean"):
            self._ffmpeg = self._ffmpeg()
        return self._ffmpeg  # type: ignore[return-value]

    def process(self, src: Path, profile: str, progress: Callable[[str], None] | None = None) -> InboxResult:
        """Turn one file into a note and move it to _done/, or move it to _failed/ with the reason."""
        report = progress or (lambda text: None)
        name = src.name
        started = time.perf_counter()
        stage = "start"
        try:
            stat = src.stat()
            level = prep_for(self.config.inbox, profile, name)
            work = self.work_root / cache_key(src, stat.st_size, stat.st_mtime_ns, self.engine.model, level)
            work.mkdir(parents=True, exist_ok=True)
            logger.info("Inbox %s: started profile=%s prep=%s size=%.1fMB", name, profile, level, stat.st_size / 1e6)

            if is_cloud_only(src):
                stage = "download"
                report("downloading from OneDrive")
                t = time.perf_counter()
                hydrate(src)
                logger.info("Inbox %s: downloaded from OneDrive took=%.2fs", name, time.perf_counter() - t)

            stage = "audio prep"
            report("preparing the audio")
            t = time.perf_counter()
            wav = work / "clean.wav"
            if not wav.is_file():
                tmp = work / "clean.tmp.wav"
                try:
                    self.ffmpeg().clean(src, tmp, level)
                except RuntimeError as exc:
                    detail = [line for line in str(exc).splitlines() if line.strip()][-1:]
                    logger.info("Inbox %s: ffmpeg said: %s", name, (detail[0] if detail else "")[:200])
                    raise InboxError("ffmpeg could not read it as audio or video. The file may be damaged.") from exc
                tmp.replace(wav)
            track = TrackFile(wav)
            duration = track.seconds
            logger.info("Inbox %s: prep level=%s length=%s took=%.2fs", name, level, fmt_ts(duration), time.perf_counter() - t)
            if duration < 0.5:
                raise InboxError("The file has no audio in it.")

            stage = "planning"
            pieces = self._pieces(work, src, duration)
            turns = [Turn(TRACK, (Span(a, b),)) for a, b in pieces if not silent(track, a, b)]
            cost = estimate_cost(self.engine.model, sum(t.speech_seconds for t in turns) / 60)
            logger.info(
                "Inbox %s: parts=%s silent_skipped=%s est_cost=%s", name, len(turns), len(pieces) - len(turns),
                f"${cost:.3f}" if cost is not None else "unknown",
            )
            if not turns:
                raise InboxError("The file is silent: no sound was found in it.")

            stage = "transcription"
            report(f"transcribing {len(turns)} parts")
            t = time.perf_counter()
            dictionary = self.dictionary.current() if self.dictionary else None
            keywords = self.dictionary.keywords() if self.dictionary else list(self.config.keywords)
            label = PROFILE_LABELS.get(profile, profile)
            texts, stats = transcribe_turns(
                turns, {TRACK: track}, self.engine, ChunkCache(work / "text"), work / "audio",
                keywords=keywords, languages=self.config.languages, context=f"A recorded {label}",
                batch_seconds=self.config.chunk_minutes * 60, workers=self.config.inbox.parallel_requests,
                on_batch=lambda b, n: (report(f"transcribing, batch {b} of {n}"),
                                       logger.info("Inbox %s: batch %s/%s done", name, b, n)),
            )
            logger.info(
                "Inbox %s: transcribed requests=%s cached=%s batches=%s keywords=%s%s took=%.2fs",
                name, stats.requests, stats.cached, stats.batches, len(keywords),
                " (dropped)" if stats.keywords_dropped else "", time.perf_counter() - t,
            )
            # Dictionary fixes; light cleanup is not run, so the transcript stays word for word.
            # Preferred name spellings (Phase 2b's `prefer:`) join here once that lands, as in meeting.py.
            replace = dictionary.replace if dictionary else {}
            segments = [(turn.start, apply_replacements(" ".join(text.split()), replace)) for turn, text in zip(turns, texts)]
            segments = [(start, text) for start, text in segments if text]
            if not segments:
                raise InboxError("OpenAI heard no words in the file.")
            transcript = render_transcript(segments)
            if self.config.debug_log_transcripts:
                logger.info("Inbox %s transcript:\n%s", name, transcript)

            stage = "summary"
            report("writing the summary")
            when = recording_date(name, stat.st_mtime, self.now().date())
            context = "\n".join([
                f"Recording: {name}", f"Kind: {label}", f"Date: {when:%Y-%m-%d}", f"Length: {fmt_ts(duration)}",
            ])
            prompt = INBOX_PROFILES[profile]
            summary = summarize(
                self.text_model, load_profile(self.prompt_dir, prompt), transcript, context,
                default_title=_fallback_title(name),
            )

            stage = "note"
            fields: dict[str, Any] = {
                "type": "recording",
                "profile": profile,
                "date": when,
                "duration": fmt_ts(duration),
                "source": name,
                "engine": self.engine.model,
                "audio_prep": level,
                "summary_model": getattr(self.text_model, "model", ""),
            }
            note = write_note(
                self.config.notes_dir / profile, f"{when:%Y-%m-%d} {file_title(summary.title)}",
                render_note(fields, summary.title, summary.body, transcript),
            )

            stage = "move"
            dest = move_unique(src, self.inbox / DONE)
            shutil.rmtree(work, ignore_errors=True)
            self._record(dest, name, profile, "done", note=str(note))
            # The note's name comes from the summary, so only its folder is logged.
            logger.info("Inbox %s: note written to %s; source moved to %s total=%.2fs",
                        name, note.parent, DONE, time.perf_counter() - started)
            return InboxResult("done", dest, profile, note)
        except Exception as exc:
            message = str(exc) if isinstance(exc, InboxError) else friendly_failure(exc)
            logger.error("Inbox %s: %s failed (%s: %s); moving it to %s", name, stage,
                         type(exc).__name__, message.rstrip("."), FAILED)
            dest = src
            if src.exists() and stage != "move":
                try:
                    dest = move_unique(src, self.inbox / FAILED)
                except OSError as move_error:
                    logger.error("Inbox %s: could not move it to %s (%s)", name, FAILED, move_error)
            try:
                self._record(dest, name, profile, "failed", stage=stage, error=message)
            except OSError:
                pass
            return InboxResult("failed", dest, profile, message=f"{stage.capitalize()} failed: {message}", stage=stage)

    def _pieces(self, work: Path, src: Path, duration: float) -> list[tuple[float, float]]:
        """The cut plan, saved in the work folder so a retry sends the very same parts."""
        path = work / "plan.json"
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if abs(float(saved["duration"]) - duration) < 0.01:
                return [(float(a), float(b)) for a, b in saved["pieces"]]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        t = time.perf_counter()
        silences = self.ffmpeg().silences(src) if duration > PIECE_SECONDS * 1.15 else []
        pieces = plan_pieces(duration, silences)
        path.write_text(json.dumps({"duration": duration, "pieces": pieces}), encoding="utf-8")
        logger.info("Inbox %s: pauses=%s parts=%s took=%.2fs", src.name, len(silences), len(pieces), time.perf_counter() - t)
        return pieces

    def _record(self, dest: Path, source: str, profile: str, status: str, **fields: Any) -> None:
        try:
            file = dest.relative_to(self.inbox).as_posix()
        except ValueError:
            file = dest.as_posix()
        self.ledger.add(file=file, source=source, profile=profile, status=status, **fields)


def cache_key(src: Path, size: int, mtime_ns: int, model: str, level: str) -> str:
    """The work folder for a file. Leaves the name out, so a file renamed on its way through
    _failed/ (talk (2).m4a) still finds its cached parts; a move or copy keeps size and time."""
    return work_key(Path(f"recording{src.suffix.lower()}"), size, mtime_ns, model, level, int(PIECE_SECONDS // 60))


def _fallback_title(name: str) -> str:
    stem = PREP_MARKER.sub("", Path(name).stem)
    return " ".join(stem.replace("_", " ").split()) or "Recording"


def prune_work(root: Path, days: int, now: float | None = None) -> int:
    """Delete cached parts of files that failed more than `days` ago and were never dropped again."""
    if not root.is_dir():
        return 0
    cutoff = (now or time.time()) - days * 86400
    removed = 0
    for folder in root.iterdir():
        try:
            if folder.is_dir() and folder.stat().st_mtime < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        except OSError:
            pass
    return removed


# -- the service side: watch, wait, queue, one file at a time --

@dataclass
class _Job:
    path: Path
    profile: str


@dataclass
class _Current:
    file: str = ""
    profile: str = ""
    step: str = ""
    started: float = 0.0


@dataclass
class InboxState:
    done_seq: int = 0
    done: InboxResult | None = None
    failed_seq: int = 0
    failed: InboxResult | None = None
    current: _Current = field(default_factory=_Current)


class InboxManager:
    """Runs inside `gpw serve`. A scanner thread finds files that have finished arriving; one worker
    thread processes them one at a time, so dictation and meeting capture never wait on it."""

    def __init__(
        self,
        config: AppConfig,
        processor: Callable[[], RecordingProcessor] | None,
        *,
        clock: Callable[[], float] = time.monotonic,
        writer_done: Callable[[Path], bool] = writer_done,
        watch: bool = True,
    ) -> None:
        self.config = config
        self.root = config.inbox_dir
        self.processor = processor
        self.clock = clock
        self.writer_done = writer_done
        self.watch = watch
        self.tracker = SettleTracker(config.inbox.settle_seconds)
        self._queue: queue.Queue[_Job | None] = queue.Queue()
        self._lock = threading.Lock()
        self._active: set[str] = set()  # queued or processing, so a file is never queued twice
        self._skipped: set[str] = set()  # files already logged as left alone
        self._stuck: dict[str, tuple[int, int]] = {}  # failed but could not be moved: wait for a change
        self._processor: RecordingProcessor | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._observer: Any = None
        self.state = InboxState()

    # -- lifecycle --
    def open(self) -> None:
        if not self.config.inbox.enabled:
            logger.info("Inbox: off (inbox.enabled is false)")
            return
        ensure_folders(self.root)
        removed = prune_work(self.config.work_dir / "inbox", self.config.inbox.keep_work_days)
        if removed:
            logger.info("Inbox: removed %s cached parts older than %s days", removed, self.config.inbox.keep_work_days)
        if self.watch:
            self._observer = start_observer(self.root, self._wake.set)
        for target, name in ((self._scan_loop, "gpw-inbox-scan"), (self._work, "gpw-inbox-process")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        logger.info("Inbox: watching %s (%s) settle=%gs", self.root,
                    "file events and a rescan" if self._observer else "a rescan every few seconds",
                    self.config.inbox.settle_seconds)

    def close(self) -> None:
        """Service shutdown. A file being processed stays where it is and resumes from its cached
        parts at the next start."""
        self._stop.set()
        self._wake.set()
        self._queue.put(None)
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2)
            except Exception:  # noqa: BLE001 - shutting down
                pass
        for thread in self._threads:
            if thread.name == "gpw-inbox-scan":
                thread.join(timeout=2)

    # -- finding files --
    def candidates(self, quiet: bool = False) -> list[tuple[Path, str]]:
        """Files in the inbox and its profile folders that are not temp, partial or other files.
        Logs each file or folder it leaves alone once, unless `quiet`."""
        out: list[tuple[Path, str]] = []
        try:
            folders = [self.root, *(e for e in self.root.iterdir() if e.is_dir())]
        except OSError as exc:
            logger.warning("Inbox: cannot read %s (%s)", self.root, exc)
            return out
        for folder in folders:
            if folder is not self.root and folder.name.lower() not in INBOX_PROFILES:
                if not quiet and folder.name not in (DONE, FAILED) and _key(folder) not in self._skipped:
                    self._skipped.add(_key(folder))
                    logger.info("Inbox: %s is not a profile folder, so files in it are left alone", folder.name)
                continue
            try:
                entries = list(folder.iterdir())
            except OSError:
                continue
            for path in entries:
                profile = profile_for(self.root, path)
                try:
                    if profile is None or not path.is_file():
                        continue
                except OSError:
                    continue
                reason = skip_reason(path)
                if reason:
                    key = _key(path)
                    if (not quiet and key not in self._skipped and reason == "not an audio or video file"
                            and path.name.lower() != "desktop.ini"):
                        self._skipped.add(key)
                        logger.info("Inbox: left %s alone (%s)", path.name, reason)
                    continue
                out.append((path, profile))
        return out

    def scan_once(self) -> int:
        """One pass: note sizes, queue files that have settled. Returns how many were queued."""
        now = self.clock()
        queued = 0
        present: set[str] = set()
        for path, profile in self.candidates():
            key = _key(path)
            with self._lock:
                if key in self._active:
                    continue
            present.add(key)
            try:
                st = path.stat()
            except OSError:
                continue
            with self._lock:
                stuck = self._stuck.get(key)
                if stuck is not None:
                    if stuck == (st.st_size, st.st_mtime_ns):
                        continue
                    del self._stuck[key]
            if not self.tracker.update(key, st.st_size, st.st_mtime_ns, now):
                continue
            if not self.writer_done(path):
                continue  # still open for writing: check again next pass
            self.tracker.forget(key)
            present.discard(key)
            with self._lock:
                self._active.add(key)
            self._queue.put(_Job(path, profile))
            queued += 1
            logger.info("Inbox: queued %s (profile %s)", path.name, profile)
        self.tracker.keep_only(present)
        return queued

    def _scan_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception:  # keep watching whatever happens
                logger.exception("Inbox: scan failed")
            self._wake.wait(POLL_BUSY if self.tracker.waiting else POLL_IDLE)
            self._wake.clear()

    # -- processing --
    def _work(self) -> None:
        while True:
            job = self._queue.get()
            if job is None or self._stop.is_set():
                return
            key = _key(job.path)
            with self._lock:
                self.state.current = _Current(job.path.name, job.profile, "starting", self.clock())
            try:
                result = self._process(job)
            except Exception as exc:  # a bug, not an API failure: keep the worker alive
                logger.exception("Inbox %s: processing crashed", job.path.name)
                result = InboxResult("failed", job.path, job.profile, message=f"Processing crashed ({type(exc).__name__}).")
            stuck = None
            if result.status == "failed" and _same(result.source, job.path):
                try:
                    st = job.path.stat()
                    stuck = (st.st_size, st.st_mtime_ns)  # not moved: don't try it again until it changes
                except OSError:
                    pass
            with self._lock:
                if stuck is not None:
                    self._stuck[key] = stuck
                self._active.discard(key)
                self.state.current = _Current()
                if result.status == "done":
                    self.state.done_seq += 1
                    self.state.done = result
                else:
                    self.state.failed_seq += 1
                    self.state.failed = result
            self._wake.set()  # look again: more may have arrived meanwhile

    def _process(self, job: _Job) -> InboxResult:
        if self.processor is None:
            return InboxResult("failed", job.path, job.profile, message="Recording processing is not set up.")
        if not job.path.exists():
            return InboxResult("failed", job.path, job.profile, message="The file was moved or deleted before it was processed.")
        if self._processor is None:
            self._processor = self.processor()
        return self._processor.process(job.path, job.profile, self._progress)

    def _progress(self, step: str) -> None:
        with self._lock:
            self.state.current.step = step

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """For tests: wait until nothing is queued or processing."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._active:
                    return True
            time.sleep(0.02)
        return False

    def status(self) -> dict[str, Any]:
        with self._lock:
            s = self.state
            current = s.current
            return {
                "enabled": self.config.inbox.enabled,
                "folder": self.root.as_posix(),
                "processing": current.file,
                "profile": current.profile,
                "step": current.step,
                "queued": max(0, len(self._active) - (1 if current.file else 0)),
                "waiting": self.tracker.waiting,
                "done_seq": s.done_seq,
                "done_note": s.done.note.as_posix() if s.done and s.done.note else "",
                "done_file": s.done.source.name if s.done else "",
                "failed_seq": s.failed_seq,
                "failed_file": s.failed.source.name if s.failed else "",
                "failed_message": s.failed.message if s.failed else "",
                "failed_folder": (self.root / FAILED).as_posix(),
            }


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def start_observer(root: Path, wake: Callable[[], None]) -> Any:
    """watchdog file events wake the scanner at once. Without watchdog the rescan still finds files."""
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        logger.warning("Inbox: the watchdog package is not installed; falling back to a rescan every %gs", POLL_IDLE)
        return None

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event: Any) -> None:
            wake()

    try:
        observer = Observer()
        observer.schedule(Handler(), str(root), recursive=True)
        observer.daemon = True
        observer.start()
        return observer
    except Exception as exc:  # noqa: BLE001 - a network or cloud folder may refuse change notifications
        logger.warning("Inbox: cannot watch %s for changes (%s); falling back to a rescan every %gs", root, exc, POLL_IDLE)
        return None


# -- for the CLI: status and retry --

@dataclass(frozen=True)
class InboxListing:
    waiting: list[tuple[str, str]]  # (profile, file name)
    failed: list[tuple[str, dict[str, Any]]]  # (file name in _failed, its newest ledger entry or {})
    done: list[dict[str, Any]]  # newest first


def listing(config: AppConfig, recent: int = 10) -> InboxListing:
    root = config.inbox_dir
    manager = InboxManager(config, None, watch=False)
    waiting = sorted((profile, path.name) for path, profile in manager.candidates(quiet=True)) if root.is_dir() else []
    ledger = ledger_for(config)
    failed = []
    failed_dir = root / FAILED
    if failed_dir.is_dir():
        for path in sorted(failed_dir.iterdir()):
            if path.is_file() and not skip_reason(path):
                failed.append((path.name, ledger.last_for(f"{FAILED}/{path.name}") or {}))
    done = [e for e in reversed(ledger.entries()) if e.get("status") == "done"][:recent]
    return InboxListing(waiting, failed, done)


def retry_file(config: AppConfig, name: str, profile: str | None = None) -> tuple[Path, str]:
    """Move a file from _failed/ back into its profile folder, where the service picks it up again.
    Its cached parts are found again, so only what is missing is paid for. Returns (new path, profile)."""
    root = config.inbox_dir
    failed_dir = root / FAILED
    path = Path(name)
    candidates = [path] if path.is_absolute() else [failed_dir / name, path]
    src = next((p for p in candidates if p.is_file()), None)
    if src is None or not _same(src.parent, failed_dir):
        raise FileNotFoundError(f"No file called '{name}' in {failed_dir}. `gpw inbox status` lists the failed files.")
    if profile is None:
        entry = ledger_for(config).last_for(f"{FAILED}/{src.name}") or {}
        profile = str(entry.get("profile") or DEFAULT_PROFILE)
    profile = profile.lower()
    if profile not in INBOX_PROFILES:
        raise ValueError(f"'{profile}' is not an inbox profile; use one of {', '.join(INBOX_PROFILES)}")
    target = root if profile == DEFAULT_PROFILE else root / profile
    return move_unique(src, target), profile
