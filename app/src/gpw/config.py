from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    audio_dir: Path
    notes_dir: Path
    logs_dir: Path
    sample_rate: int
    channels: int
    default_duration_seconds: int
    model_size: str
    device: str
    compute_type: str
    language: str | None
    vad_filter: bool
    cleanup_enabled: bool
    ollama_url: str
    cleanup_model: str
    prompt_profile: str
    timeout_seconds: int
    debug_log_transcripts: bool
    delete_audio_after_success: bool


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    app = raw.get("app", {})
    recording = raw.get("recording", {})
    transcription = raw.get("transcription", {})
    cleanup = raw.get("cleanup", {})
    privacy = raw.get("privacy", {})

    return AppConfig(
        data_dir=Path(app.get("data_dir", "C:/GhostPepperWindows")),
        audio_dir=Path(app.get("audio_dir", "C:/GhostPepperWindows/audio")),
        notes_dir=Path(app.get("notes_dir", "C:/GhostPepperWindows/notes")),
        logs_dir=Path(app.get("logs_dir", "C:/GhostPepperWindows/logs")),
        sample_rate=int(recording.get("sample_rate", 16000)),
        channels=int(recording.get("channels", 1)),
        default_duration_seconds=int(recording.get("default_duration_seconds", 8)),
        model_size=str(transcription.get("model_size", "small.en")),
        device=str(transcription.get("device", "cpu")),
        compute_type=str(transcription.get("compute_type", "int8")),
        language=transcription.get("language", "en"),
        vad_filter=bool(transcription.get("vad_filter", True)),
        cleanup_enabled=bool(cleanup.get("enabled", True)),
        ollama_url=str(cleanup.get("ollama_url", "http://localhost:11434/api/chat")),
        cleanup_model=str(cleanup.get("model", "qwen2.5:3b")),
        prompt_profile=str(cleanup.get("prompt_profile", "general_cleanup")),
        timeout_seconds=int(cleanup.get("timeout_seconds", 120)),
        debug_log_transcripts=bool(privacy.get("debug_log_transcripts", False)),
        delete_audio_after_success=bool(privacy.get("delete_audio_after_success", False)),
    )


def ensure_directories(config: AppConfig) -> None:
    for path in [config.data_dir, config.audio_dir, config.notes_dir, config.logs_dir]:
        path.mkdir(parents=True, exist_ok=True)
