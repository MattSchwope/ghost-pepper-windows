"""Personal dictionary (dictionary.yaml): spelling hints for GPT-Transcribe and fixes applied afterwards.

terms:    names and acronyms sent as keywords with every dictation, top of the list first
replace:  "wrong": "Right" pairs, matched case-insensitively on whole words

The file lives in the data folder, not the repo. The service re-reads it when it changes, so
edits apply to the next dictation. The add-word hotkey and auto-learn append to it without
touching the comments.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .engines import clean_keywords

logger = logging.getLogger(__name__)

MAX_TERM_CHARS = 60

TEMPLATE = """\
# Ghost Pepper personal dictionary. Edits apply to the next dictation; no restart needed.
#
# terms: names and acronyms sent to GPT-Transcribe as spelling hints with every dictation.
#   Put the ones you use most at the top. To add one fast, select it in any app and press
#   the add-word hotkey (hotkeys.add_word, Ctrl+Alt+D by default; use the left Ctrl).
#
# replace: fixes applied to the text after transcription, as "heard": "Wanted".
#   Matching ignores case and only hits whole words, so "ts sci" fixes "TS SCI"
#   but leaves "contests science" alone. Auto-learn adds lines here when you
#   correct a pasted name (dictionary.auto_learn in config.yaml).

terms:
  - Kavaliro
  - TS/SCI
  - CI Poly
  - SOCOM
  - FSP

replace:
  "cavaliro": "Kavaliro"
  "ts sci": "TS/SCI"
"""


@dataclass(frozen=True)
class Dictionary:
    terms: list[str] = field(default_factory=list)
    replace: dict[str, str] = field(default_factory=dict)


def parse_dictionary(raw: Any) -> Dictionary:
    if raw is None:
        return Dictionary()
    if not isinstance(raw, dict):
        raise ValueError("dictionary.yaml must have a terms: list and a replace: block")
    terms = raw.get("terms") or []
    if isinstance(terms, str):
        terms = [terms]
    if not isinstance(terms, list):
        raise ValueError("terms: must be a list, one term per line starting with '- '")
    replace = raw.get("replace") or {}
    if not isinstance(replace, dict):
        raise ValueError('replace: must be "heard": "Wanted" lines')
    pairs: dict[str, str] = {}
    for wrong, right in replace.items():
        if wrong is None or right is None or not str(wrong).strip():
            raise ValueError(f'replace: every line needs both sides, as "heard": "Wanted"; got {wrong!r}: {right!r}')
        pairs[str(wrong).strip()] = str(right)
    return Dictionary(terms=[str(t).strip() for t in terms if t is not None and str(t).strip()], replace=pairs)


def keywords_for(dictionary: Dictionary, extra: list[str], limit: int) -> list[str]:
    """Dictionary terms first, then transcription.keywords from config, without duplicates, capped."""
    seen: set[str] = set()
    out: list[str] = []
    for word in clean_keywords([*dictionary.terms, *extra]):
        if word.casefold() not in seen:
            seen.add(word.casefold())
            out.append(word)
    return out[:limit]


def _phrase_pattern(phrase: str) -> str:
    return r"\s+".join(re.escape(part) for part in phrase.split())


def compile_replacements(replace: dict[str, str]) -> tuple[re.Pattern[str], dict[str, str]] | None:
    """One regex for all pairs, longest first, so "ts sci" wins over "ts"."""
    if not replace:
        return None
    lookup = {" ".join(k.casefold().split()): v for k, v in replace.items()}
    keys = sorted(replace, key=len, reverse=True)
    pattern = r"(?<!\w)(?:" + "|".join(_phrase_pattern(k) for k in keys) + r")(?!\w)"
    return re.compile(pattern, re.IGNORECASE), lookup


def apply_replacements(text: str, replace: dict[str, str]) -> str:
    compiled = compile_replacements(replace)
    if compiled is None or not text:
        return text
    regex, lookup = compiled
    return regex.sub(lambda m: lookup.get(" ".join(m.group(0).casefold().split()), m.group(0)), text)


def validate_term(term: str) -> str:
    term = " ".join(term.split())
    if not term:
        raise ValueError("Nothing selected.")
    if len(term) > MAX_TERM_CHARS:
        raise ValueError(f"Too long for a dictionary term ({len(term)} characters; the limit is {MAX_TERM_CHARS}).")
    if "<" in term or ">" in term:
        raise ValueError("Terms cannot contain < or >.")
    return term


# -- editing the file without losing its comments --

def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid YAML double-quoted scalar


def _find_block(lines: list[str], key: str) -> tuple[int, int] | None:
    """Index of the top-level `key:` line and the index after the last indented line under it."""
    for i, line in enumerate(lines):
        if re.match(rf"{key}\s*:", line):
            end = i + 1
            last = i + 1
            while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t#"):
                if lines[end].strip() and lines[end][0] in " \t":
                    last = end + 1
                end += 1
            return i, last
    return None


def _insert(text: str, key: str, new_line: str) -> str:
    lines = text.splitlines()
    block = _find_block(lines, key)
    if block is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"{key}:", new_line]
    else:
        start, after = block
        head = lines[start]
        value = head.split(":", 1)[1].split("#", 1)[0].strip()
        if value:  # flow style such as `terms: [a, b]` or `replace: {}`: rewrite as a block
            existing = yaml.safe_load(value)
            if isinstance(existing, str):  # `terms: Kavaliro`
                existing = [existing]
            comment = head[len(head.split("#", 1)[0]):] if "#" in head else ""
            items = (
                [f"  - {_quoted(str(x))}" for x in existing or []]
                if key == "terms"
                else [f"  {_quoted(str(k))}: {_quoted(str(v))}" for k, v in (existing or {}).items()]
            )
            lines[start : start + 1] = [f"{key}:" + (f"  {comment}" if comment else ""), *items, new_line]
        else:
            lines.insert(after, new_line)
    return "\n".join(lines) + "\n"


def insert_term(text: str, term: str) -> str:
    return _insert(text, "terms", f"  - {_quoted(term)}")


def insert_replacement(text: str, wrong: str, right: str) -> str:
    """Add a fix, or rewrite the line that already fixes `wrong`."""
    new_line = f"  {_quoted(wrong)}: {_quoted(right)}"
    lines = text.splitlines()
    block = _find_block(lines, "replace")
    if block is not None:
        start, after = block
        key = " ".join(wrong.casefold().split())
        for i in range(start + 1, after):
            try:
                item = yaml.safe_load(lines[i])
            except yaml.YAMLError:
                continue
            if isinstance(item, dict) and len(item) == 1 and " ".join(str(next(iter(item))).casefold().split()) == key:
                lines[i] = new_line
                return "\n".join(lines) + "\n"
    return _insert(text, "replace", new_line)


class DictionaryStore:
    """dictionary.yaml on disk, re-read when it changes. Thread-safe."""

    def __init__(self, path: Path, extra_keywords: list[str] | None = None, max_keywords: int = 100) -> None:
        self.path = path
        self.extra_keywords = list(extra_keywords or [])
        self.max_keywords = max_keywords
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._dictionary = Dictionary()
        self.error = ""

    def ensure_file(self) -> bool:
        """Write the starter file if there is none. True if it was created."""
        if self.path.exists():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(TEMPLATE, encoding="utf-8")
        logger.info("Created the personal dictionary at %s", self.path)
        return True

    def current(self) -> Dictionary:
        with self._lock:
            self._reload_locked()
            return self._dictionary

    def keywords(self) -> list[str]:
        return keywords_for(self.current(), self.extra_keywords, self.max_keywords)

    def _reload_locked(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            if self._mtime is not None:
                logger.warning("Dictionary %s is gone; using no terms or fixes", self.path)
            self._mtime, self._dictionary, self.error = None, Dictionary(), ""
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            self._dictionary = parse_dictionary(yaml.safe_load(self.path.read_text(encoding="utf-8")))
            self.error = ""
            logger.info(
                "Dictionary loaded: %s terms, %s fixes from %s",
                len(self._dictionary.terms), len(self._dictionary.replace), self.path,
            )
        except (OSError, ValueError, yaml.YAMLError) as exc:
            # Keep the last good copy so a typo mid-edit does not break dictation.
            self.error = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            logger.error("Dictionary %s has an error; still using the last good copy: %s", self.path, self.error)

    def add_term(self, term: str) -> bool:
        """Append a term. False if it is already there (any case)."""
        term = validate_term(term)
        with self._lock:
            self._reload_locked()
            if term.casefold() in {t.casefold() for t in self._dictionary.terms}:
                return False
            self._edit_locked(lambda text: insert_term(text, term), lambda d: term in d.terms)
        return True

    def add_replacement(self, wrong: str, right: str) -> bool:
        """Add or update a fix. False if that exact fix is already there."""
        wrong, right = " ".join(wrong.split()).casefold(), right.strip()
        with self._lock:
            self._reload_locked()
            current = {" ".join(k.casefold().split()): v for k, v in self._dictionary.replace.items()}
            if current.get(wrong) == right:
                return False
            self._edit_locked(lambda text: insert_replacement(text, wrong, right), lambda d: d.replace.get(wrong) == right)
        return True

    def _edit_locked(self, edit, check) -> None:
        if self.error:
            raise ValueError(f"dictionary.yaml has an error, so nothing was added: {self.error}")
        self.ensure_file()
        before = self.path.read_text(encoding="utf-8")
        after = edit(before)
        try:
            ok = check(parse_dictionary(yaml.safe_load(after)))
        except (ValueError, yaml.YAMLError):
            ok = False
        if not ok:
            raise ValueError("Could not add it to dictionary.yaml without breaking the file. Add it by hand.")
        self.path.write_text(after, encoding="utf-8")
        self._mtime = None
        self._reload_locked()
