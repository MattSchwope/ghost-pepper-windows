"""Read names off the focused window while the mic records (Phase 2b).

On key down the service calls ScreenReader.begin(). One worker thread (UI Automation wants its
own COM thread) reads the foreground window's text and keeps only the names in it
(names.extract_names). The text itself is dropped right there: it is never logged, stored or
sent anywhere. At key release the service takes whatever is ready by a short deadline, so a
slow window costs at most that deadline, never a full read.

Skipped: apps in dictation.screen_names_skip (by program name, or a word in the window title),
password fields, and anything UI Automation cannot read (remote desktop, images, scanned PDFs).

Zones (closest to the cursor first, see UIAWindowReader.window_parts):
  near   the focused field and the panes around it: the conversation or email being answered,
         its header, To and Subject. Grown outward from the focus until it holds NEAR_MIN_CHARS,
         in rings, so what is closer still ranks first.
  title  the window title.
  page   everything else, in rings by how far out from the focus it sits.
  far    toolbars, menus, tab strips, folder trees, nav bars, sidebars and page banners.
Only counts per zone are logged, never the names or the text.
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
from .names import ZONES, ScreenNames, extract_names

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
    focus: str = ""  # field | caret | page | none (see UIAWindowReader.window_parts); "" if not known

    @property
    def found(self) -> int:
        return len(self.names.names)


class Window(Protocol):
    """A window to read. parts() gives (zone, text) pairs, closest to the cursor first;
    a window with only text() is read as one "page" zone."""

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
            parts = window.parts() if hasattr(window, "parts") else [("page", window.text())]
        except Exception as exc:
            logger.debug("Screen names: could not read %s (%s)", window.app, type(exc).__name__)
            return ScreenResult("error", app=window.app, seconds=time.perf_counter() - started)
        names = extract_names(parts, self.max_names)
        del parts  # only the names leave this function
        focus = getattr(window, "focus", "")
        return ScreenResult("ok", app=window.app, names=names, seconds=time.perf_counter() - started, focus=focus)


# -- zones: which part of the window each piece of text sits in --

NEAR_MIN_CHARS = 400  # grow the near zone outward from the focus until it holds this much text
# Near never holds more than this share of the window's text. When the focus is the whole page
# (Chrome often reports its web document as focused), there is no near zone rather than all of it.
NEAR_MAX_SHARE = 0.5
NEAR_SHARE_MIN_TEXT = 2_000  # a window with less text than this is small enough not to need the cap
ZONE_ORDER = {zone: i for i, zone in enumerate(ZONES)}


@dataclass
class Node:
    """One element of a window's tree, in document order (a parent comes before its children)."""

    parent: int  # index of the parent node; -1 for the window itself
    text: list[str] = field(default_factory=list)
    far: bool = False  # in a toolbar, menu, tab strip, folder tree, nav bar, sidebar or banner


def node_zones(nodes: list[Node], focus: int, near_min_chars: int = NEAR_MIN_CHARS) -> list[tuple[str, int]]:
    """(zone, ring) for each node. The ring is how many levels up from the focused element you go
    before the node is inside: 0 is the focused element itself, 1 its parent's other children, and so on.

    The near zone is rings 0 to K, where K is the first ring at which the text so far (outside far
    parts) reaches near_min_chars. It never takes in the whole window, nor a ring that would put
    more than NEAR_MAX_SHARE of the window's text in it; if the focused element alone holds that
    much, there is no near zone. The window's own text (its title) is the title zone. Without a
    focused element, everything is page or far.
    """
    n = len(nodes)
    if not 0 <= focus < n:
        return [("title", 0) if i == 0 else ("far" if node.far else "page", 0) for i, node in enumerate(nodes)]
    path: dict[int, int] = {}
    i, k = focus, 0
    while i >= 0 and i not in path:
        path[i] = k
        i, k = nodes[i].parent, k + 1
    top = max(path.values())  # the window itself
    ring = [0] * n
    for i, node in enumerate(nodes):
        if i in path:
            ring[i] = path[i]
        elif 0 <= node.parent < i:
            ring[i] = ring[node.parent]
        else:
            ring[i] = top
    chars = [0] * (top + 1)
    for i, node in enumerate(nodes):
        if not node.far and i != 0:
            chars[ring[i]] += sum(len(t) for t in node.text)
    whole = sum(chars)
    near, total = -1, 0
    for k in range(top):
        total += chars[k]
        if whole >= NEAR_SHARE_MIN_TEXT and total > NEAR_MAX_SHARE * whole:
            break  # this ring would make "near" most of the window
        near = k
        if total >= near_min_chars:
            break
    zones: list[tuple[str, int]] = []
    for i, node in enumerate(nodes):
        if i == 0:
            zones.append(("title", 0))
        elif ring[i] <= near:
            zones.append(("near", ring[i]))
        elif node.far:
            zones.append(("far", ring[i]))
        else:
            zones.append(("page", ring[i]))
    return zones


def assemble_parts(nodes: list[Node], zones: list[tuple[str, int]]) -> list[tuple[str, str]]:
    """The window's text as (zone, text) parts, closest to the cursor first: near rings, title, page rings, far.

    The near zone keeps its rings apart, so the open conversation (ring 2, say) still ranks ahead of
    the conversation list next to it (ring 3) when a short conversation pulls both into near."""
    buckets: dict[tuple[int, int], list[str]] = {}
    for node, (zone, ring) in zip(nodes, zones):
        if node.text:
            order = (ZONE_ORDER[zone], ring if zone in ("near", "page") else 0)
            buckets.setdefault(order, []).extend(node.text)
    return [(ZONES[order[0]], "\n".join(texts)) for order, texts in sorted(buckets.items())]


# -- Windows UI Automation --

UIA_RuntimeIdPropertyId = 30000
UIA_IsTextPatternAvailablePropertyId = 30040
UIA_ValueValuePropertyId = 30045
UIA_LandmarkTypePropertyId = 30157
UIA_LocalizedLandmarkTypePropertyId = 30158
UIA_DocumentControlTypeId = 50030
UIA_EditControlTypeId = 50004
TreeScope_Subtree = 7
# Menus, menu bars, status bars, toolbars, tab items, trees (folder panes), title bars.
FAR_CONTROL_TYPES = {50009, 50010, 50017, 50019, 50021, 50023, 50037}
UIA_NavigationLandmarkTypeId = 80004
UIA_SearchLandmarkTypeId = 80005
UIA_CustomLandmarkTypeId = 80000
FAR_CUSTOM_LANDMARKS = {"complementary", "banner", "content information", "contentinfo"}


class UIAWindow:
    def __init__(self, reader: "UIAWindowReader", hwnd: int, app: str, title: str) -> None:
        self._reader = reader
        self._hwnd = hwnd
        self.app = app
        self.title = title
        self.focus = ""

    def parts(self) -> list[tuple[str, str]]:
        parts, self.focus = self._reader.window_parts(self._hwnd)
        return parts

    def text(self) -> str:
        return "\n".join(text for _, text in self.parts())


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
        # One cross-process call caches the whole tree (control view) with these properties.
        cache = self._uia.CreateCacheRequest()
        for prop in (
            uia.UIA_NamePropertyId, uia.UIA_IsPasswordPropertyId, uia.UIA_ControlTypePropertyId,
            UIA_ValueValuePropertyId, UIA_IsTextPatternAvailablePropertyId, UIA_RuntimeIdPropertyId,
        ):
            cache.AddProperty(prop)
        self._landmarks = True
        try:  # Windows 10 1703 and later
            cache.AddProperty(UIA_LandmarkTypePropertyId)
            cache.AddProperty(UIA_LocalizedLandmarkTypePropertyId)
        except Exception:
            self._landmarks = False
        cache.TreeScope = TreeScope_Subtree
        cache.TreeFilter = self._uia.ControlViewCondition
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

    def _is_far(self, element: object, kind: int) -> bool:
        if kind in FAR_CONTROL_TYPES:
            return True
        if not self._landmarks:
            return False
        landmark = element.GetCachedPropertyValue(UIA_LandmarkTypePropertyId)
        if landmark in (UIA_NavigationLandmarkTypeId, UIA_SearchLandmarkTypeId):
            return True
        if landmark == UIA_CustomLandmarkTypeId:
            label = element.GetCachedPropertyValue(UIA_LocalizedLandmarkTypePropertyId)
            return isinstance(label, str) and label.casefold() in FAR_CUSTOM_LANDMARKS
        return False

    def _focus_ids(self) -> tuple[tuple | None, tuple | None]:
        """(focused element, element holding the caret). Chrome often reports its whole web document
        as focused while you type in a message box; the caret then says which box."""
        try:
            focused = self._uia.GetFocusedElement()
            focus_id = tuple(focused.GetRuntimeId()) if focused is not None else None
        except Exception:
            return None, None
        caret_id = None
        try:
            if focused.CurrentControlType == UIA_DocumentControlTypeId:
                m = self._m
                pattern = focused.GetCurrentPattern(m.UIA_TextPattern2Id)
                if pattern:
                    _, caret = pattern.QueryInterface(m.IUIAutomationTextPattern2).GetCaretRange()
                    holder = caret.GetEnclosingElement() if caret else None
                    if holder is not None:
                        caret_id = tuple(holder.GetRuntimeId())
        except Exception:
            caret_id = None  # no caret (nothing being typed), or no TextPattern2
        return focus_id, (caret_id if caret_id != focus_id else None)

    def window_parts(self, hwnd: int) -> tuple[list[tuple[str, str]], str]:
        """The window's text in zones (see node_zones), and how the focus was found:
        field (the focused element), caret (the element holding the caret inside a focused page),
        page (the focus was most of the window, so no near zone), none (not in this window).
        Password fields are skipped."""
        m = self._m
        focus_id, caret_id = self._focus_ids()
        root = self._uia.ElementFromHandleBuildCache(hwnd, self._cache)
        if root is None:
            return [], "none"
        nodes: list[Node] = []
        readable: list[tuple[int, object]] = []  # documents and edit fields, read through TextPattern later
        focus = caret = -1
        size = 0
        stack: list[tuple[object, int, bool]] = [(root, -1, False)]
        while stack and len(nodes) < MAX_ELEMENTS:
            element, parent, far = stack.pop()
            try:
                if element.CachedIsPassword:
                    continue
                kind = element.CachedControlType
                far = far or self._is_far(element, kind)
                text: list[str] = []
                if size < MAX_SCREEN_CHARS:
                    name = element.CachedName
                    if name:
                        text.append(name)
                    value = element.GetCachedPropertyValue(UIA_ValueValuePropertyId)
                    if isinstance(value, str) and value and value != name:
                        text.append(value)
                    size += sum(len(t) for t in text)
                if (focus < 0 and focus_id is not None) or (caret < 0 and caret_id is not None):
                    runtime_id = element.GetCachedPropertyValue(UIA_RuntimeIdPropertyId)
                    if runtime_id is not None:
                        runtime_id = tuple(runtime_id)
                        if runtime_id == focus_id:
                            focus = len(nodes)
                        elif runtime_id == caret_id:
                            caret = len(nodes)
                if kind in (UIA_DocumentControlTypeId, UIA_EditControlTypeId) and element.GetCachedPropertyValue(
                    UIA_IsTextPatternAvailablePropertyId
                ):
                    readable.append((len(nodes), element))
                children = element.GetCachedChildren()
            except Exception:
                continue
            index = len(nodes)
            nodes.append(Node(parent, text, far))
            count = children.Length if children else 0
            for j in reversed(range(count)):
                stack.append((children.GetElement(j), index, far))
        how = "caret" if caret >= 0 else "field" if focus >= 0 else "none"
        focus = caret if caret >= 0 else focus
        zones = node_zones(nodes, focus)
        if focus >= 0 and not any(zone == "near" for zone, _ in zones):
            how = "page"
        # Documents and edit fields hold text their children do not show (a reply's quoted email).
        # Nearest first, at most MAX_TEXT_READS. Skip the focus's outer containers: in a browser that
        # is the whole page again, which would pull the sidebar and nav into the near rings.
        path = set()
        i = focus
        while i >= 0 and i not in path:
            path.add(i)
            i = nodes[i].parent
        reads = 0
        for index, element in sorted(readable, key=lambda r: (ZONE_ORDER[zones[r[0]][0]], zones[r[0]][1])):
            if reads >= MAX_TEXT_READS or size >= MAX_SCREEN_CHARS:
                break
            if index in path and zones[index][0] != "near":
                continue
            try:
                pattern = element.GetCurrentPattern(m.UIA_TextPatternId)
                if not pattern:
                    continue
                reads += 1
                body = pattern.QueryInterface(m.IUIAutomationTextPattern).DocumentRange.GetText(MAX_SCREEN_CHARS - size)
            except Exception:
                continue
            if body:
                nodes[index].text.append(body)
                size += len(body)
        return assemble_parts(nodes, zones), how
