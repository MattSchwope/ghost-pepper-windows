"""Snippets (snippets.yaml): canned text, pasted when a whole dictation is "insert <name>".

GPT-Transcribe hears the phrase like any dictation. The match itself is local, and the text goes
out exactly as written: no dictionary fixes, no cleanup, no text model. The phrase must be the
whole dictation, so "please insert signature here" stays as words and cannot fire by accident.

The file lives in the data folder, not the repo. The service re-reads it when it changes.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .cleanup import remove_fillers

logger = logging.getLogger(__name__)

TRIGGER = "insert"

TEMPLATE = """\
# Ghost Pepper snippets. Edits apply to the next dictation; no restart needed.
#
# Say "insert <name>" as the whole dictation, such as "insert signature", and the text is
# pasted exactly as written here. "Please insert signature here" stays as plain words.
#
# Names ignore case and punctuation, so "follow-up" also matches "insert follow up".
# For several lines, put | after the name and indent every line by two spaces.
# Keep to one line for short text, in quotes if it contains a colon.

signature: |
  Your Name
  Your Title
  you@example.com

# outreach: |
#   Hi,
#
#   I came across your profile and wanted to reach out.
"""


@dataclass(frozen=True)
class Snippet:
    name: str
    text: str


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold())


def parse_snippets(raw: Any) -> dict[str, Snippet]:
    """Snippets keyed by their spoken name: lowercase words, no punctuation."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("snippets.yaml must be name: text lines")
    snippets: dict[str, Snippet] = {}
    for name, text in raw.items():
        spoken = " ".join(_words(str(name))) if name is not None else ""
        if not spoken:
            raise ValueError(f"Snippet name {name!r} has no words to say")
        if isinstance(text, bool) or not isinstance(text, (str, int, float)):
            raise ValueError(f"Snippet '{name}' needs text; put the value in quotes if it is text already")
        body = str(text).rstrip("\r\n")  # a | block ends in a newline nobody means to paste
        if not body.strip():
            raise ValueError(f"Snippet '{name}' has no text")
        if spoken in snippets:
            raise ValueError(f"Snippets '{snippets[spoken].name}' and '{name}' sound the same")
        snippets[spoken] = Snippet(str(name), body)
    return snippets


def spoken_name(transcript: str) -> str | None:
    """The <name> if the whole transcript is "insert <name>". Fillers and punctuation don't count."""
    words = _words(remove_fillers(transcript))
    if len(words) < 2 or words[0] != TRIGGER:
        return None
    return " ".join(words[1:])


def match_snippet(transcript: str, snippets: dict[str, Snippet]) -> Snippet | None:
    name = spoken_name(transcript)
    return snippets.get(name) if name else None


class SnippetStore:
    """snippets.yaml on disk, re-read when it changes. Thread-safe."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._snippets: dict[str, Snippet] = {}
        self.error = ""

    def ensure_file(self) -> bool:
        """Write the starter file if there is none. True if it was created."""
        if self.path.exists():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(TEMPLATE, encoding="utf-8")
        logger.info("Created the snippets file at %s", self.path)
        return True

    def current(self) -> dict[str, Snippet]:
        with self._lock:
            self._reload_locked()
            return self._snippets

    def _reload_locked(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            if self._mtime is not None:
                logger.warning("Snippets file %s is gone; using no snippets", self.path)
            self._mtime, self._snippets, self.error = None, {}, ""
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            self._snippets = parse_snippets(yaml.safe_load(self.path.read_text(encoding="utf-8")))
            self.error = ""
            logger.info("Snippets loaded: %s from %s", len(self._snippets), self.path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            # Keep the last good copy so a typo mid-edit does not break the ones that worked.
            self.error = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            logger.error("Snippets file %s has an error; still using the last good copy: %s", self.path, self.error)
