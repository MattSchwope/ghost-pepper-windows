"""On-screen name spelling (Phase 2b): pick names out of window text and respell names in a transcript.

Pure functions, no I/O. The screen text itself never leaves extract_names(); only the names do.

How two spellings are judged the same name (sounds_alike):
  - Both in name_data.NAME_VARIANTS: only if they share a line (Sean and Shawn yes, Sean and Shane no).
  - Otherwise (most surnames): the same consonant key (name_key), a compatible first vowel sound,
    and spelling at least SIMILARITY alike (Smith and Smyth yes, Smith and Smart no).

Where a name sits matters (screen.py splits the window into zones): names near the cursor (the
conversation or email being answered) go first as keywords, win when the screen shows two
spellings, and are the only ones a mumbled word may be matched to loosely (NameFix._loose).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence
from difflib import SequenceMatcher
from functools import lru_cache

from .name_data import COMMON_WORDS, variant_groups

SIMILARITY = 0.55
LIGHT_SYLLABLE_SIMILARITY = 0.7  # stricter when the first vowels differ (Lamont, Lemonte)
MAX_SCREEN_NAMES = 30  # names (one to three words each) sent as keywords per dictation
MAX_NAME_WORDS = 300  # single words kept for respelling
MAX_RUN_WORDS = 3
# Zones of a window, closest to the cursor first (see screen.py).
ZONES = ("near", "title", "page", "far")
# The loose pass for mumbled names (NameFix._loose): near-focus names only.
LOOSE_SIMILARITY = 0.6
LOOSE_KNOWN_SIMILARITY = 0.75  # stricter when the heard word is itself a known first name
LOOSE_MARGIN = 0.1  # the best candidate must beat the runner-up by this much
LOOSE_LENGTH_RATIO = 0.65  # heard and name no more than this far apart in length
# Evidence that a spelling is someone's name, from the full names it shows up in ("Jon Bruno").
# A lone word in a message body ("Hi John") is the weakest: it may be an earlier dictation gone wrong.
FULL_NAME_ELSEWHERE = 1  # in a full name somewhere on screen
FULL_NAME_NEAR = 2  # in a full name near the cursor
FULL_NAME_HEADER = 3  # in a full name in the window title, or repeated near the cursor (a header, a signature)
LOOSE_KEY_DISTANCE = 1  # consonant sounds a mumble may drop; 2 for keys longer than 4 sounds

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
        # Lamont and Lemonte, DeShawn and Dashawn: a light first syllable is said as "uh",
        # so its vowel can differ when the rest of the name matches. Sean and Shane still don't.
        return _light_first_syllables(a, b) and spelling_similarity(a, b) >= LIGHT_SYLLABLE_SIMILARITY
    return spelling_similarity(a, b) >= SIMILARITY


_LIGHT_FIRST = re.compile(r"[^aeiouy]{1,2}[aeiou](?=[^aeiouy])")


def _light_first_syllables(a: str, b: str) -> bool:
    """Do both names open with a consonant and one short vowel, then match from the next vowel on?"""
    fa, fb = _fold(a), _fold(b)
    if min(len(fa), len(fb)) < 5:
        return False
    ma, mb = _LIGHT_FIRST.match(fa), _LIGHT_FIRST.match(fb)
    if not ma or not mb:
        return False
    ra, rb = fa[ma.end():], fb[mb.end():]
    # A short rest is likely the stressed part's tail (Karen, Koren; Davis, Devis): keep those apart.
    if min(len(ra), len(rb)) < 4:
        return False
    return bool(vowel_classes(ra) & vowel_classes(rb))


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
    near: set[str] = field(default_factory=set)  # names and words from the near-focus zone
    zones: dict[str, int] = field(default_factory=dict)  # how many of `names` came from each zone
    # How strongly the screen backs a spelling, from the full names it is part of (see FULL_NAME_*).
    tiers: dict[str, int] = field(default_factory=dict)

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


def extract_names(text: str | Sequence[tuple[str, str]], max_names: int = MAX_SCREEN_NAMES) -> ScreenNames:
    """Likely names in window text: capitalized words and runs of up to three, minus common words.

    `text` is the window's text, or its parts as (zone, text) pairs ordered closest to the cursor
    first (screen.py). A name ranks by the first part it shows up in, then by how often it shows,
    so the conversation being answered beats the toolbar and the sidebar.

    A word at the start of a sentence or line counts only if it is a known first name, is part of
    a full name with another uncommon word, or also shows up capitalized mid-sentence. Words that
    also appear in lowercase elsewhere on screen are words, not names, unless they are known names.
    """
    parts = [("page", text)] if isinstance(text, str) else [(z, t) for z, t in text if t]
    if not parts:
        return ScreenNames()
    lower_words: set[str] = set()
    for _, part in parts:
        lower_words.update(re.findall(r"\b[a-zß-öø-ÿ][\w'’-]*\b", part))
    part_runs = [_runs(part) for _, part in parts]
    mid_sentence = {w for runs in part_runs for words, start in runs for i, w in enumerate(words) if i > 0 or not start}

    counts: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    where: dict[str, int] = {}  # name -> the first (closest) part it shows up in
    words_out: Counter[str] = Counter()
    word_where: dict[str, int] = {}
    seen_in: dict[str, set[str]] = {}  # name -> the zones it shows up in
    near_count: Counter[str] = Counter()  # times each name shows up near the cursor

    def keep_word(word: str, at_start: bool, in_full_name: bool) -> bool:
        if is_common(word):
            return False
        if known_name(word):
            return True
        if word.casefold() in lower_words and not in_full_name:
            return False
        return not at_start or in_full_name or word in mid_sentence

    for index, runs in enumerate(part_runs):
        for words, start in runs:
            # "Hi Kelli", "Kelli Smith Reply": greetings and labels glued to a name are not part of it.
            # A common word that is also a first name stays: "Mark Jones", "Grace Hopper".
            while len(words) > 1 and is_common(words[0]) and not known_name(words[0]):
                words, start = words[1:], False
            while len(words) > 1 and is_common(words[-1]) and not known_name(words[-1]):
                words = words[:-1]
            uncommon = [w for w in words if not is_common(w)]
            full_name = len(words) > 1 and len(uncommon) >= 1 and (
                len(uncommon) >= 2 or any(known_name(w) for w in words) or not start
            )
            kept = [w for i, w in enumerate(words) if keep_word(w, start and i == 0, full_name)]
            if not kept:
                continue
            if full_name and len(words) <= MAX_RUN_WORDS:
                name = " ".join(words)
                name_parts = words
            else:
                name, name_parts = None, kept
            for w in name_parts:
                if not is_common(w) or full_name:
                    words_out[w] += 1
                    word_where.setdefault(w, index)
            for candidate in [name] if name else kept:
                counts[candidate] += 1
                first_seen.setdefault(candidate, len(first_seen))
                where.setdefault(candidate, index)
                seen_in.setdefault(candidate, set()).add(parts[index][0])
                if parts[index][0] == "near":
                    near_count[candidate] += 1

    # A full name also counts the times its first or last name shows up alone ("Kelli Smith" ... "Kelli").
    score = {n: counts[n] + sum(counts[w] for w in n.split() if w != n) for n in counts}
    ranked = sorted(counts, key=lambda n: (where[n], -score[n], first_seen[n]))
    # A single word already inside a full name kept as close to the cursor adds nothing as a keyword.
    names: list[str] = []
    for name in ranked:
        if " " not in name and any(
            name in other.split() and where[other] <= where[name] for other in ranked if " " in other
        ):
            continue
        names.append(name)
    names = names[:max_names]
    top_words = sorted(words_out, key=lambda w: (word_where[w], -words_out[w]))[:MAX_NAME_WORDS]
    near = {n for n in counts if parts[where[n]][0] == "near"} | {w for w in words_out if parts[word_where[w]][0] == "near"}
    zones = Counter(parts[where[n]][0] for n in names)
    tiers: dict[str, int] = {}
    for name in counts:
        if " " not in name:
            continue
        if "title" in seen_in[name] or near_count[name] >= 2:
            tier = FULL_NAME_HEADER
        elif "near" in seen_in[name]:
            tier = FULL_NAME_NEAR
        else:
            tier = FULL_NAME_ELSEWHERE
        for word in name.split():
            tiers[word] = max(tiers.get(word, 0), tier)
    return ScreenNames(
        names=names, words=set(top_words), near=near, zones={z: zones.get(z, 0) for z in ZONES}, tiers=tiers,
    )


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

def _dropped(a: str, b: str) -> int | None:
    """How many letters the shorter of a and b leaves out of the longer, if it is the longer with
    letters left out (pgs, pg). None if any letter differs instead (dlwr, dlmr)."""
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    it = iter(long)
    if all(c in it for c in short):
        return len(long) - len(short)
    return None


def loose_score(heard: str, name: str, known: bool = False) -> float | None:
    """How well a mumbled word (or words run together) fits a name, or None if it does not.

    Same first consonant sound, consonant keys equal or one with a sound dropped (two for longer
    names; a mumble drops sounds, it rarely swaps them: Delaware is not Delmar), similar length,
    and spelling at least LOOSE_SIMILARITY alike: "Lemon Tay" fits Lemonte, "Peggys" fits Pegues.
    A heard word that is itself a known first name must match the consonants exactly.
    """
    h, n = _fold(heard), _fold(name)
    if len(h) < 3 or len(n) < 3 or min(len(h), len(n)) / max(len(h), len(n)) < LOOSE_LENGTH_RATIO:
        return None  # "Del More" is not all of "Delmer Hayes"
    kh, kn = name_key(h), name_key(n)
    if not kh or not kn or kh[0] != kn[0]:
        return None
    distance = _dropped(kh, kn)
    allowed = LOOSE_KEY_DISTANCE if len(kn) <= 4 else LOOSE_KEY_DISTANCE + 1
    if distance is None or distance > allowed or (known and distance):
        return None
    similarity = SequenceMatcher(None, h, n).ratio()
    if similarity < (LOOSE_KNOWN_SIMILARITY if known else LOOSE_SIMILARITY):
        return None
    return similarity - 0.1 * distance


@dataclass
class NameFix:
    """Respells capitalized names: the screen first, then the dictionary's prefer: list.

    Call it on text; it counts what it did. Only capitalized words change, never "mark the date".

    Two passes:
      1. Each capitalized word that sounds like a name on screen (sounds_alike) takes its spelling.
         Names near the cursor (`near`) come first: if exactly one near spelling fits, it wins even
         when another spelling shows elsewhere on screen. When several fit, the one backed by the
         strongest full name wins (`tiers`): "Jon Bruno" in the header beats a lone "Hi John" in
         the thread. Two spellings backed equally: left alone.
      2. Loose, for mumbled names: a capitalized word, or two or three run together ("Lemon Tay"),
         that sounds like nothing on screen takes a near-focus name it clearly resembles
         (loose_score), when no other near name comes close (LOOSE_MARGIN).
    """

    screen_words: set[str] = field(default_factory=set)
    prefer: dict[str, str] = field(default_factory=dict)
    near: set[str] = field(default_factory=set)  # names and words read close to the cursor
    tiers: dict[str, int] = field(default_factory=dict)  # ScreenNames.tiers: full-name evidence per spelling
    from_screen: int = 0
    from_near: int = 0  # the loose pass
    from_prefer: int = 0
    ambiguous: int = 0
    changes: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._prefer = {" ".join(k.casefold().split()): v.strip() for k, v in self.prefer.items() if v and v.strip()}
        self._preferred = {v.casefold() for v in self._prefer.values()}
        self._exact = {w.casefold() for w in self.screen_words}
        self._near_words = {w for name in self.near for w in name.split()}
        self._near_names = sorted(
            {n for n in self.near | self._near_words if n and is_name_word(n.split()[0]) and not n.isupper()}
        )

    @property
    def changed(self) -> int:
        return self.from_screen + self.from_near + self.from_prefer

    def spelling(self, word: str, at_sentence_start: bool) -> str | None:
        """The spelling to use for one capitalized transcript word, or None to leave it."""
        if len(_fold(word)) < 2 or (at_sentence_start and is_common(word)):
            return None
        matches = {s for s in self._near_words if sounds_alike(word, s)}
        if not matches:
            matches = {s for s in self.screen_words if sounds_alike(word, s)}
        spellings = {s.casefold() for s in matches}
        if len(spellings) > 1:
            # A spelling in a full name beats one seen only on its own; a header beats a body.
            tier = {f: max(self.tiers.get(s, 0) for s in matches if s.casefold() == f) for f in spellings}
            best = max(tier.values())
            leaders = [f for f in spellings if tier[f] == best]
            if best == 0 or len(leaders) > 1:
                if word not in matches:  # spelled as on screen: leave it quietly
                    self.ambiguous += 1
                return None
            matches = {s for s in matches if s.casefold() == leaders[0]}
            spellings = {leaders[0]}
        if len(spellings) == 1:
            choice = next(iter(matches))
            if len(matches) > 1:  # Kelli and KELLI: take the one with lowercase letters
                choice = sorted(matches, key=lambda s: (s.isupper(), s))[0]
            self.from_screen += choice != word
            return choice if choice != word else None
        if word.casefold() in self._exact:
            return None
        preferred = self._prefer.get(word.casefold())
        if preferred and preferred != word and is_name_word(preferred):
            self.from_prefer += 1
            return preferred
        return None

    def __call__(self, text: str) -> str:
        if not text or (not self.screen_words and not self._prefer and not self.near):
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

        if self._near_names:
            # Words run together first ("Lemon Tay" is Lemonte), so the strict pass cannot take
            # one piece of them for some other name on screen (Tay -> Tate from a sidebar).
            text = self._loose(text, min_words=2)
        text = _NAME_TOKEN.sub(swap, text)
        return self._loose(text, min_words=1, max_words=1) if self._near_names else text

    # -- the loose pass --

    def _loose_ok(self, word: str, alone: bool) -> bool:
        """May the loose pass replace this transcript word? Not one spelled as on screen or in prefer:,
        and, on its own, not one the strict pass already had a say about."""
        f = word.casefold()
        if word.isupper() or len(_fold(word)) < 2 or f in self._exact or f in self._prefer or f in self._preferred:
            return False
        return not alone or not any(sounds_alike(word, s) for s in self.screen_words)

    def _span_ok(self, text: str, span: list[re.Match[str]]) -> bool:
        words = [m.group("word") for m in span]
        if not all(self._loose_ok(w.rstrip("'’-"), alone=len(span) == 1) for w in words):
            return False
        for before, after in zip(span, span[1:]):
            if before.group("poss") or before.group("word")[-1] in "'’-":
                return False
            if not _GAP.fullmatch(text[before.end() : after.start()]):
                return False
        if len(span) == 1 and _at_sentence_start(text, span[0].start()):
            # Any word is capitalized here ("Lemon cake is good"); a name opening a sentence is
            # usually said to someone: "Lemonte, ..." or "Lemonte." Mid-sentence, capitals mark names.
            rest = text[span[0].end() :].lstrip(" \t")
            if rest and rest[0] not in ",.!?;:":
                return False
        # Never a common word on its own ("Will", "Mark"); in a run it can be part of a misheard name ("Lemon Tay").
        return any(not is_common(w.rstrip("'’-")) for w in words)

    def _loose_match(self, span: list[re.Match[str]]) -> tuple[str, bool] | None:
        """(near-focus name, keep the possessive) for a span of transcript words, or None."""
        words = [m.group("word").rstrip("'’-") for m in span]
        heard = "".join(_fold(w) for w in words)
        known = len(words) == 1 and known_name(words[0])
        tries = [(heard, True)]
        if span[-1].group("poss"):
            tries.append((heard + "s", False))  # "Peggy's" may be all one name: Pegues
        best: dict[str, tuple[float, bool, str]] = {}
        for name in self._near_names:
            if known and " " not in name and known_name(name):
                continue  # two real first names: sounds_alike already said they differ (Sean, Shane)
            for h, keep in tries:
                score = loose_score(h, name, known)
                key = name.casefold()
                if score is not None and (key not in best or score > best[key][0]):
                    best[key] = (score, keep, name)
        if not best:
            return None
        ranked = sorted(best.values(), key=lambda t: -t[0])
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < LOOSE_MARGIN:
            self.ambiguous += 1  # two near names fit about as well: leave it
            return None
        _, keep, name = ranked[0]
        return name, keep

    def _loose(self, text: str, min_words: int = 1, max_words: int = MAX_RUN_WORDS) -> str:
        tokens = list(_NAME_TOKEN.finditer(text))
        out: list[str] = []
        pos = i = 0
        while i < len(tokens):
            matched = 0
            for length in range(min(max_words, len(tokens) - i), min_words - 1, -1):
                span = tokens[i : i + length]
                if not self._span_ok(text, span):
                    continue
                found = self._loose_match(span)
                if found is None:
                    continue
                name, keep = found
                last = span[-1]
                tail = last.group("word")[len(last.group("word").rstrip("'’-")) :]
                out.append(text[pos : span[0].start()])
                out.append(name + tail + ((last.group("poss") or "") if keep else ""))
                pos = last.end()
                self.from_near += 1
                self.changes.append((" ".join(m.group("word").rstrip("'’-") for m in span), name))
                matched = length
                break
            i += matched or 1
        out.append(text[pos:])
        return "".join(out)


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
