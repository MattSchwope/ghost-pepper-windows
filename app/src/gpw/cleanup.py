"""Light cleanup for dictation. Word choice is never changed.

Rules (local, well under a millisecond), in this order:
  1. dictionary fixes (dictionary.yaml replace:)
  2. filler removal: um, uh, erm
  3. optional text-model pass (cleanup.ai_pass) for punctuation and false starts.
     words_kept() throws its answer away if it changed, added or reordered a word.
  4. spoken commands: "new line", "new paragraph"
  5. after a text-model pass, dictionary spellings again, in case it re-cased a name
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable

from .dictionary import apply_replacements

logger = logging.getLogger(__name__)

_EDGE = r"\w'’-"
FILLER = r"(?:[Uu](?:m+|h+|hm)|[Ee]rm)"  # "UM" stays: it could be an acronym. "er" stays: "the ER".
_FILLER_RE = re.compile(
    rf"(?P<lead>[,;][ \t]*)?(?<![{_EDGE}])(?P<word>{FILLER})(?![{_EDGE}])(?:[,;]|\.\.\.|…)?[ \t]*"
)
_FILLER_WORD = re.compile(rf"(?i){FILLER}")

_COMMAND_RE = re.compile(
    rf"[ \t]*(?<![{_EDGE}])(?P<cmd>new[ \t]+paragraph|new[ \t]*line)(?![{_EDGE}])(?![ \t]+of\b)[ \t]*[,.;:!?]*[ \t]*",
    re.IGNORECASE,
)
# "a new line", "the new line manager": the words, not the command.
_NOT_A_COMMAND_AFTER = {
    "a", "an", "the", "this", "that", "these", "those", "our", "their", "his", "her", "my", "your", "its",
    "another", "each", "every", "one", "any", "no", "whole", "entire", "same",
}
_NEGATIONS = {"not", "no", "never", "nor", "none", "nothing", "nobody", "neither", "cannot", "without"}


def _capitalize_at(text: str, positions: list[int]) -> str:
    chars = list(text)
    for pos in positions:
        i = pos
        while i < len(chars) and chars[i] in " \t\"'“‘(":
            i += 1
        if i < len(chars) and chars[i].islower():
            chars[i] = chars[i].upper()
    return "".join(chars)


def _sentence_start(prior: str) -> bool:
    prior = prior.rstrip(" \t")
    return not prior or prior[-1] in ".?!\n"


def remove_fillers(text: str) -> str:
    out = ""
    pos = 0
    capitalize: list[int] = []
    for m in _FILLER_RE.finditer(text):
        out += text[pos : m.start()]
        pos = m.end()
        rest = text[pos:].lstrip(" \t")
        if not rest or rest[0] in ".?!\n":
            out = out.rstrip(" \t")  # "We need, uh." -> "We need."
            continue
        if _sentence_start(out):
            out = out.rstrip(" \t") + (" " if out.strip() else "")
            capitalize.append(len(out))
        elif m.group("lead"):
            out += m.group("lead")  # "So, um, I think" -> "So, I think"
    out += text[pos:]
    out = _capitalize_at(out, capitalize)
    out = re.sub(r"[ \t]{2,}", " ", out).strip(" \t")
    return out if re.search(r"\w", out) else ""


def apply_spoken_commands(text: str) -> str:
    out = ""
    pos = 0
    capitalize: list[int] = []
    for m in _COMMAND_RE.finditer(text):
        before = text[pos : m.start()]
        previous = re.search(r"([\w'’-]+)[ \t]*$", out + before)
        if previous and previous.group(1).casefold() in _NOT_A_COMMAND_AFTER:
            out += before + m.group(0)
            pos = m.end()
            continue
        out += before
        pos = m.end()
        paragraph = "paragraph" in m.group("cmd").lower()
        if paragraph or _sentence_start(out):
            capitalize.append(len(out) + (2 if paragraph else 1))
        out += "\n\n" if paragraph else "\n"
    out += text[pos:]
    return _capitalize_at(out, capitalize)


def _words(text: str) -> list[str]:
    return [w.casefold().replace("’", "'") for w in re.findall(r"[\w'’]+", text)]


MAX_FALSE_START_WORDS = 4


def words_kept(source: str, cleaned: str) -> bool:
    """True if `cleaned` only re-punctuated `source` and dropped fillers or false starts.

    Every word of `cleaned` must appear in `source` in the same order. Each run of dropped words
    must be fillers, or a false start: up to four words the speaker then restarted with the same
    opening word ("I want I want to", "the the", "we should we need"). Numbers and negations
    ("not", "don't") are never dropped.
    """
    src, out = _words(source), _words(cleaned)
    # Match from the end, so a repeated phrase drops its first, abandoned copy.
    kept = [False] * len(src)
    i = len(src) - 1
    for word in reversed(out):
        while i >= 0 and src[i] != word:
            i -= 1
        if i < 0:
            return False
        kept[i] = True
        i -= 1
    start = 0
    while start < len(src):
        if kept[start]:
            start += 1
            continue
        end = start
        while end < len(src) and not kept[end]:
            end += 1
        run = [w for w in src[start:end] if not _FILLER_WORD.fullmatch(w)]
        if run:
            if any(w in _NEGATIONS or w.endswith("n't") or any(c.isdigit() for c in w) for w in run):
                return False
            if len(run) > MAX_FALSE_START_WORDS or end >= len(src) or src[end] != run[0]:
                return False
        start = end
    return True


@dataclass(frozen=True)
class CleanupResult:
    text: str
    ai_seconds: float | None = None
    ai_status: str = "off"  # off | used | unchanged | rejected | failed


def keep_term_case(text: str, terms: list[str], replace: dict[str, str]) -> str:
    """Put dictionary spellings back after the text model, which may re-case a name (Kavaliro -> kavaliro)."""
    return apply_replacements(text, {phrase: phrase for phrase in [*terms, *replace.values()] if phrase.strip()})


def clean_dictation(
    raw: str,
    replace: dict[str, str],
    *,
    terms: list[str] | None = None,
    fillers: bool = True,
    commands: bool = True,
    ai: Callable[[str], str] | None = None,
) -> CleanupResult:
    text = apply_replacements(raw, replace)
    if fillers:
        text = remove_fillers(text)
    ai_seconds, ai_status = None, "off"
    if ai is not None and text.strip():
        started = time.perf_counter()
        try:
            candidate = (ai(text) or "").strip()
            if not candidate or candidate == text:
                ai_status = "unchanged"
            elif words_kept(text, candidate):
                text, ai_status = candidate, "used"
            else:
                ai_status = "rejected"
        except Exception as exc:
            logger.error("Text-model cleanup failed (%s); kept the rule-based text", type(exc).__name__)
            ai_status = "failed"
        ai_seconds = time.perf_counter() - started
    if commands:
        text = apply_spoken_commands(text)
    if ai_status == "used":
        text = keep_term_case(text, terms or [], replace)
    return CleanupResult(text=text, ai_seconds=ai_seconds, ai_status=ai_status)
