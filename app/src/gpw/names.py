"""On-screen name spelling (Phase 2b): pick names out of window text and respell names in a transcript.

Pure functions, no I/O. The screen text itself never leaves extract_names(); only the names do.

How two spellings are judged the same name (sounds_alike):
  - Both in name_data.NAME_VARIANTS: only if they share a line (Sean and Shawn yes, Sean and Shane no).
  - Otherwise (most surnames): the same consonant key (name_key), a compatible first vowel sound,
    and spelling at least SIMILARITY alike (Smith and Smyth yes, Smith and Smart no).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache

from .name_data import COMMON_WORDS, variant_groups

SIMILARITY = 0.55
MAX_SCREEN_NAMES = 30  # names (one to three words each) sent as keywords per dictation
MAX_NAME_WORDS = 300  # single words kept for respelling
MAX_RUN_WORDS = 3

_VARIANTS = variant_groups()
_LETTERS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ž"
# A capitalized word with at least one lowercase letter: Kelli, McDonald, O'Brien, Anne-Marie. Not TS/SCI or SOCOM.
# Lazy, so "Kelli's" splits into the name and the possessive.
_NAME_TOKEN = re.compile(
    rf"(?<![\w@/\\])(?P<word>[A-ZÀ-ÖØ-ÞĀ-Ž][{_LETTERS}'’-]*?[a-zß-öø-ÿ][{_LETTERS}'’-]*?)(?P<poss>['’]s)?(?![\w@'’-])"
)
_GAP = re.compile(r"[ \t]{1,2}")  # what may sit between the words of one full name
_TRANSPARENT = " \t\"'“”‘’([{*•·-–—>#"


def _fold(word: str) -> str:
    """Lowercase a-z only: Zoë -> zoe, O'Brien -> obrien."""
    plain = unicodedata.normalize("NFKD", word)
    return "".join(c for c in plain.casefold() if "a" <= c <= "z")


def is_name_word(word: str) -> bool:
    return bool(_NAME_TOKEN.fullmatch(word))


def known_name(word: str) -> bool:
    return word.casefold() in _VARIANTS


def is_common(word: str) -> bool:
    return word.casefold() in COMMON_WORDS


# -- sound --

_ONSETS = (("sch", "s"), ("kn", "n"), ("gn", "n"), ("pn", "n"), ("wr", "r"), ("ps", "s"), ("wh", "w"), ("x", "s"))
_VOWELS = "aeiouy"


@lru_cache(maxsize=4096)
def name_key(word: str) -> str:
    """Consonant sounds of a name, a small Metaphone made for spelling variants.

    Merges what names swap freely: c/k/q/ch, s/z/sh/sch, f/ph/v, t/th, g/j before e and i,
    doubled letters, silent h, gh and w. Vowels are left out here (see vowel_classes).
    """
    w = _fold(word)
    for onset, sound in _ONSETS:
        if w.startswith(onset):
            w = sound + w[len(onset):]
            break
    out: list[str] = []
    i, n = 0, len(w)
    while i < n:
        c = w[i]
        nxt = w[i + 1] if i + 1 < n else ""
        after = w[i + 2] if i + 2 < n else ""
        step, sound = 1, ""
        if c in "aeiou":
            pass
        elif c == "y":
            sound = "y" if i == 0 and nxt in "aeiou" and nxt else ""
        elif c == "w":
            sound = "w" if nxt and nxt in _VOWELS else ""  # Shawn, Andrew: the w is a vowel sound
        elif c == "h":
            sound = "h" if i == 0 and nxt in _VOWELS and nxt else ""
        elif c == "s" and nxt == "c" and after == "h":
            step, sound = 3, "s"
        elif c in "sz":
            step, sound = (2 if nxt == "h" else 1), "s"
        elif c == "p" and nxt == "h":
            step, sound = 2, "f"
        elif c in "fv":
            sound = "f"
        elif c == "t" and nxt == "h":
            step, sound = 2, "t"
        elif c == "t" and nxt == "c" and after == "h":
            step, sound = 3, "k"
        elif c == "c" and nxt == "h":
            step, sound = 2, "k"
        elif c == "c":
            sound = "s" if nxt and nxt in "eiy" else "k"
        elif c in "kq":
            sound = "k"
        elif c == "x":
            sound = "ks"
        elif c == "g" and nxt == "h":
            step, sound = 2, ("g" if i == 0 or (after and after in _VOWELS) else "")
        elif c == "g":
            sound = "j" if nxt and nxt in "eiy" else "g"
        else:
            sound = c
        for s in sound:
            if not out or out[-1] != s:
                out.append(s)
        i += step
    return "".join(out)


_VOWEL_CLASSES = {
    "a": "A", "aa": "A", "ae": "AE", "ai": "AE", "ay": "AE", "au": "O", "aw": "O",
    "e": "E", "ea": "EO", "ee": "EI", "ei": "AEI", "eo": "E", "ey": "AE", "eu": "U",
    "i": "I", "ia": "I", "ie": "IE", "io": "I",
    "o": "O", "oa": "O", "oe": "O", "oo": "OU", "ou": "OU", "oi": "O", "oy": "O",
    "u": "U", "ue": "U", "ui": "UI",
    "y": "I", "ya": "I", "ye": "I",
}


def first_vowel(word: str) -> str:
    """The first vowel group: Shawn -> aw, Sean -> ea, Kelli -> e, Bryan -> ya."""
    w = _fold(word)
    i = 0
    while i < len(w) and (w[i] not in _VOWELS or (w[i] == "y" and i == 0 and len(w) > 1 and w[1] in "aeiou")):
        i += 1
    j = i
    while j < len(w) and w[j] in _VOWELS:
        j += 1
    group = w[i:j]
    if j < len(w) and w[j] == "w" and (j + 1 >= len(w) or w[j + 1] not in _VOWELS):
        group += "w"
    return group


def vowel_classes(word: str) -> frozenset[str]:
    group = first_vowel(word)
    if not group:
        return frozenset()
    classes = _VOWEL_CLASSES.get(group[:2]) or _VOWEL_CLASSES.get(group[:1], "")
    return frozenset(classes)


def spelling_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _fold(a), _fold(b)).ratio()


@lru_cache(maxsize=8192)
def sounds_alike(a: str, b: str) -> bool:
    """Are a and b spellings of the same name? Case and accents are ignored."""
    fa, fb = _fold(a), _fold(b)
    if not fa or not fb:
        return False
    if fa == fb:
        return True
    ga, gb = _VARIANTS.get(a.casefold()), _VARIANTS.get(b.casefold())
    if ga and gb:
        return bool(ga & gb)
    if name_key(a) != name_key(b):
        return False
    va, vb = vowel_classes(a), vowel_classes(b)
    if va and vb and not va & vb:
        return False
    return spelling_similarity(a, b) >= SIMILARITY


# -- reading names off the screen --

def _at_sentence_start(text: str, pos: int) -> bool:
    i = pos - 1
    while i >= 0 and text[i] in _TRANSPARENT:
        i -= 1
    return i < 0 or text[i] in ".!?:\n\r  "


@dataclass
class ScreenNames:
    """What one read of the screen yields. names go to GPT-Transcribe; words drive the respelling."""

    names: list[str] = field(default_factory=list)
    words: set[str] = field(default_factory=set)

    def __len__(self) -> int:
        return len(self.names)


def _runs(text: str) -> list[tuple[list[str], bool]]:
    """Runs of capitalized words on one line: (words, starts a sentence or line)."""
    runs: list[tuple[list[str], bool]] = []
    current: list[str] = []
    start = False
    last_end = -1
    for m in _NAME_TOKEN.finditer(text):
        word = m.group("word").strip("'’-")
        if len(word) < 2 or len(word) > 30:
            if current:
                runs.append((current, start))
            current, last_end = [], -1
            continue
        joined = current and last_end >= 0 and _GAP.fullmatch(text[last_end : m.start()]) is not None
        if joined and len(current) < MAX_RUN_WORDS:
            current.append(word)
        else:
            if current:
                runs.append((current, start))
            current = [word]
            start = _at_sentence_start(text, m.start())
        last_end = m.end() if not m.group("poss") else -1  # "Kelli's report": the run ends at the possessive
        if m.group("poss") and current:
            runs.append((current, start))
            current = []
    if current:
        runs.append((current, start))
    return runs


def extract_names(text: str, max_names: int = MAX_SCREEN_NAMES) -> ScreenNames:
    """Likely names in window text: capitalized words and runs of up to three, minus common words.

    A word at the start of a sentence or line counts only if it is a known first name, is part of
    a full name with another uncommon word, or also shows up capitalized mid-sentence. Words that
    also appear in lowercase elsewhere on screen are words, not names, unless they are known names.
    Most frequent first, so the To line and the thread beat a one-off mention.
    """
    if not text:
        return ScreenNames()
    lower_words = {w for w in re.findall(r"\b[a-zß-öø-ÿ][\w'’-]*\b", text)}
    runs = _runs(text)
    mid_sentence = {w for words, start in runs for i, w in enumerate(words) if i > 0 or not start}

    counts: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    words_out: Counter[str] = Counter()

    def keep_word(word: str, at_start: bool, in_full_name: bool) -> bool:
        if is_common(word):
            return False
        if known_name(word):
            return True
        if word.casefold() in lower_words and not in_full_name:
            return False
        return not at_start or in_full_name or word in mid_sentence

    for words, start in runs:
        # "Hi Kelli", "Kelli Smith Reply": greetings and labels glued to a name are not part of it.
        # A common word that is also a first name stays: "Mark Jones", "Grace Hopper".
        while len(words) > 1 and is_common(words[0]) and not known_name(words[0]):
            words, start = words[1:], False
        while len(words) > 1 and is_common(words[-1]) and not known_name(words[-1]):
            words = words[:-1]
        uncommon = [w for w in words if not is_common(w)]
        full_name = len(words) > 1 and len(uncommon) >= 1 and (len(uncommon) >= 2 or any(known_name(w) for w in words) or not start)
        kept = [w for i, w in enumerate(words) if keep_word(w, start and i == 0, full_name)]
        if not kept:
            continue
        if full_name and len(words) <= MAX_RUN_WORDS:
            name = " ".join(words)
            parts = words
        else:
            name, parts = None, kept
        for w in parts:
            if not is_common(w) or full_name:
                words_out[w] += 1
        for candidate in [name] if name else kept:
            counts[candidate] += 1
            first_seen.setdefault(candidate, len(first_seen))

    # A full name also counts the times its first or last name shows up alone ("Kelli Smith" ... "Kelli").
    score = {n: counts[n] + sum(counts[w] for w in n.split() if w != n) for n in counts}
    ranked = sorted(counts, key=lambda n: (-score[n], first_seen[n]))
    # A single word already inside a kept full name adds nothing as a keyword.
    names: list[str] = []
    for name in ranked:
        if " " not in name and any(name in other.split() for other in ranked if " " in other):
            continue
        names.append(name)
    top_words = [w for w, _ in words_out.most_common(MAX_NAME_WORDS)]
    return ScreenNames(names=names[:max_names], words=set(top_words))


def merge_keywords(first: list[str], then: list[str], limit: int) -> list[str]:
    """Screen names first, then dictionary keywords, without repeats (any case), at most `limit`."""
    seen: set[str] = set()
    out: list[str] = []
    for word in [*first, *then]:
        key = word.casefold()
        if key not in seen:
            seen.add(key)
            out.append(word)
    return out[:limit]


# -- respelling names in the transcript --

@dataclass
class NameFix:
    """Respells capitalized names: the screen first, then the dictionary's prefer: list.

    Call it on text; it counts what it did. Only capitalized words change, never "mark the date".
    """

    screen_words: set[str] = field(default_factory=set)
    prefer: dict[str, str] = field(default_factory=dict)
    from_screen: int = 0
    from_prefer: int = 0
    ambiguous: int = 0
    changes: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._prefer = {" ".join(k.casefold().split()): v.strip() for k, v in self.prefer.items() if v and v.strip()}
        self._exact = {w.casefold() for w in self.screen_words}

    @property
    def changed(self) -> int:
        return self.from_screen + self.from_prefer

    def spelling(self, word: str, at_sentence_start: bool) -> str | None:
        """The spelling to use for one capitalized transcript word, or None to leave it."""
        if len(_fold(word)) < 2 or (at_sentence_start and is_common(word)):
            return None
        if word in self.screen_words:
            return None
        matches = {s for s in self.screen_words if sounds_alike(word, s)}
        spellings = {s.casefold() for s in matches}
        if len(spellings) == 1:
            choice = next(iter(matches))
            if len(matches) > 1:  # Kelli and KELLI: take the one with lowercase letters
                choice = sorted(matches, key=lambda s: (s.isupper(), s))[0]
            self.from_screen += choice != word
            return choice if choice != word else None
        if len(spellings) > 1:
            self.ambiguous += 1
            return None
        if word.casefold() in self._exact:
            return None
        preferred = self._prefer.get(word.casefold())
        if preferred and preferred != word and is_name_word(preferred):
            self.from_prefer += 1
            return preferred
        return None

    def __call__(self, text: str) -> str:
        if not text or (not self.screen_words and not self._prefer):
            return text

        def swap(m: re.Match[str]) -> str:
            raw = m.group("word")
            word = raw.rstrip("'’-")  # 'Kelli': the closing quote is not part of the name
            if word.isupper():
                return m.group(0)
            new = self.spelling(word, _at_sentence_start(text, m.start()))
            if new is None:
                return m.group(0)
            self.changes.append((word, new))
            return new + raw[len(word):] + (m.group("poss") or "")

        return _NAME_TOKEN.sub(swap, text)


# -- dictionary checks --

def looks_like_name_variant(wrong: str, right: str) -> bool:
    """A replace: entry that picks one spelling of a name (kelly -> Kelli). Those belong in prefer:."""
    wrong, right = wrong.strip(), right.strip()
    if " " in wrong or " " in right or not is_name_word(right):
        return False
    if wrong.casefold() == right.casefold():
        return False
    return (known_name(wrong) or known_name(right)) and sounds_alike(wrong, right)


def adds_words(wrong: str, right: str) -> bool:
    """A replace: entry that keeps the heard words and adds more ("clearance" -> "clearance TS/SCI")."""
    heard = [w.casefold() for w in re.findall(r"[\w/'’-]+", wrong)]
    wanted = [w.casefold() for w in re.findall(r"[\w/'’-]+", right)]
    if not heard or len(wanted) <= len(heard):
        return False
    it = iter(wanted)
    return all(any(w == x for x in it) for w in heard)
