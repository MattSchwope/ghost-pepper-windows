"""Read names off the focused window while the mic records (Phase 2b).

On key down the service calls ScreenReader.begin(). One worker thread (UI Automation wants its
own COM thread) reads the foreground window's text and keeps only the names in it
(names.extract_names). The text itself is dropped right there: it is never logged, stored or
sent anywhere. At key release the service takes whatever is ready by a short deadline, so a
slow window costs at most that deadline, never a full read.

Skipped: apps in dictation.screen_names_skip (by program name, or a word in the window title),
password fields, and anything UI Automation cannot read (remote desktop, images, scanned PDFs).
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .autolearn import process_name
from .config import SCREEN_NAMES_SKIP
from .names import ScreenNames, extract_names

logger = logging.getLogger(__name__)

MAX_SCREEN_CHARS = 300_000
MAX_ELEMENTS = 20_000
MAX_TEXT_READS = 4  # documents and edit fields read through TextPattern per window
UIA_TIMEOUT_MS = 3_000

DEFAULT_SKIP = SCREEN_NAMES_SKIP


@dataclass
class ScreenResult:
    status: str  # ok | skipped | no_window | error | late | busy | off
    app: str = ""
    names: ScreenNames = field(default_factory=ScreenNames)
    seconds: float = 0.0

    @property
    def found(self) -> int:
        return len(self.names.names)


class Window(Protocol):
    app: str
    title: str

    def text(self) -> str: ...


class WindowReader(Protocol):
    def foreground(self) -> Window | None: ...


def skipped(app: str, title: str, skip: list[str] | tuple[str, ...]) -> bool:
    """Entries ending in .exe match the program; anything else matches a word or phrase in the window title."""
    app_l, title_l = app.casefold(), title.casefold()
    for entry in skip:
        e = entry.strip().casefold()
        if not e:
            continue
        if e.endswith(".exe"):
            if app_l == e:
                return True
        elif e in title_l:
            return True
    return False


class ScreenReader:
    """begin() at key down, collect() at release. One read at a time; a read still running is not queued behind."""

    def __init__(
        self,
        reader_factory: Callable[[], WindowReader],
        skip: list[str] | tuple[str, ...] = DEFAULT_SKIP,
        max_names: int = 30,
    ) -> None:
        self.reader_factory = reader_factory
        self.skip = list(skip)
        self.max_names = max_names
        self.available = True
        self._reader: WindowReader | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()
        self._running: Future | None = None

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpw-screen", initializer=self._init)
        return self._executor

    def _init(self) -> None:
        try:
            self._reader = self.reader_factory()
        except Exception as exc:
            self.available = False
            logger.error(
                "Screen names are off: could not start UI Automation (%s). "
                "If comtypes is missing, run: .venv\\Scripts\\python.exe -m pip install -e .", exc,
            )

    def start(self) -> None:
        """Start the worker thread now, so the first dictation does not pay for loading UI Automation."""
        with self._lock:
            self._ensure_executor().submit(lambda: None)

    def close(self) -> None:
        with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None

    def begin(self) -> Future | None:
        """Start reading the focused window. None if screen names are off or a read is still running."""
        if not self.available:
            return None
        with self._lock:
            if self._running is not None and not self._running.done():
                return None
            self._running = self._ensure_executor().submit(self._read)
            return self._running

    def collect(self, future: Future | None, deadline: float) -> ScreenResult:
        """The read's result if it is done by `deadline` (time.perf_counter()), else status late."""
        if future is None:
            return ScreenResult("busy" if self.available else "off")
        try:
            return future.result(timeout=max(0.0, deadline - time.perf_counter()))
        except FutureTimeout:
            return ScreenResult("late")
        except Exception as exc:  # the read itself failed
            logger.error("Screen names read failed (%s)", type(exc).__name__)
            return ScreenResult("error")

    def _read(self) -> ScreenResult:
        started = time.perf_counter()
        reader = self._reader
        if reader is None:
            return ScreenResult("off")
        try:
            window = reader.foreground()
        except Exception as exc:
            logger.debug("Screen names: no foreground window (%s)", type(exc).__name__)
            window = None
        if window is None:
            return ScreenResult("no_window", seconds=time.perf_counter() - started)
        if skipped(window.app, window.title, self.skip):
            return ScreenResult("skipped", app=window.app, seconds=time.perf_counter() - started)
        try:
            text = window.text()
        except Exception as exc:
            logger.debug("Screen names: could not read %s (%s)", window.app, type(exc).__name__)
            return ScreenResult("error", app=window.app, seconds=time.perf_counter() - started)
        names = extract_names(text, self.max_names)
        del text  # only the names leave this function
        return ScreenResult("ok", app=window.app, names=names, seconds=time.perf_counter() - started)


# -- Windows UI Automation --

UIA_IsTextPatternAvailablePropertyId = 30040
UIA_ValueValuePropertyId = 30045
UIA_DocumentControlTypeId = 50030
UIA_EditControlTypeId = 50004
TreeScope_Descendants = 4


class UIAWindow:
    def __init__(self, reader: "UIAWindowReader", hwnd: int, app: str, title: str) -> None:
        self._reader = reader
        self._hwnd = hwnd
        self.app = app
        self.title = title

    def text(self) -> str:
        return self._reader.window_text(self._hwnd)


class UIAWindowReader:
    """Created on the screen worker thread, so every UI Automation call stays on that one thread."""

    def __init__(self) -> None:
        import comtypes
        import comtypes.client

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as uia

        self._m = uia
        self._uia = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
        try:  # a hung app must not hold the worker for the default 20 s
            uia2 = self._uia.QueryInterface(uia.IUIAutomation2)
            uia2.ConnectionTimeout = UIA_TIMEOUT_MS
            uia2.TransactionTimeout = UIA_TIMEOUT_MS
        except Exception:
            pass
        cache = self._uia.CreateCacheRequest()
        for prop in (
            uia.UIA_NamePropertyId, uia.UIA_IsPasswordPropertyId, uia.UIA_ControlTypePropertyId,
            UIA_ValueValuePropertyId, UIA_IsTextPatternAvailablePropertyId,
        ):
            cache.AddProperty(prop)
        self._cache = cache
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)

    def foreground(self) -> UIAWindow | None:
        from ctypes import wintypes

        u = self._user32
        u.GetForegroundWindow.restype = wintypes.HWND
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(wintypes.HWND(hwnd), buf, len(buf))
        return UIAWindow(self, hwnd, process_name(pid.value), buf.value)

    def window_text(self, hwnd: int) -> str:
        m = self._m
        root = self._uia.ElementFromHandle(hwnd)
        if root is None:
            return ""
        found = root.FindAllBuildCache(TreeScope_Descendants, self._uia.ControlViewCondition, self._cache)
        parts: list[str] = []
        size = 0
        text_reads = 0
        count = min(found.Length if found else 0, MAX_ELEMENTS)
        for i in range(count):
            if size > MAX_SCREEN_CHARS:
                break
            element = found.GetElement(i)
            try:
                if element.CachedIsPassword:
                    continue
                name = element.CachedName
                if name:
                    parts.append(name)
                    size += len(name)
                value = element.GetCachedPropertyValue(UIA_ValueValuePropertyId)
                if isinstance(value, str) and value and value != name:
                    parts.append(value)
                    size += len(value)
                kind = element.CachedControlType
                if (
                    text_reads < MAX_TEXT_READS
                    and kind in (UIA_DocumentControlTypeId, UIA_EditControlTypeId)
                    and element.GetCachedPropertyValue(UIA_IsTextPatternAvailablePropertyId)
                ):
                    pattern = element.GetCurrentPattern(m.UIA_TextPatternId)
                    if pattern:
                        text_reads += 1
                        body = pattern.QueryInterface(m.IUIAutomationTextPattern).DocumentRange.GetText(
                            MAX_SCREEN_CHARS - size
                        )
                        if body:
                            parts.append(body)
                            size += len(body)
            except Exception:
                continue
        return "\n".join(parts)
