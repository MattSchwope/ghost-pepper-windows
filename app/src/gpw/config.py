from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .audio import PREP_LEVELS

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_ROOT / "app" / "config.example.yaml"
PROMPT_DIR = REPO_ROOT / "app" / "prompts"


@dataclass(frozen=True)
class OpenAIConfig:
    api_key_env: str = "OPENAI_API_KEY"
    transcribe_model: str = "gpt-transcribe"
    text_model: str = "gpt-6-luna"
    text_reasoning: str = "none"
    summary_model: str = "gpt-6.1-sol"
    timeout_seconds: float = 600.0
    max_retries: int = 5


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    audio_dir: Path
    notes_dir: Path
    logs_dir: Path
    sample_rate: int
    channels: int
    default_duration_seconds: int
    input_device: int | str | None
    openai: OpenAIConfig
    languages: list[str]
    audio_prep: str
    chunk_minutes: int
    keywords: list[str]
    cleanup_enabled: bool
    prompt_profile: str
    save_notes: bool
    debug_log_transcripts: bool
    delete_audio_after_success: bool

    @property
    def work_dir(self) -> Path:
        return self.data_dir / ".work"


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    return [str(x).strip() for x in value if str(x).strip()]


def parse_config(raw: dict[str, Any]) -> AppConfig:
    app = raw.get("app") or {}
    recording = raw.get("recording") or {}
    oa = raw.get("openai") or {}
    transcription = raw.get("transcription") or {}
    cleanup = raw.get("cleanup") or {}
    markdown = raw.get("markdown") or {}
    privacy = raw.get("privacy") or {}

    defaults = OpenAIConfig()
    openai_cfg = OpenAIConfig(
        api_key_env=str(oa.get("api_key_env", defaults.api_key_env)),
        transcribe_model=str(oa.get("transcribe_model", defaults.transcribe_model)),
        text_model=str(oa.get("text_model", defaults.text_model)),
        text_reasoning=str(oa.get("text_reasoning", defaults.text_reasoning)),
        summary_model=str(oa.get("summary_model", defaults.summary_model)),
        timeout_seconds=float(oa.get("timeout_seconds", defaults.timeout_seconds)),
        max_retries=int(oa.get("max_retries", defaults.max_retries)),
    )

    audio_prep = str(transcription.get("audio_prep", "light"))
    if audio_prep not in PREP_LEVELS:
        raise ValueError(f"transcription.audio_prep must be one of {', '.join(PREP_LEVELS)}; got '{audio_prep}'")
    chunk_minutes = int(transcription.get("chunk_minutes", 10))
    if not 2 <= chunk_minutes <= 40:
        raise ValueError(f"transcription.chunk_minutes must be between 2 and 40; got {chunk_minutes}")

    input_device = recording.get("input_device")
    if isinstance(input_device, bool) or not isinstance(input_device, (int, str, type(None))):
        raise ValueError(f"recording.input_device must be null, a mic name or an index; got '{input_device}'")

    data_dir = Path(app.get("data_dir", "C:/GhostPepperWindows"))
    return AppConfig(
        data_dir=data_dir,
        audio_dir=Path(app.get("audio_dir", data_dir / "audio")),
        notes_dir=Path(app.get("notes_dir", data_dir / "notes")),
        logs_dir=Path(app.get("logs_dir", data_dir / "logs")),
        sample_rate=int(recording.get("sample_rate", 16000)),
        channels=int(recording.get("channels", 1)),
        default_duration_seconds=int(recording.get("default_duration_seconds", 8)),
        input_device=input_device,
        openai=openai_cfg,
        languages=_str_list(transcription.get("languages", ["en"])),
        audio_prep=audio_prep,
        chunk_minutes=chunk_minutes,
        keywords=_str_list(transcription.get("keywords")),
        cleanup_enabled=bool(cleanup.get("enabled", False)),
        prompt_profile=str(cleanup.get("prompt_profile", "general_cleanup")),
        save_notes=bool(markdown.get("save_notes", True)),
        debug_log_transcripts=bool(privacy.get("debug_log_transcripts", False)),
        delete_audio_after_success=bool(privacy.get("delete_audio_after_success", False)),
    )


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return parse_config(raw)


def ensure_directories(config: AppConfig) -> None:
    for path in [config.data_dir, config.audio_dir, config.notes_dir, config.logs_dir]:
        path.mkdir(parents=True, exist_ok=True)
