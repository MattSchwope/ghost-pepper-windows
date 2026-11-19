"""Energy-based voice detection for long tracks: where the speech is, split into utterances at pauses.

Works on per-frame loudness, read a block at a time, so a four-hour track never sits in memory.
The threshold follows the track's own noise floor, so a hissy mic and a clean loopback both work.
Shared by meeting capture (Phase 2d) and the long-recording inbox (Phase 4).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

FRAME_SECONDS = 0.03
FLOOR_PERCENTILE = 10
ABOVE_FLOOR_DB = 12.0  # speech is at least this much louder than the track's quiet parts
MIN_THRESHOLD_DBFS = -50.0  # never call anything quieter than this speech
MAX_THRESHOLD_DBFS = -30.0  # a noisy floor must not hide normal speech
BRIDGE_SECONDS = 0.3  # gaps shorter than this inside speech are kept
MIN_SPEECH_SECONDS = 0.25  # louder runs shorter than this are clicks and taps
PAD_SECONDS = 0.2  # kept on each side of speech, so word edges are not clipped
PAUSE_SECONDS = 0.6  # a pause at least this long ends an utterance
MAX_UTTERANCE_SECONDS = 30.0


@dataclass(frozen=True)
class Span:
    start: float
    end: float

    @property
    def seconds(self) -> float:
        return self.end - self.start


def frame_db(blocks: Iterable[np.ndarray], sample_rate: int, frame_seconds: float = FRAME_SECONDS) -> np.ndarray:
    """RMS loudness in dBFS per frame. Blocks may be any length; leftovers carry to the next block."""
    n = int(sample_rate * frame_seconds)
    out: list[np.ndarray] = []
    carry = np.zeros(0, dtype=np.float32)
    for block in blocks:
        data = np.concatenate([carry, np.asarray(block, dtype=np.float32)])
        whole = len(data) // n * n
        if whole:
            frames = data[:whole].reshape(-1, n).astype(np.float64)
            rms = np.sqrt(np.mean(frames * frames, axis=1))
            out.append(20 * np.log10(np.maximum(rms, 1e-6)))
        carry = data[whole:]
    if len(carry):
        rms = float(np.sqrt(np.mean(carry.astype(np.float64) ** 2)))
        out.append(np.array([20 * np.log10(max(rms, 1e-6))]))
    return np.concatenate(out) if out else np.zeros(0)


def threshold_for(db: np.ndarray) -> float:
    if len(db) == 0:
        return MIN_THRESHOLD_DBFS
    floor = float(np.percentile(db, FLOOR_PERCENTILE))
    return float(min(MAX_THRESHOLD_DBFS, max(MIN_THRESHOLD_DBFS, floor + ABOVE_FLOOR_DB)))


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index runs where mask is true."""
    if not len(mask):
        return []
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist()))


def speech_spans(db: np.ndarray, frame_seconds: float = FRAME_SECONDS, threshold: float | None = None) -> list[Span]:
    """Stretches of speech, padded a little, with short gaps bridged and short blips dropped."""
    if not len(db):
        return []
    limit = threshold_for(db) if threshold is None else threshold
    runs = _runs(db > limit)
    bridge = BRIDGE_SECONDS / frame_seconds
    merged: list[list[int]] = []
    for a, b in runs:
        if merged and a - merged[-1][1] <= bridge:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    total = len(db) * frame_seconds
    spans = [
        Span(max(0.0, a * frame_seconds - PAD_SECONDS), min(total, b * frame_seconds + PAD_SECONDS))
        for a, b in merged
        if (b - a) * frame_seconds >= MIN_SPEECH_SECONDS
    ]
    out: list[Span] = []
    for span in spans:  # padding can make neighbours touch
        if out and span.start <= out[-1].end:
            out[-1] = Span(out[-1].start, span.end)
        else:
            out.append(span)
    return out


def split_long(span: Span, db: np.ndarray, frame_seconds: float = FRAME_SECONDS, limit: float = MAX_UTTERANCE_SECONDS) -> list[Span]:
    """Cut a run of speech longer than `limit` at its quietest frame in the last third of each piece."""
    out: list[Span] = []
    start = span.start
    while span.end - start > limit:
        lo = int((start + limit * 2 / 3) / frame_seconds)
        hi = max(lo + 1, int((start + limit) / frame_seconds))
        window = db[lo:hi]
        cut = (lo + int(np.argmin(window))) * frame_seconds if len(window) else start + limit
        out.append(Span(start, cut))
        start = cut
    out.append(Span(start, span.end))
    return out


def utterances(spans: list[Span], db: np.ndarray | None = None, frame_seconds: float = FRAME_SECONDS,
               pause: float = PAUSE_SECONDS, limit: float = MAX_UTTERANCE_SECONDS) -> list[Span]:
    """Join speech spans closer than `pause` into utterances, none longer than `limit`."""
    joined: list[Span] = []
    for span in spans:
        if joined and span.start - joined[-1].end < pause and span.end - joined[-1].start <= limit:
            joined[-1] = Span(joined[-1].start, span.end)
        else:
            joined.append(span)
    out: list[Span] = []
    for span in joined:
        if span.seconds > limit and db is not None:
            out.extend(split_long(span, db, frame_seconds, limit))
        else:
            out.append(span)
    return out


def total_seconds(spans: Iterable[Span]) -> float:
    return sum(s.seconds for s in spans)
