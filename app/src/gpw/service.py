"""The always-running dictation service: mic, clip prep, GPT-Transcribe, snippets, cleanup, timing logs, daily log.

The AutoHotkey script drives it over HTTP on 127.0.0.1 (server.py, ADR-003). Transcript
text goes back to the script and into the daily note, never into the log unless
privacy.debug_log_transcripts is on.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np
import soundfile as sf

from . import __version__
from .autolearn import Learner
from .cleanup import clean_dictation
from .clip_prep import encode, ffmpeg_filter, prepare_clip
from .config import AppConfig
from .dictionary import DictionaryStore, apply_replacements
from .engines import TimedText, clean_keywords
from .latency import SCREEN_WAIT_CAP, append_record
from .markdown_store import append_daily_dictation
from .mic import MicCapture, MicError, MicTimeout
from .names import NameFix, merge_keywords
from .recent import RecentDictation, RecentDictations, from_daily_log, snippet_label
from .screen import ScreenReader, ScreenResult
from .snippets import Snippet, SnippetStore, match_snippet, snippet_named, spoken_name

logger = logging.getLogger(__name__)

DEVICE_REFRESH_SECONDS = 60.0  # re-read the mic list before a start if it is older than this (costs ~40 ms)
PREWARM_IDLE_SECONDS = 20.0  # skip the prewarm if OpenAI was used more recently than this
PENDING_LIMIT = 20
# Screen names are read while the mic records. At release, wait at most this long (from the key
# release) for a read that is still running, then go on without it. Keeps the Phase 2b cost under 0.1 s.
SCREEN_DEADLINE_SECONDS = SCREEN_WAIT_CAP  # 0.08 s

SKIP_MESSAGES = {
    "quiet": "Didn't hear anything. Nothing was sent or pasted.",
    "too_little_speech": "Too short to be speech. Nothing was sent or pasted.",
    "empty_transcript": "OpenAI heard no words. Nothing was pasted.",
}


class ServiceBusy(RuntimeError):
    pass


class Engine(Protocol):
    model: str

    def transcribe_clip(
        self, filename: str, data: bytes, keywords: list[str], languages: list[str], timeout: float
    ) -> TimedText: ...

    def prewarm(self) -> None: ...


@dataclass(frozen=True)
class StopResult:
    status: str  # ok | skipped | error | cancelled
    dictation_id: int
    text: str = ""
    message: str = ""
    note: str = ""  # shown after the paste, such as a dictionary problem


def _r(seconds: float) -> float:
    return round(seconds, 3)


def friendly_error(exc: Exception) -> str:
    """A short reason for the tooltip. Exception text is not shown: it can echo request details."""
    name = type(exc).__name__
    if name in ("APIConnectionError", "APITimeoutError", "ConnectError", "TimeoutException"):
        return "Could not reach OpenAI. Check the internet connection."
    if name in ("AuthenticationError", "PermissionDeniedError"):
        return "OpenAI rejected the API key. Check OPENAI_API_KEY in .env."
    if name == "RateLimitError":
        return "OpenAI rate limit or quota reached."
    return f"Transcription failed ({name})."


class DictationService:
    def __init__(
        self,
        config: AppConfig,
        engine: Engine,
        mic: MicCapture,
        *,
        clean: Callable[[str], str] | None = None,
        dictionary: DictionaryStore | None = None,
        snippets: SnippetStore | None = None,
        learner: Learner | None = None,
        screen: ScreenReader | None = None,
        dry_run: bool = False,
        assets_dir: Path | None = None,
        post_executor: Executor | None = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.config = config
        self.engine = engine
        self.mic = mic
        self.clean = clean  # the optional text-model pass (cleanup.ai_pass)
        self.dictionary = dictionary
        self.snippets = snippets
        self.learner = learner
        self.screen = screen
        self.dry_run = dry_run
        self.assets_dir = assets_dir or config.data_dir / "assets"
        self.now = now
        self.keywords = clean_keywords(config.keywords)  # used when there is no dictionary file
        self.latency_log = config.logs_dir / "dictation-latency.jsonl"
        self._post = post_executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpw-post")
        self._lock = threading.Lock()  # service state
        self._mic_lock = threading.Lock()  # PortAudio calls
        self._state = "idle"  # idle | recording | processing
        self._id = 0
        self._session: dict[str, Any] = {}
        self._cancelled: set[int] = set()
        self._pending: OrderedDict[int, dict[str, Any]] = OrderedDict()
        # Pasted text and the name spellings seen on screen, for auto-learn; never logged.
        self._texts: OrderedDict[int, tuple[str, frozenset[str]]] = OrderedDict()
        self._screen_read: Any = None  # the screen-names read for the recording in progress (a Future)
        self.recent = RecentDictations()  # the recent-dictations window and paste_last; memory only, never logged
        self._watchdog: threading.Timer | None = None
        self._last_api = 0.0
        self._last_refresh = time.monotonic()

    # -- lifecycle --
    def open(self) -> None:
        """Open the mic now when warm_mic is on, so the first dictation has pre-roll."""
        with self._mic_lock:
            self.mic.open_warm()
        self.load_recent()
        if self.learner is not None:
            self.learner.start()
        if self.screen is not None:
            self.screen.start()

    def load_recent(self) -> None:
        """Refill recent dictations from today's daily log, so a restart does not empty the window."""
        if not self.config.dictation.daily_log:
            return
        try:
            items = from_daily_log(self.config.notes_dir, self.now().date(), self._snippet_text)
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Could not read today's daily log for recent dictations: %s", exc)
            return
        self.recent.extend(items)
        logger.info("Recent dictations: %s refilled from today's daily log", len(items))

    def _snippet_text(self, name: str) -> str | None:
        snippet = snippet_named(name, self.snippets.current()) if self.snippets is not None else None
        return snippet.text if snippet is not None else None

    def close(self) -> None:
        self._cancel_watchdog()
        if self.learner is not None:
            self.learner.close()
        if self.screen is not None:
            self.screen.close()
        with self._mic_lock:
            self.mic.close()
        self._post.shutdown(wait=True)

    def wait_for_background(self) -> None:
        """Block until the daily log and audio writes queued so far are done."""
        self._post.submit(lambda: None).result()

    @property
    def state(self) -> str:
        return self._state

    def health(self) -> dict[str, Any]:
        d, h = self.config.dictation, self.config.hotkeys
        return {
            "ok": True,
            "service": "gpw",
            "version": __version__,
            "pid": os.getpid(),
            "state": self._state,
            "dry_run": self.dry_run,
            "model": self.engine.model,
            "mic": self.mic.mic_name,
            "dictate_key": h.dictate,
            "cancel_key": h.cancel,
            "add_word_key": h.add_word,
            "recent_key": h.recent,
            "paste_last_key": h.paste_last,
            "dictionary_path": self.dictionary.path.as_posix() if self.dictionary else "",
            "snippets_path": self.snippets.path.as_posix() if self.snippets else "",
            "auto_learn": self.learner is not None and self.learner.available,
            "screen_names": self.screen is not None and self.screen.available,
            "tap_ms": h.tap_ms,
            "double_tap_ms": h.double_tap_ms,
            "max_seconds": int(d.max_seconds),
            "sounds": d.sounds,
            "paste_restore_ms": d.paste_restore_ms,
            "assets_dir": self.assets_dir.as_posix(),
            "notes_dir": self.config.notes_dir.as_posix(),
            "logs_dir": self.config.logs_dir.as_posix(),
        }

    # -- recording --
    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._state == "processing":
                raise ServiceBusy("Still transcribing the last dictation.")
            if self._state == "recording":
                logger.warning("Dictation id=%s: new start while recording; dropped the old recording", self._id)
                self._discard_locked()
            started = time.perf_counter()
            # Read the focused window's names while the mic records, so release does not wait for it.
            self._screen_read = self.screen.begin() if self.screen is not None else None
            try:
                self._open_mic()
            except Exception:
                self._screen_read = None
                raise
            self._id += 1
            self._state = "recording"
            self._session = {
                "id": self._id,
                "ts": self.now().isoformat(timespec="seconds"),
                "model": self.engine.model,
                "mic_open": _r(time.perf_counter() - started),
            }
            dictation_id = self._id
            self._arm_watchdog(dictation_id)
        logger.info("Dictation id=%s recording mic=%s mic_open=%.3fs", dictation_id, self.mic.mic_name, self._session["mic_open"])
        self._maybe_prewarm()
        return {"id": dictation_id, "mic": self.mic.mic_name}

    def _open_mic(self) -> None:
        with self._mic_lock:
            if time.monotonic() - self._last_refresh > DEVICE_REFRESH_SECONDS:
                self._refresh_devices_locked()
            try:
                self.mic.start()
                return
            except MicTimeout:
                raise
            except (MicError, ValueError) as exc:
                # A mic plugged in or switched after the last device scan: rescan once and retry.
                logger.warning("Mic open failed (%s); rescanning devices and retrying", exc)
                self._refresh_devices_locked()
            try:
                self.mic.start()
            except ValueError as exc:  # no mic matches recording.input_device
                raise MicError(str(exc)) from exc

    def _refresh_devices_locked(self) -> None:
        self.mic.refresh_devices()
        self._last_refresh = time.monotonic()

    def cancel(self) -> str:
        with self._lock:
            if self._state == "recording":
                self._discard_locked()
                logger.info("Dictation id=%s cancelled while recording", self._id)
                return "cancelled"
            if self._state == "processing":
                self._cancelled.add(self._id)
                logger.info("Dictation id=%s cancelled while transcribing", self._id)
                return "cancelled"
            return "idle"

    def _discard_locked(self) -> None:
        self._cancel_watchdog()
        self._screen_read = None
        with self._mic_lock:
            self.mic.cancel()
        self._state = "idle"

    def _arm_watchdog(self, dictation_id: int) -> None:
        # If the hotkey script dies mid-recording, close the mic instead of listening forever.
        self._cancel_watchdog()
        self._watchdog = threading.Timer(self.config.dictation.max_seconds + 30, self._watchdog_fired, args=(dictation_id,))
        self._watchdog.daemon = True
        self._watchdog.start()

    def _cancel_watchdog(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None

    def _watchdog_fired(self, dictation_id: int) -> None:
        with self._lock:
            if self._state == "recording" and self._id == dictation_id:
                self._discard_locked()
                logger.warning(
                    "Dictation id=%s: no stop came within the hands-free limit; dropped it. Is the hotkey script running?",
                    dictation_id,
                )

    def _maybe_prewarm(self) -> None:
        if not self.config.dictation.prewarm_connection or self.dry_run:
            return
        if time.monotonic() - self._last_api < PREWARM_IDLE_SECONDS:
            return
        self._last_api = time.monotonic()
        threading.Thread(target=self._prewarm, name="gpw-prewarm", daemon=True).start()

    def _prewarm(self) -> None:
        started = time.perf_counter()
        try:
            self.engine.prewarm()
            logger.info("Prewarmed OpenAI connection took=%.2fs", time.perf_counter() - started)
        except Exception as exc:
            logger.info("Prewarm failed (%s); the upload opens its own connection", type(exc).__name__)

    # -- stop and transcribe --
    def stop(self, mode: str = "hold") -> StopResult:
        released = time.perf_counter()
        with self._lock:
            if self._state != "recording":
                return StopResult("error", self._id, message="Not recording.")
            self._cancel_watchdog()
            self._state = "processing"
            dictation_id, session = self._id, dict(self._session)
            screen_read, self._screen_read = self._screen_read, None
        try:
            result = self._finish(dictation_id, session, mode, released, screen_read)
        except Exception as exc:
            logger.exception("Dictation id=%s failed", dictation_id)
            result = StopResult("error", dictation_id, message=f"Dictation failed ({type(exc).__name__}).")
        with self._lock:
            if dictation_id in self._cancelled:
                self._cancelled.discard(dictation_id)
                self._pending.pop(dictation_id, None)
                self.recent.remove(dictation_id)
                result = StopResult("cancelled", dictation_id)
            self._state = "idle"
        return result

    def _finish(
        self, dictation_id: int, record: dict[str, Any], mode: str, released: float, screen_read: Any = None
    ) -> StopResult:
        d = self.config.dictation
        with self._mic_lock:
            raw = self.mic.stop()
        rate = self.mic.sample_rate
        stopped = time.perf_counter()
        record.update(mode=mode, record_end=_r(stopped - released))
        if self.mic.capped:
            logger.warning("Dictation id=%s hit the %.0f-minute limit; later audio was dropped", dictation_id, d.hands_free_max_minutes)

        clip = prepare_clip(
            raw, rate, d.audio_prep, d.silence_peak_dbfs, d.min_speech_ms,
            strong=lambda x, sr: ffmpeg_filter(x, sr, "strong", self.config.work_dir),
        )
        prepped = time.perf_counter()
        record.update(
            audio=_r(clip.raw_seconds), sent=_r(clip.seconds), peak_dbfs=clip.peak_dbfs,
            speech_ms=clip.speech_ms, prep=_r(prepped - stopped),
        )
        if clip.skip_reason:
            return self._skipped(dictation_id, record, clip.skip_reason)

        data = encode(clip.audio, clip.sample_rate, d.upload_format)
        encoded = time.perf_counter()
        record.update(encode=_r(encoded - prepped), upload_kb=round(len(data) / 1024))
        screen = self._screen_result(screen_read, released, record)
        keywords = self.dictionary.keywords() if self.dictionary else self.keywords
        if screen is not None and screen.names.names:
            limit = self.dictionary.max_keywords if self.dictionary else self.config.dictionary.max_keywords
            keywords = merge_keywords(clean_keywords(screen.names.names), keywords, limit)
        record["keywords"] = len(keywords)
        note = ""

        def transcribe(words: list[str]) -> TimedText:
            return self.engine.transcribe_clip(
                f"dictation.{d.upload_format}", data, words, self.config.languages, timeout=30 + clip.seconds,
            )

        try:
            try:
                timed = transcribe(keywords)
            except Exception as exc:
                if type(exc).__name__ != "BadRequestError" or not keywords:
                    raise
                # The keyword cap is untested; a long dictionary must not break dictation.
                logger.warning(
                    "Dictation id=%s: OpenAI refused the request with %s dictionary hints; retrying without them",
                    dictation_id, len(keywords),
                )
                record["keywords_dropped"] = True
                note = "OpenAI refused the dictionary hints, so this one went without them. See the log."
                timed = transcribe([])
        except Exception as exc:
            record.update(status="error", error=type(exc).__name__, api=_r(time.perf_counter() - encoded))
            logger.error(
                "Dictation id=%s transcription failed: %s status=%s after=%.2fs",
                dictation_id, type(exc).__name__, getattr(exc, "status_code", "-"), record["api"],
            )
            append_record(self.latency_log, record)
            self._post.submit(self._save_audio, dictation_id, raw, rate)
            return StopResult("error", dictation_id, message=friendly_error(exc))
        transcribed = time.perf_counter()
        self._last_api = time.monotonic()
        record.update(api=_r(timed.api_seconds), server=timed.server_seconds)

        raw_text = timed.text
        c = self.config.cleanup
        words = self.dictionary.current() if self.dictionary else None
        seen = frozenset(screen.names.words) if screen is not None else frozenset()
        near = set(screen.names.near) if screen is not None else set()
        tiers = dict(screen.names.tiers) if screen is not None else {}
        fixer = NameFix(set(seen), words.prefer if words else {}, near=near, tiers=tiers)
        snippet = self._find_snippet(raw_text, words.replace if words else {})
        if snippet is not None:
            text = snippet.text
            record.update(snippet=True, transcribed=_r(transcribed - released))
            logger.info("Dictation id=%s matched a snippet", dictation_id)
        else:
            if self.snippets is not None and self.snippets.error and spoken_name(raw_text):
                note = "snippets.yaml has an error, so this may have been a snippet. Run gpw snippets to see it."
            cleaned = clean_dictation(
                raw_text, words.replace if words else {}, terms=words.terms if words else [],
                fillers=c.remove_fillers, commands=c.spoken_commands, ai=self.clean, names=fixer,
            )
            text = cleaned.text
            self._log_names(dictation_id, screen, fixer, record)
            if cleaned.ai_seconds is not None:
                record.update(cleanup=_r(cleaned.ai_seconds), cleanup_ai=cleaned.ai_status)
                if cleaned.ai_status == "rejected":
                    logger.warning("Dictation id=%s: the text model changed words, so its cleanup was not used", dictation_id)
        if not text.strip():
            return self._skipped(dictation_id, record, "empty_transcript")

        record.update(status="ready", chars=len(text), service=_r(time.perf_counter() - released))
        logger.info(
            "Dictation id=%s ready mode=%s audio=%.1fs sent=%.1fs record_end=%.3fs prep=%.3fs encode=%.3fs "
            "upload_kb=%s api=%.2fs server=%s cleanup=%s service=%.2fs chars=%s",
            dictation_id, mode, record["audio"], record["sent"], record["record_end"], record["prep"],
            record["encode"], record["upload_kb"], record["api"],
            "-" if record["server"] is None else f"{record['server']:.2f}s",
            f"{record['cleanup']:.2f}s" if "cleanup" in record else "off",
            record["service"], record["chars"],
        )
        if self.config.debug_log_transcripts:
            logger.info("Dictation id=%s raw transcript: %s", dictation_id, raw_text)
            if text != raw_text:
                logger.info("Dictation id=%s cleaned text: %s", dictation_id, text)
        with self._lock:
            if dictation_id in self._cancelled:
                return StopResult("cancelled", dictation_id)  # Esc during transcription: no paste, no daily log
            self._pending[dictation_id] = record
            while len(self._pending) > PENDING_LIMIT:
                self._pending.popitem(last=False)
            if self.learner is not None and snippet is None:  # editing a snippet teaches nothing about speech
                self._texts[dictation_id] = (text, seen)
                while len(self._texts) > PENDING_LIMIT:
                    self._texts.popitem(last=False)
            when = datetime.fromisoformat(record["ts"])
            label = snippet_label(snippet.name) if snippet is not None else ""
            self.recent.add(RecentDictation(when, text, label=label, dictation_id=dictation_id))
        logged = label or text
        self._post.submit(self._after_success, dictation_id, raw, rate, logged, raw_text, clip.raw_seconds, when)
        return StopResult("ok", dictation_id, text=text, note=note)

    def _screen_result(self, screen_read: Any, released: float, record: dict[str, Any]) -> ScreenResult | None:
        """Names read off the focused window, if the read is done by the deadline."""
        if self.screen is None:
            return None
        waited = time.perf_counter()
        result = self.screen.collect(screen_read, released + SCREEN_DEADLINE_SECONDS)
        record.update(screen=result.status, screen_wait=_r(time.perf_counter() - waited))
        if result.app:
            record["screen_app"] = result.app
        if result.status == "ok":
            record.update(
                screen_names=result.found, screen_near=result.names.zones.get("near", 0), screen_read=_r(result.seconds)
            )
        return result

    def _log_names(self, dictation_id: int, screen: ScreenResult | None, fixer: NameFix, record: dict[str, Any]) -> None:
        """Which app was read, how many names, what changed. The names only with debug_log_transcripts."""
        if fixer.changed or fixer.ambiguous:
            record.update(names_fixed=fixer.changed, names_ambiguous=fixer.ambiguous)
        if screen is None and not fixer.changed:
            return
        logger.info(
            "Dictation id=%s screen names status=%s app=%s found=%s (%s) read=%s wait=%.3fs "
            "fixed=%s (screen=%s near=%s preferred=%s) ambiguous=%s",
            dictation_id, screen.status if screen else "off", (screen.app if screen else "") or "-",
            screen.found if screen else 0,
            " ".join(f"{zone}={count}" for zone, count in screen.names.zones.items()) if screen and screen.names.zones else "-",
            f"{screen.seconds:.3f}s" if screen and screen.status == "ok" else "-",
            record.get("screen_wait", 0.0), fixer.changed, fixer.from_screen, fixer.from_near, fixer.from_prefer,
            fixer.ambiguous,
        )
        if self.config.debug_log_transcripts:
            if screen is not None and screen.names.names:
                logger.info("Dictation id=%s screen names: %s", dictation_id, ", ".join(screen.names.names))
            if fixer.changes:
                logger.info(
                    "Dictation id=%s name spellings: %s", dictation_id, "; ".join(f"{a} -> {b}" for a, b in fixer.changes)
                )

    def _find_snippet(self, raw_text: str, replace: dict[str, str]) -> Snippet | None:
        if self.snippets is None:
            return None
        snippets = self.snippets.current()
        # Dictionary fixes first, so a misheard name still matches; then as heard, for a name spelled the way it is heard.
        return match_snippet(apply_replacements(raw_text, replace), snippets) or match_snippet(raw_text, snippets)

    def _skipped(self, dictation_id: int, record: dict[str, Any], reason: str) -> StopResult:
        record.update(status="skipped", reason=reason)
        append_record(self.latency_log, record)
        logger.info(
            "Dictation id=%s skipped reason=%s audio=%.1fs peak=%.1fdBFS speech=%sms",
            dictation_id, reason, record["audio"], record["peak_dbfs"], record["speech_ms"],
        )
        return StopResult("skipped", dictation_id, message=SKIP_MESSAGES[reason])

    # -- after the paste --
    def pasted(
        self, dictation_id: int, release_to_paste_ms: int, paste_ms: int, clipboard_restored: bool | None, app: str = ""
    ) -> bool:
        """Record what the hotkey script measured: key release to text pasted, and the program it went to."""
        if app:
            self.recent.set_app(dictation_id, app)  # shown in the recent-dictations window; never logged
        with self._lock:
            record = self._pending.pop(dictation_id, None)
        if record is None:
            logger.warning("Paste report for unknown dictation id=%s", dictation_id)
            return False
        record.update(status="pasted", release_to_paste=_r(release_to_paste_ms / 1000), paste=_r(paste_ms / 1000))
        if clipboard_restored is not None:
            record["clipboard_restored"] = clipboard_restored
        if record.get("snippet"):
            # The local part of a snippet. Tiny clock gaps between the script and the service can make it just below 0.
            record["transcript_to_paste"] = _r(max(0.0, record["release_to_paste"] - record["transcribed"]))
            logger.info("Dictation id=%s snippet transcript_to_paste=%.3fs", dictation_id, record["transcript_to_paste"])
        append_record(self.latency_log, record)
        logger.info(
            "Dictation id=%s pasted release_to_paste=%.2fs paste=%.3fs clipboard_restored=%s",
            dictation_id, record["release_to_paste"], record["paste"],
            "unknown" if clipboard_restored is None else str(clipboard_restored).lower(),
        )
        return True

    def watch_for_corrections(self, dictation_id: int) -> int:
        """Start auto-learn on the field the text was just pasted into. Returns the watch time in ms, or 0."""
        with self._lock:
            pasted = self._texts.pop(dictation_id, None)
        if self.learner is None or pasted is None or not self.learner.watch(dictation_id, pasted[0], seen=pasted[1]):
            return 0
        return int(self.learner.seconds * 1000)

    def take_learned(self) -> list[tuple[str, str]]:
        return self.learner.take_learned() if self.learner is not None else []

    def add_word(self, word: str) -> tuple[bool, str]:
        """The add-word hotkey: append the selected text to dictionary terms."""
        if self.dictionary is None:
            raise ValueError("There is no dictionary file.")
        added = self.dictionary.add_term(word)
        count = len(self.dictionary.current().terms)
        logger.info("Dictionary: add-word %s (%s terms)", "added a term" if added else "found it already there", count)
        term = " ".join(word.split())
        return added, (f'Added "{term}" to the dictionary.' if added else f'"{term}" is already in the dictionary.')

    def _after_success(
        self, dictation_id: int, raw: np.ndarray, rate: int, text: str, raw_text: str, seconds: float, when: datetime
    ) -> None:
        if self.config.dictation.daily_log:
            try:
                path = append_daily_dictation(self.config.notes_dir, when, text, raw_text, seconds)
                logger.info("Dictation id=%s added to daily log %s", dictation_id, path.name)
            except OSError as exc:
                logger.error("Dictation id=%s could not write the daily log: %s", dictation_id, exc)
        if not self.config.delete_audio_after_success:
            self._save_audio(dictation_id, raw, rate)

    def _save_audio(self, dictation_id: int, raw: np.ndarray, rate: int) -> None:
        try:
            self.config.audio_dir.mkdir(parents=True, exist_ok=True)
            path = self.config.audio_dir / f"dictation_{self.now():%Y-%m-%d_%H-%M-%S}_{dictation_id}.flac"
            sf.write(str(path), raw, rate, format="FLAC", subtype="PCM_16")
            logger.info("Dictation id=%s audio saved: %s", dictation_id, path)
        except Exception as exc:
            logger.error("Dictation id=%s could not save audio: %s", dictation_id, exc)
