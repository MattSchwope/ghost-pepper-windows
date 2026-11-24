"""The screen-names worker with a fake window. No UI Automation, no other apps."""

import threading
import time

import pytest

from gpw.screen import ScreenReader, skipped

SCREEN_TEXT = "From: Shawn Mulcahy\nPrivate: the salary is 180k and the start date is March 3."


class FakeWindow:
    def __init__(self, app="OUTLOOK.EXE", title="Inbox - Outlook", text=SCREEN_TEXT, delay=0.0, gate=None):
        self.app, self.title, self._text, self.delay, self.gate = app, title, text, delay, gate
        self.reads = 0

    def text(self):
        self.reads += 1
        if self.gate is not None:
            self.gate.wait(5)
        time.sleep(self.delay)
        return self._text


class FakeWindowReader:
    def __init__(self, window):
        self.window = window
        self.thread = None

    def foreground(self):
        self.thread = threading.current_thread().name
        return self.window


@pytest.fixture
def make_reader():
    readers = []

    def factory(window=None, skip=("1Password.exe",), reader=None):
        fake = reader or FakeWindowReader(window or FakeWindow())
        screen = ScreenReader(lambda: fake, skip)
        readers.append(screen)
        return screen, fake

    yield factory
    for screen in readers:
        screen.close()


def test_reads_names_on_its_own_thread_and_keeps_only_names(make_reader):
    screen, fake = make_reader()
    result = screen.collect(screen.begin(), time.perf_counter() + 2)
    assert result.status == "ok" and result.app == "OUTLOOK.EXE"
    assert result.names.names == ["Shawn Mulcahy"] and result.found == 1
    assert fake.thread.startswith("gpw-screen")
    # Nothing but the names comes back: no sentence of the window text survives the read.
    assert "salary" not in repr(result) and "180k" not in repr(result)


@pytest.mark.parametrize(
    "app, title, skip, out",
    [
        ("1Password.exe", "Vault", ["1password.exe"], True),
        ("chrome.exe", "Chase Online - Google Chrome", ["Chase"], True),
        ("chrome.exe", "Gmail - Google Chrome", ["Chase", "1Password.exe"], False),
        ("chrome.exe", "1Password.exe notes", ["1Password.exe"], False),  # an .exe entry only matches the program
        ("OUTLOOK.EXE", "Inbox", ["", "  "], False),
    ],
)
def test_skipped(app, title, skip, out):
    assert skipped(app, title, skip) is out


def test_skipped_apps_are_never_read(make_reader):
    window = FakeWindow(app="KeePassXC.exe")
    screen, _ = make_reader(window, skip=["KeePassXC.exe"])
    result = screen.collect(screen.begin(), time.perf_counter() + 2)
    assert result.status == "skipped" and result.found == 0 and window.reads == 0


def test_a_slow_read_comes_back_late_at_the_deadline(make_reader):
    gate = threading.Event()
    screen, _ = make_reader(FakeWindow(gate=gate))
    future = screen.begin()
    started = time.perf_counter()
    result = screen.collect(future, started + 0.05)
    assert result.status == "late" and time.perf_counter() - started < 0.2
    assert screen.begin() is None  # still reading: the next dictation goes without, rather than queue
    assert screen.collect(None, time.perf_counter()).status == "busy"
    gate.set()
    future.result(2)
    assert screen.begin() is not None


def test_no_window_and_unreadable_windows(make_reader):
    class Nothing:
        def foreground(self):
            return None

    screen, _ = make_reader(reader=Nothing())
    assert screen.collect(screen.begin(), time.perf_counter() + 2).status == "no_window"

    class Broken(FakeWindow):
        def text(self):
            raise OSError("COM error")

    screen, _ = make_reader(Broken())
    result = screen.collect(screen.begin(), time.perf_counter() + 2)
    assert result.status == "error" and result.app == "OUTLOOK.EXE"


def test_turns_itself_off_without_ui_automation(caplog):
    def broken():
        raise ImportError("No module named 'comtypes'")

    screen = ScreenReader(broken)
    try:
        assert screen.collect(screen.begin(), time.perf_counter() + 2).status == "off"
        assert screen.available is False and screen.begin() is None
        assert screen.collect(None, time.perf_counter()).status == "off"
    finally:
        screen.close()
    assert "pip install" in caplog.text
