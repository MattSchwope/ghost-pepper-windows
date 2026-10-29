"""Auto-learn (experimental): turn quick corrections of a pasted name into dictionary fixes.

After a paste, one worker thread reads the focused text field through Windows UI Automation
for dictionary.learn_seconds. If you change one or two words of the pasted text into a name
or acronym (cavaliro -> Kavaliro, ts sci -> TS/SCI), that pair is saved to dictionary.yaml
replace:. Plain lowercase edits (their -> there) are never learned: they are grammar or word
choice, not spelling. The field text stays in memory; it is never logged or sent anywhere.
Password fields are skipped.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from collections import OrderedDict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Protocol

from .dictionary import MAX_TERM_CHARS, DictionaryStore

logger = logging.getLogger(__name__)

MAX_FIELD_CHARS = 200_000
MAX_DIFF_TOKENS = 3_000
_TOKEN = re.compile(r"\S+")
_PUNCT = "\"'“”‘’()[]{}.,;:!?…"


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def _core(token: str) -> str:
    return token.strip(_PUNCT)


def locate(haystack: list[str], needle: list[str]) -> int | None:
    """Start of the last run of `needle` in `haystack`. The ends may be glued to typed text ("Hi,Kavaliro")."""
    n = len(needle)
    if n == 0 or n > len(haystack):
        return None
    for start in range(len(haystack) - n, -1, -1):
        window = haystack[start : start + n]
        if n == 1:
            if needle[0] in window[0]:
                return start
            continue
        if (
            window[0].endswith(needle[0])
            and window[-1].startswith(needle[-1])
            and window[1:-1] == needle[1:-1]
        ):
            return start
    return None


def _similar(a: str, b: str) -> float:
    a, b = a.casefold().replace(" ", ""), b.casefold().replace(" ", "")
    return SequenceMatcher(None, a, b).ratio()


def learnable(wrong: str, right: str, sentence_start: bool) -> bool:
    """Is wrong -> right a spelling fix for a name or acronym, safe to apply to every later dictation?"""
    if not wrong or not right or wrong == right or len(wrong) < 2 or len(right) > MAX_TERM_CHARS:
        return False
    if not any(c.isalpha() for c in right) or re.sub(r"\D", "", wrong) != re.sub(r"\D", "", right):
        return False  # 100 -> 200, 3 pm -> 4 pm: an amount, date or time edit, not a spelling
    if not any(c.isupper() or c.isdigit() for c in right):
        return False  # their -> there, affect -> effect: grammar or word choice
    inner_caps = any(c.isupper() or c.isdigit() for c in right[1:])
    if sentence_start and not inner_caps:
        return False  # its only capital may just start the sentence
    if wrong.casefold() == right.casefold():
        return inner_caps  # socom -> SOCOM yes; bill -> Bill could be a word or a name, so no
    return _similar(wrong, right) >= 0.5


def find_corrections(pasted: str, before: str, after: str, max_changes: int = 2) -> list[tuple[str, str]]:
    """Fixes made inside the pasted span between two reads of the field: [(wrong, Right)].

    Edits outside the span (typing on, other paragraphs) are ignored. Anything bigger than
    `max_changes` short word swaps inside the span is a rewrite, and nothing is learned.
    """
    p, b, a = tokens(pasted), tokens(before), tokens(after)
    start = locate(b, p)
    if start is None:
        return []
    end = start + len(p)

    pre = 0
    while pre < min(len(b), len(a)) and b[pre] == a[pre]:
        pre += 1
    suf = 0
    while suf < min(len(b), len(a)) - pre and b[len(b) - 1 - suf] == a[len(a) - 1 - suf]:
        suf += 1
    b_mid, a_mid = b[pre : len(b) - suf], a[pre : len(a) - suf]
    if len(b_mid) + len(a_mid) > MAX_DIFF_TOKENS:
        return []

    changes: list[tuple[int, list[str], list[str]]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, b_mid, a_mid, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        i1, i2 = i1 + pre, i2 + pre
        inside = (i1 < end and i2 > start) if i2 > i1 else start < i1 < end
        if not inside:
            continue
        old, new = b[i1:i2], a[j1 + pre : j2 + pre]
        if tag == "replace" and [_core(t) for t in old] == [_core(t) for t in new]:
            continue  # punctuation only
        if tag != "replace" or i1 < start or i2 > end or len(old) > 3 or len(new) > 3:
            return []
        changes.append((i1, old, new))
    if not changes or len(changes) > max_changes:
        return []

    pairs = []
    for i1, old, new in changes:
        if (i1 == start and b[start] != p[0]) or (i1 + len(old) == end and b[end - 1] != p[-1]):
            continue  # the pasted word is glued to typed text, so its edges are unclear
        wrong = " ".join(_core(t) for t in old).strip()
        right = " ".join(_core(t) for t in new).strip()
        sentence_start = i1 == 0 or b[i1 - 1][-1:] in ".?!"
        if learnable(wrong, right, sentence_start):
            pairs.append((wrong.casefold(), right))
    return pairs


class Field(Protocol):
    app: str

    def read(self) -> str | None: ...


class FieldReader(Protocol):
    def focused(self) -> Field | None: ...


class Learner:
    """Watches one paste at a time on a single worker thread (UI Automation wants its own COM thread)."""

    def __init__(
        self,
        store: DictionaryStore,
        reader_factory: Callable[[], FieldReader],
        seconds: float = 15.0,
        poll_seconds: float = 0.5,
        log_text: bool = False,
    ) -> None:
        self.store = store
        self.reader_factory = reader_factory
        self.seconds = seconds
        self.poll_seconds = poll_seconds
        self.log_text = log_text
        self.available = True
        self._jobs: queue.Queue = queue.Queue()
        self._interrupt = threading.Event()
        self._lock = threading.Lock()
        self._unreported: list[tuple[str, str]] = []
        self._queued = 0
        self._idle = threading.Event()
        self._idle.set()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._worker, name="gpw-learn", daemon=True)
            self._thread.start()

    def close(self) -> None:
        if self._thread is not None:
            self._interrupt.set()
            self._jobs.put(None)
            self._thread.join(timeout=3)
            self._thread = None

    def watch(self, dictation_id: int, pasted: str) -> bool:
        """Start watching the focused field for corrections to `pasted`. Ends the previous watch."""
        if not self.available or not pasted.strip():
            return False
        with self._lock:
            self._queued += 1
            self._idle.clear()
        self._interrupt.set()
        self._jobs.put((dictation_id, pasted))
        self.start()
        return True

    def wait_idle(self, timeout: float) -> bool:
        return self._idle.wait(timeout)

    def take_learned(self) -> list[tuple[str, str]]:
        """Fixes learned since the last call, for the hotkey script's tooltip."""
        with self._lock:
            learned, self._unreported = self._unreported, []
        return learned

    def _worker(self) -> None:
        reader = None
        try:
            reader = self.reader_factory()
        except Exception as exc:
            self.available = False
            logger.error(
                "Auto-learn is off: could not start UI Automation (%s). "
                "If comtypes is missing, run: .venv\\Scripts\\python.exe -m pip install -e .", exc,
            )
        while True:
            job = self._jobs.get()
            if job is None:
                return
            self._interrupt.clear()
            dictation_id, pasted = job
            if reader is not None:
                try:
                    self._watch(reader, dictation_id, pasted)
                except Exception as exc:
                    logger.error("Auto-learn id=%s failed (%s)", dictation_id, type(exc).__name__)
            with self._lock:
                self._queued -= 1
                if self._queued == 0:
                    self._idle.set()

    def _watch(self, reader: FieldReader, dictation_id: int, pasted: str) -> None:
        field = reader.focused()
        if field is None:
            logger.info("Auto-learn id=%s: no readable text field in focus", dictation_id)
            return
        wanted = tokens(pasted)
        before = last = None
        deadline = time.monotonic() + self.seconds
        while True:
            text = field.read()
            if text is None:
                break  # the field closed (message sent, window gone)
            if before is None:
                if locate(tokens(text), wanted) is not None:
                    before = text
            else:
                last = text
            if time.monotonic() >= deadline or self._interrupt.wait(self.poll_seconds):
                break
        pairs = find_corrections(pasted, before, last) if before is not None and last is not None else []
        added = []
        for wrong, right in pairs:
            try:
                if self.store.add_replacement(wrong, right):
                    added.append((wrong, right))
            except ValueError as exc:
                logger.error("Auto-learn id=%s could not save a fix: %s", dictation_id, exc)
        logger.info(
            "Auto-learn id=%s app=%s paste_found=%s learned=%s",
            dictation_id, field.app, before is not None, len(added),
        )
        if added and self.log_text:
            logger.info("Auto-learn id=%s learned: %s", dictation_id, "; ".join(f"{w} -> {r}" for w, r in added))
        with self._lock:
            self._unreported += added


# -- Windows UI Automation --

def process_name(pid: int) -> str:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return "unknown"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name
    finally:
        kernel32.CloseHandle(handle)
    return "unknown"


class UIAField:
    def __init__(self, element, uia_module) -> None:
        self.app = process_name(element.CurrentProcessId)
        self._text = self._value = None
        # GetCurrentPattern hands back IUnknown (null when unsupported); GetCurrentPatternAs gives a bare address.
        text = element.GetCurrentPattern(uia_module.UIA_TextPatternId)
        if text:
            self._text = text.QueryInterface(uia_module.IUIAutomationTextPattern)
        else:
            value = element.GetCurrentPattern(uia_module.UIA_ValuePatternId)
            if value:
                self._value = value.QueryInterface(uia_module.IUIAutomationValuePattern)

    def read(self) -> str | None:
        try:
            if self._text is not None:
                return self._text.DocumentRange.GetText(MAX_FIELD_CHARS)
            if self._value is not None:
                return self._value.CurrentValue
        except Exception:
            return None
        return None


class UIAReader:
    """Created on the learner's worker thread, so all UI Automation calls stay on that one thread."""

    def __init__(self) -> None:
        import comtypes
        import comtypes.client

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass  # importing comtypes on this thread already joined it to COM; that works for reads too
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as uia_module

        self._module = uia_module
        self._uia = comtypes.client.CreateObject(uia_module.CUIAutomation, interface=uia_module.IUIAutomation)

    def focused(self) -> UIAField | None:
        try:
            element = self._uia.GetFocusedElement()
            if element is None or element.CurrentIsPassword:
                return None
            field = UIAField(element, self._module)
        except Exception:
            return None
        return field if field.read() is not None else None
