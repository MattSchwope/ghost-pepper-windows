"""Markdown notes in the Phase 4 format: front matter, title, summary, transcript.

    ---
    type: meeting
    date: 2026-11-18
    ...
    ---
    # <Title from summary>
    ## Summary
    ...profile sections...
    ## Transcript
    [00:00:00] ...

Shared by meeting capture (Phase 2d) and the long-recording inbox (Phase 4).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TIME = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _yaml_value(value: Any) -> str:
    """One value as YAML: a list inline as [Me, Them], a scalar quoted only when YAML needs it."""
    if isinstance(value, str) and _TIME.match(value):
        return json.dumps(value)  # "14:05" and "00:47:12" read as text everywhere, as in the Phase 4 format
    if isinstance(value, (list, tuple)):
        return yaml.safe_dump(list(value), default_flow_style=True, allow_unicode=True, width=1000).strip()
    return yaml.safe_dump([value], default_flow_style=True, allow_unicode=True, width=1000).strip()[1:-1]


def front_matter(fields: dict[str, Any]) -> str:
    """YAML between --- lines, one key per line in the order given."""
    body = "".join(f"{key}: {_yaml_value(value)}\n" for key, value in fields.items())
    return f"---\n{body}---\n"


def render_note(fields: dict[str, Any], title: str, summary: str, transcript: str) -> str:
    return (
        front_matter(fields)
        + f"# {title}\n\n"
        + "## Summary\n\n"
        + (summary.strip() + "\n\n" if summary.strip() else "")
        + "## Transcript\n\n"
        + transcript.strip()
        + "\n"
    )


def file_title(title: str, max_len: int = 80) -> str:
    """A title Windows accepts in a file name, still readable: "Q4 plan: Acme / Kavaliro" -> "Q4 plan Acme Kavaliro"."""
    clean = " ".join(_UNSAFE.sub(" ", title).split()).strip(" .")
    clean = clean[:max_len].rstrip(" .")
    if not clean or clean.upper() in _RESERVED:
        return "Untitled"
    return clean


def unique_path(folder: Path, stem: str, suffix: str = ".md") -> Path:
    path = folder / f"{stem}{suffix}"
    n = 2
    while path.exists():
        path = folder / f"{stem} ({n}){suffix}"
        n += 1
    return path


def write_note(folder: Path, stem: str, text: str) -> Path:
    """Write a new note, never over an existing one. Returns where it went."""
    folder.mkdir(parents=True, exist_ok=True)
    path = unique_path(folder, stem)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)
    return path
