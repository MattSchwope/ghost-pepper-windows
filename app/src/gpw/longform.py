"""File transcription: prep, pause-aware chunking, chunk cache and context carry-over.

Ported from MattSchwope/transcribe. Finished chunks are cached in a work folder,
so a failed run resumes without repeating paid API calls.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from . import audio
from .engines import estimate_cost

logger = logging.getLogger(__name__)


class Engine(Protocol):
    model: str

    def transcribe(self, path: Path, prompt: str | None, keywords: list[str], languages: list[str]) -> str: ...


@dataclass
class Transcript:
    duration: float
    segments: list[tuple[float, str]] = field(default_factory=list)  # (chunk start seconds, text)

    @property
    def text(self) -> str:
        return " ".join(t for _, t in self.segments if t).strip()

    def render(self) -> str:
        """Plain text for one chunk; timestamped paragraphs for several."""
        if len(self.segments) <= 1:
            return self.text
        return render_timestamped(self.segments)


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def build_prompt(context: str | None, previous_text: str) -> str | None:
    """Context for the chunk, plus the tail of the previous chunk for continuity."""
    parts = []
    if context:
        parts.append(context if context.endswith((".", "!", "?")) else context + ".")
    if previous_text:
        tail = " ".join(previous_text.split()[-60:])
        parts.append(f'The preceding audio ended with: "{tail}"')
    return " ".join(parts) or None


def paragraphs(text: str, per_para: int = 5) -> list[str]:
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    return [" ".join(sentences[i:i + per_para]) for i in range(0, len(sentences), per_para)]


def render_timestamped(segments: list[tuple[float, str]]) -> str:
    body = ""
    for start, text in segments:
        body += f"[{fmt_ts(start)}]\n" + "\n\n".join(paragraphs(text)) + "\n\n"
    return body.rstrip() + "\n"


def work_key(src: Path, size: int, mtime_ns: int, model: str, prep: str, chunk_minutes: int) -> str:
    """Cache key: a changed file or setting gets a fresh work folder."""
    key = f"{src.name}-{size}-{mtime_ns}-{model}-{prep}-{chunk_minutes}m"
    return re.sub(r"[^\w.-]+", "_", key)


def transcribe_file(
    engine: Engine,
    src: Path,
    work_root: Path,
    *,
    prep: str = "light",
    chunk_minutes: int = 10,
    keywords: list[str] | None = None,
    languages: list[str] | None = None,
    context: str | None = None,
    keep_work: bool = False,
) -> Transcript:
    if not src.is_file():
        raise FileNotFoundError(f"Audio file not found: {src}")
    audio.filter_chain(prep)  # fail fast on a bad level
    keywords = keywords or []
    languages = languages or []

    stat = src.stat()
    work = work_root / work_key(src, stat.st_size, stat.st_mtime_ns, engine.model, prep, chunk_minutes)
    work.mkdir(parents=True, exist_ok=True)
    ff = audio.find_ffmpeg()

    started = time.perf_counter()
    wav = work / "clean.wav"
    if not wav.is_file():
        tmp = work / "clean.tmp.wav"
        audio.clean_to_wav(ff, src, tmp, prep)
        tmp.replace(wav)
    duration = audio.wav_duration(wav)
    logger.info("Prep file=%s level=%s duration=%.1fs took=%.2fs", src.name, prep, duration, time.perf_counter() - started)

    chunk_sec = chunk_minutes * 60
    if duration > chunk_sec * 1.15:
        silences = audio.detect_silences(ff, src)
        cuts = audio.plan_cuts(duration, silences, chunk_sec)
    else:
        cuts = [0.0, duration]
    spans = list(zip(cuts[:-1], cuts[1:]))
    cost = estimate_cost(engine.model, duration / 60)
    logger.info("Plan chunks=%s est_cost=%s", len(spans), f"${cost:.3f}" if cost is not None else "unknown")

    transcript = Transcript(duration=duration)
    previous = ""
    for i, (start, end) in enumerate(spans):
        cache = work / f"chunk_{i:03d}.txt"
        if cache.is_file():
            text = cache.read_text(encoding="utf-8")
            logger.info("Chunk %s/%s cached", i + 1, len(spans))
        else:
            mp3 = work / f"chunk_{i:03d}.mp3"
            audio.export_chunk(ff, wav, start, end, mp3)
            text = engine.transcribe(mp3, build_prompt(context, previous), keywords, languages)
            cache.write_text(text, encoding="utf-8")
            logger.info("Chunk %s/%s %s-%s done", i + 1, len(spans), fmt_ts(start), fmt_ts(end))
        transcript.segments.append((start, text))
        previous = text

    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    logger.info("Transcribed file=%s total=%.2fs", src.name, time.perf_counter() - started)
    return transcript
