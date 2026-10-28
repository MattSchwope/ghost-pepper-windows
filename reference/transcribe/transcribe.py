"""
Transcribe presentation recordings with OpenAI GPT-Transcribe (default) or local Whisper.

    python transcribe.py                         every recording in recordings/
    python transcribe.py talk.m4a --clean strong very noisy or distant audio
    python transcribe.py --dry-run               show chunks and cost, no API calls
    python transcribe.py --engine local          stays on this computer

See README.md for setup.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import audio

ROOT = Path(__file__).resolve().parent
AUDIO_EXTS = {
    ".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".wma",
    ".amr", ".3gp", ".mp4", ".mov", ".webm", ".mkv",
}


# ---------- helpers ----------

def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def collect_files(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in inputs:
        p = Path(raw).expanduser()
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in AUDIO_EXTS)
        elif p.is_file():
            files.append(p)
        else:
            print(f"Skipping (not found): {p}")
    return files


def read_terms(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def read_context(recording: Path, global_context: str | None) -> str | None:
    """Global --context plus an optional sidecar file: talk.m4a -> talk.context.txt"""
    sidecar = recording.with_name(f"{recording.stem}.context.txt")
    parts = [global_context or ""]
    if sidecar.is_file():
        parts.append(sidecar.read_text(encoding="utf-8"))
    text = " ".join(" ".join(parts).split())
    return text[:600] or None


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


def work_dir_for(out_dir: Path, recording: Path, args) -> Path:
    key = f"{recording.name}-{args.engine}-{args.model}-{args.clean}-{args.chunk_minutes}m"
    return out_dir / ".work" / re.sub(r"[^\w.-]+", "_", key)


def header(recording: Path, args, duration: float) -> str:
    return (
        f"Transcript: {recording.name}\n"
        f"Engine: {args.model}  |  Cleanup: {args.clean}  |  Length: {fmt_ts(duration)}\n"
        f"Generated: {datetime.now():%Y-%m-%d %H:%M}\n\n"
    )


# ---------- per-engine runs ----------

def run_openai(engine, recording: Path, wav: Path, work: Path, duration: float,
               context: str | None, keywords: list[str], languages: list[str], args) -> str | None:
    ff = audio.find_ffmpeg()
    silences = audio.detect_silences(ff, recording)
    cuts = audio.plan_cuts(duration, silences, args.chunk_minutes * 60)
    spans = list(zip(cuts[:-1], cuts[1:]))

    from engines import estimate_cost

    cost = estimate_cost(args.model, duration / 60)
    cost_txt = f", est. ${cost:.2f}" if cost is not None else ""
    print(f"  {fmt_ts(duration)} long, {len(spans)} chunk(s){cost_txt}")
    if args.dry_run:
        for i, (a, b) in enumerate(spans, 1):
            print(f"    chunk {i}: {fmt_ts(a)} - {fmt_ts(b)}")
        return None

    texts: list[str] = []
    for i, (start, end) in enumerate(spans):
        cache = work / f"chunk_{i:03d}.txt"
        label = f"  chunk {i + 1}/{len(spans)} ({fmt_ts(start)} - {fmt_ts(end)})"
        if cache.is_file():
            text = cache.read_text(encoding="utf-8")
            print(f"{label} cached")
        else:
            print(f"{label} ...", end="", flush=True)
            mp3 = work / f"chunk_{i:03d}.mp3"
            audio.export_chunk(ff, wav, start, end, mp3)
            prompt = build_prompt(context, texts[-1] if texts else "")
            text = engine.transcribe(mp3, prompt, keywords, languages)
            cache.write_text(text, encoding="utf-8")
            print(" done")
        texts.append(text)

    body = ""
    for (start, _end), text in zip(spans, texts):
        body += f"[{fmt_ts(start)}]\n" + "\n\n".join(paragraphs(text)) + "\n\n"
    return header(recording, args, duration) + body


def run_local(engine, recording: Path, wav: Path, duration: float,
              context: str | None, keywords: list[str], languages: list[str], args) -> str | None:
    print(f"  {fmt_ts(duration)} long, running locally")
    if args.dry_run:
        return None
    body, para, para_start = "", [], None
    for start, end, text in engine.transcribe(wav, context, keywords, languages):
        if para_start is None:
            para_start = start
        para.append(text)
        if end - para_start >= 60 and text.endswith((".", "?", "!")):
            body += f"[{fmt_ts(para_start)}] {' '.join(para)}\n\n"
            para, para_start = [], None
        print(f"\r  {fmt_ts(end)} / {fmt_ts(duration)}", end="", flush=True)
    if para:
        body += f"[{fmt_ts(para_start)}] {' '.join(para)}\n\n"
    print()
    return header(recording, args, duration) + body


# ---------- main ----------

def main() -> int:
    load_dotenv(ROOT / ".env")

    ap = argparse.ArgumentParser(description="Transcribe presentation recordings.")
    ap.add_argument("inputs", nargs="*", default=[str(ROOT / "recordings")],
                    help="files and/or folders (default: recordings/)")
    ap.add_argument("--out", type=Path, default=ROOT / "transcripts", help="output folder (default: transcripts/)")
    ap.add_argument("--engine", choices=["openai", "local"], default="openai")
    ap.add_argument("--model", help="default: gpt-transcribe (openai) or large-v3-turbo (local)")
    ap.add_argument("--clean", choices=list(audio.CLEAN_FILTERS), default="light",
                    help="audio cleanup before transcription (default: light)")
    ap.add_argument("--context", help='what the talk is about, e.g. "SOF Week panel on ISR contracting"')
    ap.add_argument("--keywords", default="", help='comma-separated terms, e.g. "Kavaliro, SOCOM, TS/SCI"')
    ap.add_argument("--terms", type=Path, default=ROOT / "terms.txt", help="one term per line (default: terms.txt)")
    ap.add_argument("--languages", default="en", help="expected spoken languages, comma-separated (default: en)")
    ap.add_argument("--chunk-minutes", type=int, default=10, help="target chunk length, 2-40 (default: 10)")
    ap.add_argument("--gpu", action="store_true", help="local engine only: use an NVIDIA GPU")
    ap.add_argument("--keep-audio", action="store_true", help="save the cleaned audio next to the transcript")
    ap.add_argument("--force", action="store_true", help="redo recordings that already have a transcript")
    ap.add_argument("--dry-run", action="store_true", help="clean and plan chunks, show cost, no transcription")
    args = ap.parse_args()

    if not 2 <= args.chunk_minutes <= 40:
        ap.error("--chunk-minutes must be between 2 and 40")
    args.model = args.model or ("gpt-transcribe" if args.engine == "openai" else "large-v3-turbo")

    from engines import LocalEngine, OpenAIEngine, clean_keywords

    keywords = clean_keywords(read_terms(args.terms) + args.keywords.split(","))
    languages = [x.strip() for x in args.languages.split(",") if x.strip()]

    files = collect_files(args.inputs)
    if not files:
        print("No recordings found. Put audio files in recordings/ or pass a path.")
        return 1

    engine = None
    if not args.dry_run:
        engine = OpenAIEngine(args.model) if args.engine == "openai" else LocalEngine(args.model, args.gpu)

    args.out.mkdir(parents=True, exist_ok=True)
    if keywords:
        print(f"Keywords: {', '.join(keywords)}")
    ff = audio.find_ffmpeg()
    failures = 0

    for n, recording in enumerate(files, 1):
        print(f"\n[{n}/{len(files)}] {recording.name}")
        out_txt = args.out / f"{recording.stem}.txt"
        if out_txt.exists() and not args.force and not args.dry_run:
            print(f"  already done -> {out_txt}  (use --force to redo)")
            continue

        work = work_dir_for(args.out, recording, args)
        work.mkdir(parents=True, exist_ok=True)
        started = time.time()
        try:
            wav = work / "clean.wav"
            audio.clean_to_wav(ff, recording, wav, args.clean)
            duration = audio.wav_duration(wav)
            context = read_context(recording, args.context)

            if args.engine == "openai":
                result = run_openai(engine, recording, wav, work, duration, context, keywords, languages, args)
            else:
                result = run_local(engine, recording, wav, duration, context, keywords, languages, args)

            if result is None:  # dry run
                shutil.rmtree(work, ignore_errors=True)
                continue

            out_txt.write_text(result, encoding="utf-8")
            if args.keep_audio:
                shutil.copy2(wav, args.out / f"{recording.stem}.clean.wav")
            shutil.rmtree(work, ignore_errors=True)
            print(f"  saved in {(time.time() - started) / 60:.1f} min -> {out_txt}")
        except KeyboardInterrupt:
            print("\nStopped. Re-run to resume; finished chunks are cached.")
            return 130
        except Exception as e:
            failures += 1
            print(f"\n  FAILED: {e}\n  Re-run to resume; finished chunks are cached.")

    work_root = args.out / ".work"
    if work_root.is_dir() and not any(work_root.iterdir()):
        work_root.rmdir()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
