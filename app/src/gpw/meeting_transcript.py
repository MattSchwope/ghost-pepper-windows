"""From two tracks to labeled lines: turns, batched GPT-Transcribe requests, echo removal.

1. Voice detection finds utterances on each track (vad.py).
2. Utterances become turns: one speaker's run of speech until the other track speaks, a long
   pause, or MAX_TURN_SECONDS. Each turn keeps its start time and track.
3. Turns go to GPT-Transcribe one request each, in batches of about `chunk_minutes` of speech.
   A turn's audio is only its speech, so silence isn't paid for. Every request in a batch carries
   the end of the previous batch as context, and every finished turn lands in the chunk cache,
   so a retry pays only for what is missing.
4. On laptop speakers the mic hears the call. A Me line that overlaps Them speech and repeats
   its words is dropped.

GPT-Transcribe has no speaker labels, so everyone on the far end is "Them".
Shared by meeting capture (Phase 2d); Phase 4's inbox uses the same cache and batching.
"""

from __future__ import annotations

import bisect
import hashlib
import logging
import re
import threading
from collections.abc import Callable
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from .longform import ChunkCache, Engine, build_prompt, fmt_ts, transcribe_chunk
from .meeting_capture import TrackFile
from .vad import Span, frame_db, speech_spans, utterances

logger = logging.getLogger(__name__)

SPEAKERS = {"me": "Me", "them": "Them"}
TURN_GAP_SECONDS = 8.0  # a pause longer than this starts a new line even if nobody else spoke
MAX_TURN_SECONDS = 120.0  # no line covers more than this, so timestamps stay useful
JOIN_SILENCE_SECONDS = 0.3  # between the utterances of one turn, in place of the real pause
REQUEST_WORKERS = 4
ECHO_SLACK_SECONDS = 1.0
ECHO_MIN_SHARE = 0.6


@dataclass(frozen=True)
class Turn:
    track: str  # "me" | "them"
    parts: tuple[Span, ...]

    @property
    def speaker(self) -> str:
        return SPEAKERS[self.track]

    @property
    def start(self) -> float:
        return self.parts[0].start

    @property
    def end(self) -> float:
        return self.parts[-1].end

    @property
    def speech_seconds(self) -> float:
        return sum(p.seconds for p in self.parts) + JOIN_SILENCE_SECONDS * (len(self.parts) - 1)

    def key(self, model: str) -> str:
        """Names this turn's audio in the cache: same track, same spans, same model."""
        spans = ",".join(f"{p.start:.3f}-{p.end:.3f}" for p in self.parts)
        digest = hashlib.sha1(spans.encode()).hexdigest()[:10]
        return f"{model}_{self.track}_{int(self.start * 1000):09d}_{digest}"


@dataclass(frozen=True)
class Line:
    speaker: str
    start: float
    end: float
    text: str


@dataclass
class TranscribeStats:
    requests: int = 0
    cached: int = 0
    keywords_dropped: bool = False
    speech_seconds: float = 0.0
    batches: int = 0


@dataclass
class TrackSpeech:
    utterances: list[Span] = field(default_factory=list)
    seconds: float = 0.0  # length of the track


def detect_speech(track: TrackFile, block_seconds: float = 60.0) -> TrackSpeech:
    """Utterances on one track, read a minute at a time."""
    db = frame_db(track.blocks(int(track.sample_rate * block_seconds)), track.sample_rate)
    return TrackSpeech(utterances(speech_spans(db), db), track.seconds)


class _Activity:
    """When a track was speaking, for asking "did it speak between these two moments?"."""

    def __init__(self, spans: list[Span]) -> None:
        ordered = sorted(spans, key=lambda s: s.start)
        self.starts = [s.start for s in ordered]
        self.max_end: list[float] = []
        for s in ordered:
            self.max_end.append(max(s.end, self.max_end[-1] if self.max_end else s.end))

    def spoke_between(self, after: float, until: float) -> bool:
        """True if any utterance starting at or before `until` was still going after `after`."""
        i = bisect.bisect_right(self.starts, until)
        return i > 0 and self.max_end[i - 1] > after


def build_turns(tracks: dict[str, list[Span]]) -> list[Turn]:
    """Merge each track's utterances into turns, in start order across both tracks. A turn never
    runs across the other track's speech, even speech that starts at the same moment, so echo
    stays in turns of its own where it can be found and dropped."""
    events = sorted((u.start, u.end, track) for track, spans in tracks.items() for u in spans)
    activity = {track: _Activity(spans) for track, spans in tracks.items()}
    turns: list[Turn] = []
    for start, end, track in events:
        last = turns[-1] if turns else None
        if (
            last is not None and last.track == track
            and start - last.end <= TURN_GAP_SECONDS
            and end - last.start <= MAX_TURN_SECONDS
            and not any(a.spoke_between(last.end, start) for t, a in activity.items() if t != track)
        ):
            turns[-1] = Turn(track, (*last.parts, Span(start, end)))
        else:
            turns.append(Turn(track, (Span(start, end),)))
    return turns


def batch_turns(turns: list[Turn], batch_seconds: float) -> list[list[int]]:
    """Consecutive turns grouped into batches of about `batch_seconds` of speech."""
    batches: list[list[int]] = []
    size = 0.0
    for i, turn in enumerate(turns):
        if not batches or size >= batch_seconds:
            batches.append([])
            size = 0.0
        batches[-1].append(i)
        size += turn.speech_seconds
    return batches


def turn_audio(turn: Turn, track: TrackFile) -> np.ndarray:
    gap = np.zeros(int(track.sample_rate * JOIN_SILENCE_SECONDS), dtype=np.float32)
    pieces: list[np.ndarray] = []
    for i, part in enumerate(turn.parts):
        if i:
            pieces.append(gap)
        pieces.append(track.read_seconds(part.start, part.end))
    return np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)


def transcribe_turns(
    turns: list[Turn],
    tracks: dict[str, TrackFile],
    engine: Engine,
    cache: ChunkCache,
    work: Path,
    *,
    keywords: list[str],
    languages: list[str],
    context: str | None = None,
    batch_seconds: float = 600.0,
    workers: int = REQUEST_WORKERS,
    on_batch: Callable[[int, int], None] | None = None,
) -> tuple[list[str], TranscribeStats]:
    """Text for every turn, from the cache where it is already there."""
    stats = TranscribeStats(speech_seconds=sum(t.speech_seconds for t in turns))
    texts: list[str] = [""] * len(turns)
    hints = {"keywords": list(keywords)}
    hints_lock = threading.Lock()
    previous = ""
    work.mkdir(parents=True, exist_ok=True)

    def one(i: int, prompt: str | None) -> str:
        turn = turns[i]
        key = turn.key(engine.model)
        path = work / f"{key}.flac"
        sf.write(str(path), turn_audio(turn, tracks[turn.track]), tracks[turn.track].sample_rate, format="FLAC", subtype="PCM_16")
        try:
            with hints_lock:
                words = hints["keywords"]
            text, dropped = transcribe_chunk(engine, path, prompt, words, languages)
            if dropped:
                with hints_lock:
                    hints["keywords"] = []  # the rest go without them too
                stats.keywords_dropped = True
        finally:
            path.unlink(missing_ok=True)
        cache.put(key, text)
        return text

    batches = batch_turns(turns, batch_seconds)
    stats.batches = len(batches)
    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="gpw-meeting-stt") as pool:
        for b, batch in enumerate(batches):
            prompt = build_prompt(context, previous)
            futures = {}
            for i in batch:
                cached = cache.get(turns[i].key(engine.model))
                if cached is not None:
                    texts[i] = cached
                    stats.cached += 1
                else:
                    futures[pool.submit(one, i, prompt)] = i
            stats.requests += len(futures)
            done, pending = wait(futures, return_when=FIRST_EXCEPTION)
            for future in pending:
                future.cancel()
            for future in done:
                if future.exception() is not None:
                    wait(pending)  # let requests already running finish into the cache
                    raise future.exception()  # type: ignore[misc]
                texts[futures[future]] = future.result()
            previous = " ".join(texts[i] for i in batch if texts[i])
            if on_batch is not None:
                on_batch(b + 1, len(batches))
    return texts, stats


# -- echo and lines --

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def is_echo(me: Line, them: list[Line], slack: float = ECHO_SLACK_SECONDS, min_share: float = ECHO_MIN_SHARE) -> bool:
    """A Me line is the call coming back through the mic when it overlaps Them speech and repeats it."""
    overlapping = [t for t in them if t.start - slack < me.end and me.start < t.end + slack]
    mine = words(me.text)
    if not overlapping or not mine:
        return False
    theirs = words(" ".join(t.text for t in overlapping))
    if len(mine) < 3:
        # "Yes" is said on both ends all the time. A short line counts only as a phrase heard inside a Them line.
        inside = any(t.start - slack <= me.start and me.end <= t.end + slack for t in overlapping)
        return inside and f" {' '.join(mine)} " in f" {' '.join(theirs)} "
    vocabulary = set(theirs)
    return sum(w in vocabulary for w in mine) / len(mine) >= min_share


def drop_echo(lines: list[Line]) -> tuple[list[Line], int]:
    them = [line for line in lines if line.speaker == SPEAKERS["them"]]
    kept = [line for line in lines if not (line.speaker == SPEAKERS["me"] and is_echo(line, them))]
    return kept, len(lines) - len(kept)


def merge_lines(lines: list[Line], gap: float = TURN_GAP_SECONDS, limit: float = MAX_TURN_SECONDS) -> list[Line]:
    """Join neighbouring lines from the same speaker, such as a Them turn an echo line had split."""
    out: list[Line] = []
    for line in lines:
        last = out[-1] if out else None
        if last is not None and last.speaker == line.speaker and line.start - last.end <= gap and line.end - last.start <= limit:
            out[-1] = Line(last.speaker, last.start, line.end, f"{last.text} {line.text}".strip())
        else:
            out.append(line)
    return out


def to_lines(turns: list[Turn], texts: list[str], fix: Callable[[str], str] = lambda s: s) -> list[Line]:
    """Turns with their text, in time order, empty ones left out. `fix` applies dictionary fixes."""
    lines = [Line(t.speaker, t.start, t.end, fix(" ".join(text.split()))) for t, text in zip(turns, texts)]
    return sorted((line for line in lines if line.text), key=lambda line: (line.start, line.speaker))


def render_lines(lines: list[Line]) -> str:
    """`[00:12:03] Me: ...` lines, a blank line between them so Markdown keeps them apart."""
    return "\n\n".join(f"[{fmt_ts(line.start)}] {line.speaker}: {line.text}" for line in lines) + ("\n" if lines else "")
