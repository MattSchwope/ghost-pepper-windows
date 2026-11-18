"""Recent dictations (Phase 2c): the last few pasted texts, kept in memory for the tray window.

A dictation that pasted into the wrong window can be copied again from the hotkey script's
recent-dictations window, or pasted again with the paste_last hotkey. The service keeps the
list in memory only. After a restart it refills from today's daily log, so nothing new is
written to disk. The text never goes into the log.
"""

from __future__ import annotations

import re
import threading
from collections import deque
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable

from .markdown_store import daily_log_path

RECENT_LIMIT = 10
APP_MAX_CHARS = 60
SNIPPET_LABEL = re.compile(r"^\[snippet: (.+)\]$")
RAW_MARKER = "\n\n**Raw transcript:** "
_HEADING = re.compile(r"^## (\d{2}):(\d{2}):(\d{2})(?: \(\d+ s\))?[ \t]*$", re.MULTILINE)
_ESCAPES = {"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"}


@dataclass(frozen=True)
class RecentDictation:
    when: datetime
    text: str  # exactly what was pasted: what the copy button puts on the clipboard
    label: str = ""  # shown instead of the text, such as "[snippet: signature]"
    app: str = ""  # the program it was pasted into, such as "OUTLOOK"; "" when unknown
    dictation_id: int = 0  # 0 for entries refilled from the daily log


def snippet_label(name: str) -> str:
    return f"[snippet: {name}]"


def clean_app_name(app: str) -> str:
    """A process name as people read it: "OUTLOOK.EXE" -> "OUTLOOK". No control characters."""
    name = "".join(ch for ch in app if ch.isprintable()).strip()
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name[:APP_MAX_CHARS]


class RecentDictations:
    """The last RECENT_LIMIT dictations, newest last. Thread-safe."""

    def __init__(self, limit: int = RECENT_LIMIT) -> None:
        self._lock = threading.Lock()
        self._items: deque[RecentDictation] = deque(maxlen=limit)

    def add(self, item: RecentDictation) -> None:
        with self._lock:
            self._items.append(item)

    def extend(self, items: Iterable[RecentDictation]) -> None:
        with self._lock:
            self._items.extend(items)

    def remove(self, dictation_id: int) -> None:
        with self._lock:
            kept = [x for x in self._items if x.dictation_id != dictation_id]
            self._items.clear()
            self._items.extend(kept)

    def set_app(self, dictation_id: int, app: str) -> bool:
        name = clean_app_name(app)
        with self._lock:
            for i, item in enumerate(self._items):
                if item.dictation_id == dictation_id and dictation_id:
                    self._items[i] = replace(item, app=name)
                    return True
        return False

    def newest_first(self) -> list[RecentDictation]:
        with self._lock:
            return list(reversed(self._items))

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


def parse_daily_log(text: str, day: date) -> list[tuple[datetime, str]]:
    """The (time, text) of each entry in a daily log, oldest first. Raw transcripts are left out.

    The format is what markdown_store.render_daily_entry writes. Hand edits are tolerated:
    Windows line breaks, and entries with no text, which are skipped.
    """
    text = text.replace("\r\n", "\n")
    headings = list(_HEADING.finditer(text))
    entries: list[tuple[datetime, str]] = []
    for i, m in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        body = text[m.end():end]
        cut = body.rfind(RAW_MARKER)
        if cut >= 0:
            body = body[:cut]
        body = body.strip()
        if not body:
            continue
        hour, minute, second = (int(x) for x in m.groups())
        if hour > 23 or minute > 59 or second > 59:
            continue
        entries.append((datetime(day.year, day.month, day.day, hour, minute, second), body))
    return entries


def from_daily_log(
    notes_dir: Path, day: date, snippet_text: Callable[[str], str | None], limit: int = RECENT_LIMIT
) -> list[RecentDictation]:
    """The last `limit` entries of that day's log, oldest first. [] if there is no log.

    The log names a snippet instead of repeating it, so its text comes from snippet_text(name);
    a snippet that has since been removed copies as its label.
    """
    path = daily_log_path(notes_dir, day)
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    items = []
    for when, text in parse_daily_log(content, day)[-limit:]:
        m = SNIPPET_LABEL.match(text)
        if m:
            items.append(RecentDictation(when, snippet_text(m.group(1)) or text, label=text))
        else:
            items.append(RecentDictation(when, text))
    return items


def escape_field(value: str) -> str:
    return "".join(_ESCAPES.get(ch, ch) for ch in value)


def render_recent(items: Iterable[RecentDictation]) -> str:
    """The /recent body for the hotkey script: one dictation per line, newest first.

    Fields are tab-separated: time (HH:MM), app, label, text. Backslash, tab, CR and LF inside a
    field are escaped as \\\\ \\t \\r \\n, so the text survives exactly, line breaks included.
    """
    lines = [
        "\t".join(escape_field(x) for x in (f"{item.when:%H:%M}", item.app, item.label, item.text))
        for item in items
    ]
    return "".join(line + "\n" for line in lines)
