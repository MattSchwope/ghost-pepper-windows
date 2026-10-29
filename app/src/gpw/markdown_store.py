from __future__ import annotations

from datetime import date, datetime
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


def daily_log_path(notes_dir: Path, day: date) -> Path:
    return notes_dir / "dictation" / f"{day.isoformat()}.md"


def render_daily_header(day: date) -> str:
    return f"---\ntype: dictation-log\ndate: {day.isoformat()}\n---\n\n# Dictation {day.isoformat()}\n"


def render_daily_entry(when: datetime, text: str, raw_text: str | None = None, seconds: float | None = None) -> str:
    heading = f"## {when:%H:%M:%S}"
    if seconds is not None:
        heading += f" ({seconds:.0f} s)"
    entry = f"\n{heading}\n\n{text.strip()}\n"
    if raw_text and raw_text.strip() != text.strip():
        entry += f"\n**Raw transcript:** {raw_text.strip()}\n"
    return entry


def append_daily_dictation(
    notes_dir: Path,
    when: datetime,
    text: str,
    raw_text: str | None = None,
    seconds: float | None = None,
) -> Path:
    """Append one dictation to notes/dictation/YYYY-MM-DD.md, creating the day's file with a header."""
    path = daily_log_path(notes_dir, when.date())
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", encoding="utf-8", newline="\n") as f:
        if is_new:
            f.write(render_daily_header(when.date()))
        f.write(render_daily_entry(when, text, raw_text, seconds))
    return path
