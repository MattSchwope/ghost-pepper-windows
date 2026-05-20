from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ghost Pepper Windows MVP")
    parser.add_argument("--config", default="app/config.example.yaml", help="Path to config YAML")

    subparsers = parser.add_subparsers(dest="command", required=True)

    rec = subparsers.add_parser("record-once", help="Record, transcribe, clean, copy, and save")
    rec.add_argument("--duration", type=int, default=None, help="Recording duration in seconds")
    rec.add_argument("--profile", default=None, help="Prompt profile name")

    audio = subparsers.add_parser("record-audio", help="Record mic audio to a local WAV file")
    audio.add_argument("--duration", type=int, default=None, help="Recording duration in seconds")

    trans = subparsers.add_parser("transcribe-file", help="Transcribe an existing audio file")
    trans.add_argument("audio_path")
    trans.add_argument("--profile", default=None, help="Prompt profile name")

    return parser


def run_record_audio(config_path: str, duration: int | None) -> int:
    from .config import ensure_directories, load_config
    from .logging_setup import configure_logging
    from .recorder import record_wav

    config = load_config(config_path)
    ensure_directories(config)
    configure_logging(config.logs_dir)

    try:
        audio_path = record_wav(
            audio_dir=config.audio_dir,
            duration_seconds=config.default_duration_seconds if duration is None else duration,
            sample_rate=config.sample_rate,
            channels=config.channels,
        )
    except Exception as exc:
        logger.exception("Audio recording failed.")
        print(f"Audio recording failed: {exc}", file=sys.stderr)
        return 1

    print(f"Saved audio: {audio_path}")
    return 0


def run_pipeline(config_path: str, audio_path: Path | None, duration: int | None, profile: str | None) -> int:
    from .cleaner import OllamaCleaner, load_prompt
    from .clipboard import copy_text
    from .config import ensure_directories, load_config
    from .logging_setup import configure_logging
    from .markdown_store import save_dictation_note
    from .recorder import record_wav
    from .transcriber import FasterWhisperTranscriber

    config = load_config(config_path)
    ensure_directories(config)
    configure_logging(config.logs_dir)

    prompt_profile = profile or config.prompt_profile
    prompt_dir = Path("app/prompts")

    if audio_path is None:
        audio_path = record_wav(
            audio_dir=config.audio_dir,
            duration_seconds=config.default_duration_seconds if duration is None else duration,
            sample_rate=config.sample_rate,
            channels=config.channels,
        )

    transcriber = FasterWhisperTranscriber(
        model_size=config.model_size,
        device=config.device,
        compute_type=config.compute_type,
    )
    raw_text = transcriber.transcribe(
        audio_path=audio_path,
        language=config.language,
        vad_filter=config.vad_filter,
    )

    if not raw_text.strip():
        logger.warning("No speech detected; nothing copied.")
        return 2

    cleaned_text = raw_text
    if config.cleanup_enabled:
        try:
            prompt_template = load_prompt(prompt_dir, prompt_profile)
            cleaner = OllamaCleaner(
                url=config.ollama_url,
                model=config.cleanup_model,
                timeout_seconds=config.timeout_seconds,
            )
            cleaned_text = cleaner.clean(raw_text, prompt_template)
        except Exception:
            logger.exception("Cleanup failed; falling back to raw transcript.")
            cleaned_text = raw_text

    copy_text(cleaned_text)
    note_path = save_dictation_note(
        notes_dir=config.notes_dir,
        raw_text=raw_text,
        cleaned_text=cleaned_text,
        audio_path=audio_path,
        profile=prompt_profile,
    )
    logger.info("Saved Markdown note: %s", note_path)
    return 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "record-once":
        raise SystemExit(run_pipeline(args.config, None, args.duration, args.profile))

    if args.command == "record-audio":
        raise SystemExit(run_record_audio(args.config, args.duration))

    if args.command == "transcribe-file":
        raise SystemExit(run_pipeline(args.config, Path(args.audio_path), None, args.profile))

    raise SystemExit(1)


if __name__ == "__main__":
    main()
