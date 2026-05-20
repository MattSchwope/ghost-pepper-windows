from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re


def safe_slug(value: str, max_len: int = 60) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value[:max_len] or "dictation"


def render_dictation_note(
    raw_text: str,
    cleaned_text: str,
    audio_path: str | Path | None = None,
    profile: str = "general_cleanup",
) -> str:
    created = datetime.now().isoformat(timespec="seconds")
    audio_value = str(audio_path).replace("\\", "/") if audio_path else ""
    return (
        "---\n"
        "type: dictation\n"
        f"created: {created}\n"
        f"profile: {profile}\n"
        f'audio_path: "{audio_value}"\n'
        "source: local_windows_voice\n"
        "---\n\n"
        "# Cleaned Text\n\n"
        f"{cleaned_text}\n\n"
        "# Raw Transcript\n\n"
        f"{raw_text}\n"
    )


def save_dictation_note(
    notes_dir: Path,
    raw_text: str,
    cleaned_text: str,
    audio_path: str | Path | None = None,
    profile: str = "general_cleanup",
) -> Path:
    notes_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    slug_source = cleaned_text[:80] if cleaned_text else raw_text[:80]
    path = notes_dir / f"{stamp}_{safe_slug(slug_source)}.md"
    path.write_text(
        render_dictation_note(raw_text, cleaned_text, audio_path, profile),
        encoding="utf-8",
    )
    return path
