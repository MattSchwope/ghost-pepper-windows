from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG, PROMPT_DIR, REPO_ROOT, AppConfig

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gpw", description="Ghost Pepper Windows")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config YAML")

    subparsers = parser.add_subparsers(dest="command", required=True)

    rec = subparsers.add_parser("record-once", help="Record, transcribe, copy, and save")
    rec.add_argument("--duration", type=int, default=None, help="Recording duration in seconds")
    rec.add_argument("--profile", default=None, help="Prompt profile name")

    trans = subparsers.add_parser("transcribe-file", help="Transcribe an audio file and print the text")
    trans.add_argument("audio_path")
    trans.add_argument("--prep", choices=["off", "light", "strong"], default=None, help="Audio prep level")
    trans.add_argument("--context", default=None, help="One line on what the audio covers")
    trans.add_argument("--profile", default=None, help="Prompt profile name")

    ev = subparsers.add_parser("eval", help="Run eval clips through prep levels and print a WER table")
    ev.add_argument("--clips", default=str(REPO_ROOT / "eval" / "clips"), help="Folder of clips + .ref.txt files")
    ev.add_argument("--prep", default="off,light,strong", help="Comma-separated prep levels")
    ev.add_argument("--results", default=str(REPO_ROOT / "eval" / "results"), help="Where hypotheses are cached")
    ev.add_argument("--fresh", action="store_true", help="Ignore cached hypotheses and call the API again")

    return parser


def _setup(config_path: str) -> AppConfig:
    from .config import ensure_directories, load_config
    from .logging_setup import configure_logging
    from .openai_client import load_dotenv

    load_dotenv(Path.cwd() / ".env")
    load_dotenv(REPO_ROOT / ".env")
    config = load_config(config_path)
    ensure_directories(config)
    configure_logging(config.logs_dir)
    return config


def _engine(config: AppConfig):
    from .engines import OpenAIEngine
    from .openai_client import make_client

    return OpenAIEngine(make_client(config.openai), config.openai.transcribe_model)


def _cue(message: str, beep_hz: int) -> None:
    """Show and sound a recording cue. Goes to stderr so stdout stays transcript-only."""
    sys.stderr.write(message + "\n")
    sys.stderr.flush()
    try:
        import winsound

        winsound.Beep(beep_hz, 150)
    except (ImportError, RuntimeError):
        pass


def run_pipeline(
    config: AppConfig,
    audio_path: Path | None,
    duration: int | None,
    profile: str | None,
    prep: str | None = None,
    context: str | None = None,
) -> int:
    from .cleaner import clean_text, load_prompt
    from .engines import clean_keywords
    from .longform import transcribe_file
    from .markdown_store import save_dictation_note

    recorded = audio_path is None
    if recorded:
        from .recorder import record_wav

        seconds = duration or config.default_duration_seconds
        audio_path = record_wav(
            audio_dir=config.audio_dir,
            duration_seconds=seconds,
            sample_rate=config.sample_rate,
            channels=config.channels,
            device=config.input_device,
            on_start=lambda: _cue(f"\n>>> LISTENING for {seconds} seconds. Speak now...", 880),
        )
        _cue(">>> Done listening. Transcribing...\n", 440)

    engine = _engine(config)
    transcript = transcribe_file(
        engine,
        audio_path,
        config.work_dir,
        prep=prep or config.audio_prep,
        chunk_minutes=config.chunk_minutes,
        keywords=clean_keywords(config.keywords),
        languages=config.languages,
        context=context,
    )
    raw_text = transcript.render()
    if config.debug_log_transcripts:
        logger.info("Raw transcript: %s", raw_text)

    if not raw_text.strip():
        logger.warning("No speech detected; nothing to output.")
        return 2

    prompt_profile = profile or config.prompt_profile
    cleaned_text = raw_text
    if config.cleanup_enabled:
        from .openai_client import make_client
        from .text_client import OpenAITextClient

        try:
            client = OpenAITextClient(make_client(config.openai), config.openai.text_model, config.openai.text_reasoning)
            cleaned_text = clean_text(client, raw_text, load_prompt(PROMPT_DIR, prompt_profile)) or raw_text
        except Exception:
            logger.exception("Cleanup failed; falling back to raw transcript.")
            cleaned_text = raw_text

    if recorded:
        from .clipboard import copy_text

        copy_text(cleaned_text)
    else:
        sys.stdout.write(cleaned_text.rstrip() + "\n")
        sys.stdout.flush()

    if config.save_notes:
        note_path = save_dictation_note(
            notes_dir=config.notes_dir,
            raw_text=raw_text,
            cleaned_text=cleaned_text,
            audio_path=audio_path,
            profile=prompt_profile,
        )
        logger.info("Saved Markdown note: %s", note_path)

    if recorded and config.delete_audio_after_success:
        audio_path.unlink(missing_ok=True)
        logger.info("Deleted recorded audio after success")
    return 0


def run_eval_command(config: AppConfig, clips_dir: Path, prep_arg: str, results_dir: Path, fresh: bool) -> int:
    from .audio import PREP_LEVELS
    from .engines import clean_keywords
    from .evaluation import discover_clips, format_table, parse_prep_list, run_eval
    from .longform import transcribe_file

    try:
        preps = parse_prep_list(prep_arg, PREP_LEVELS)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    clips, missing = discover_clips(clips_dir)
    for path in missing:
        logger.warning("Skipping %s: no %s.ref.txt next to it", path.name, path.stem)
    if not clips:
        logger.error("No eval clips with .ref.txt files in %s. See eval/README.md.", clips_dir)
        return 1

    engine = _engine(config)
    keywords = clean_keywords(config.keywords)

    def transcribe(path: Path, prep: str) -> str:
        return transcribe_file(
            engine, path, config.work_dir,
            prep=prep, chunk_minutes=config.chunk_minutes,
            keywords=keywords, languages=config.languages,
        ).text

    logger.info("Eval clips=%s preps=%s model=%s", len(clips), ",".join(preps), engine.model)
    results = run_eval(clips, preps, transcribe, results_dir, engine.model, fresh=fresh)
    sys.stdout.write(f"\nWER by clip ({engine.model}; lower is better)\n\n")
    sys.stdout.write(format_table(clips, preps, results) + "\n")
    return 0 if len(results) == len(clips) * len(preps) else 1


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = _setup(args.config)
        if args.command == "record-once":
            code = run_pipeline(config, None, args.duration, args.profile)
        elif args.command == "transcribe-file":
            code = run_pipeline(config, Path(args.audio_path), None, args.profile, args.prep, args.context)
        elif args.command == "eval":
            code = run_eval_command(config, Path(args.clips), args.prep, Path(args.results), args.fresh)
        else:
            code = 1
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        if logging.getLogger().handlers:
            logger.error("%s", exc)
        else:
            print(f"Error: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
