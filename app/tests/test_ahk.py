"""Checks the AutoHotkey side: the hotkey script loads, and the key state machine behaves.

Runs headless (no hotkeys are registered, nothing is typed). Skipped where AutoHotkey v2 is not installed.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from gpw.config import REPO_ROOT


def find_autohotkey() -> Path | None:
    for base in (os.environ.get("ProgramFiles"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            exe = Path(base) / "AutoHotkey" / "v2" / "AutoHotkey64.exe"
            if exe.is_file():
                return exe
    return None


AHK = find_autohotkey()
pytestmark = pytest.mark.skipif(AHK is None, reason="AutoHotkey v2 is not installed")


def run_ahk(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(AHK), "/ErrorStdOut", *args], capture_output=True, text=True, timeout=30)


def test_hotkey_script_loads_without_errors():
    result = run_ahk("/Validate", str(REPO_ROOT / "app" / "scripts" / "hotkey.ahk"))
    assert result.returncode == 0, result.stdout + result.stderr


def test_dictation_key_state_machine():
    result = run_ahk(str(REPO_ROOT / "app" / "tests" / "ahk" / "test_dictation_logic.ahk"))
    assert result.returncode == 0 and "ALL PASSED" in result.stdout, result.stdout + result.stderr


def test_hotkey_http_client_against_the_real_server(tmp_path):
    """The script's WinHttp client and the Python server agree: headers, status, UTF-8 text, abort."""
    import threading

    from gpw.server import make_server
    from gpw.service import DictationService
    from test_service import FakeEngine, FakeMic, make_config

    text = "Café at 9 – “quoted” ✓"
    gate = threading.Event()

    class SlowSecondEngine(FakeEngine):
        def transcribe_clip(self, *args, **kwargs):
            if self.calls:
                self.gate = gate  # the second /stop hangs until the test ends, so the client must abort
            return super().transcribe_clip(*args, **kwargs)

    engine = SlowSecondEngine(text=text)
    service = DictationService(make_config(tmp_path), engine, FakeMic())
    server = make_server(service, 0)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        result = subprocess.run(
            [str(AHK), "/ErrorStdOut", str(REPO_ROOT / "app" / "tests" / "ahk" / "test_service_client.ahk"),
             f"http://127.0.0.1:{server.server_port}"],
            capture_output=True, timeout=30,
        )
    finally:
        gate.set()
        server.shutdown()
        server.server_close()
        service.close()
    out = result.stdout.decode("utf-8")
    assert result.returncode == 0, out + result.stderr.decode("utf-8", "replace")
    assert out.splitlines() == [
        "health 200 gpw RCtrl",
        "start 200 1",
        "stop 200 ok 1",
        f"text {text}",
        "pasted 200 true",
        "aborted yes",
        "cancel 200 cancelled",
        "closed port: error",
    ]
    assert json.loads(service.latency_log.read_text(encoding="utf-8").splitlines()[0])["release_to_paste"] == 1.234
