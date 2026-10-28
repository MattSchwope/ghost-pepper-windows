"""Eval harness: run clips through audio prep settings and report word error rate (WER)."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import jiwer

from .audio import AUDIO_EXTS

logger = logging.getLogger(__name__)

SOURCE_TYPES = ("dictation", "talk", "call")


@dataclass(frozen=True)
class Clip:
    audio: Path
    ref: Path

    @property
    def name(self) -> str:
        return self.audio.stem

    @property
    def source(self) -> str:
        return source_type(self.audio.name)


def source_type(filename: str) -> str:
    """Name prefixes set the source type: dictation-, talk-, call-."""
    lower = filename.lower()
    for kind in SOURCE_TYPES:
        if lower.startswith(kind + "-"):
            return kind
    return "other"


def discover_clips(clips_dir: Path) -> tuple[list[Clip], list[Path]]:
    """Return (clips with a .ref.txt, audio files missing one)."""
    clips: list[Clip] = []
    missing: list[Path] = []
    if not clips_dir.is_dir():
        return clips, missing
    taken: dict[str, Path] = {}
    for path in sorted(clips_dir.iterdir()):
        if path.suffix.lower() not in AUDIO_EXTS:
            continue
        ref = path.with_name(f"{path.stem}.ref.txt")
        if not ref.is_file():
            missing.append(path)
        elif path.stem.lower() in taken:
            # Results and cached hypotheses are keyed by name, and both files would share one reference.
            logger.warning("Skipping %s: %s already uses %s. Rename one of them.", path.name, taken[path.stem.lower()].name, ref.name)
        else:
            taken[path.stem.lower()] = path
            clips.append(Clip(path, ref))
    return clips, missing


def normalize_for_wer(text: str) -> str:
    """Lowercase, drop punctuation (keep in-word apostrophes), collapse whitespace."""
    text = text.lower().replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^\w\s']", " ", text)
    text = re.sub(r"(?<!\w)'|'(?!\w)", " ", text)
    return " ".join(text.split())


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref = normalize_for_wer(reference)
    hyp = normalize_for_wer(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return float(jiwer.wer(ref, hyp))


def parse_prep_list(raw: str, allowed: tuple[str, ...]) -> list[str]:
    levels = [x.strip() for x in raw.split(",") if x.strip()]
    if not levels:
        raise ValueError("--prep needs at least one level")
    bad = [x for x in levels if x not in allowed]
    if bad:
        raise ValueError(f"Unknown prep level(s): {', '.join(bad)}. Use: {', '.join(allowed)}")
    return list(dict.fromkeys(levels))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def format_table(clips: list[Clip], preps: list[str], results: dict[tuple[str, str], float]) -> str:
    """results maps (clip name, prep) -> WER. Adds mean rows per source type and overall."""
    rows: list[tuple[str, list[float | None]]] = [
        (clip.name, [results.get((clip.name, p)) for p in preps]) for clip in clips
    ]
    summary: list[tuple[str, list[float | None]]] = []
    for kind in (*SOURCE_TYPES, "other"):
        group = [c for c in clips if c.source == kind]
        if group:
            summary.append((f"mean {kind} ({len(group)})", [
                _mean([results[(c.name, p)] for c in group if (c.name, p) in results]) for p in preps
            ]))
    summary.append((f"mean all ({len(clips)})", [
        _mean([results[(c.name, p)] for c in clips if (c.name, p) in results]) for p in preps
    ]))

    width = max(len(name) for name, _ in rows + summary)
    width = max(width, len("clip"))

    def line(name: str, cells: list[str]) -> str:
        return f"{name:<{width}}  " + "  ".join(f"{c:>8}" for c in cells)

    def pct(v: float | None) -> str:
        return "-" if v is None else f"{v * 100:.1f}%"

    out = [line("clip", preps), "-" * (width + 10 * len(preps))]
    out += [line(name, [pct(v) for v in vals]) for name, vals in rows]
    out.append("-" * (width + 10 * len(preps)))
    out += [line(name, [pct(v) for v in vals]) for name, vals in summary]
    return "\n".join(out)


def run_eval(
    clips: list[Clip],
    preps: list[str],
    transcribe: Callable[[Path, str], str],
    results_dir: Path,
    model: str,
    fresh: bool = False,
) -> dict[tuple[str, str], float]:
    """Transcribe every clip at every prep level and score it.

    Hypotheses are cached in results_dir/<prep>/<clip>.<model>.txt so re-runs cost
    nothing and you can diff them against the reference. Failures are logged and
    left out of the table, never hidden.
    """
    results: dict[tuple[str, str], float] = {}
    safe_model = re.sub(r"[^\w.-]+", "_", model)
    for prep in preps:
        out_dir = results_dir / prep
        out_dir.mkdir(parents=True, exist_ok=True)
        for clip in clips:
            hyp_path = out_dir / f"{clip.name}.{safe_model}.txt"
            if hyp_path.is_file() and not fresh:
                hyp = hyp_path.read_text(encoding="utf-8")
            else:
                try:
                    hyp = transcribe(clip.audio, prep)
                except Exception:
                    logger.exception("Eval failed clip=%s prep=%s", clip.name, prep)
                    continue
                hyp_path.write_text(hyp, encoding="utf-8")
            results[(clip.name, prep)] = word_error_rate(clip.ref.read_text(encoding="utf-8"), hyp)
            logger.info("Eval clip=%s prep=%s wer=%.3f", clip.name, prep, results[(clip.name, prep)])
    return results
