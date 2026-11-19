"""Phase 2b in the dictation service: screen names read while recording, sent first, used to respell.

A real ScreenReader with a fake window. No UI Automation, no audio device, no network.
"""

import logging
import threading
import time

import pytest

from gpw.dictionary import DictionaryStore
from gpw.screen import ScreenReader
from test_screen import FakeWindow, FakeWindowReader
from test_service import FakeEngine, FakeLearner, latency_records, make_service  # noqa: F401 - fixture

PRIVATE = "Private: the salary is 180k and the start date is March 3."
SCREEN = f"Inbox - Outlook\nFrom: Shawn Mulcahy <smulcahy@contoso.com>\nTo: Matt\nHi Matt,\n{PRIVATE}\nKelli Nguyen agreed."
HEARD = "Tell Sean Mulcahey that Kelly Nguyen is in."
PASTED = "Tell Shawn Mulcahy that Kelli Nguyen is in."


def screen_reader(window=None, skip=()):
    fake = FakeWindowReader(window or FakeWindow(text=SCREEN))
    return ScreenReader(lambda: fake, list(skip))


def dictate(service):
    service.start()
    time.sleep(0.05)  # the key is held; the screen read runs meanwhile
    return service.stop()


def test_screen_names_go_first_and_respell_the_transcript(make_service, caplog):
    caplog.set_level(logging.INFO)
    engine = FakeEngine(text=HEARD)
    service = make_service(engine=engine, screen=screen_reader())
    service.open()

    result = dictate(service)

    assert result.status == "ok" and result.text == PASTED
    keywords = engine.calls[0]["keywords"]
    assert keywords == ["Matt", "Shawn Mulcahy", "Kelli Nguyen", "Kavaliro", "SOCOM"]  # Matt shows twice
    service.pasted(result.dictation_id, 1500, 40, True)
    record = latency_records(service)[-1]
    assert record["screen"] == "ok" and record["screen_app"] == "OUTLOOK.EXE"
    assert record["screen_names"] >= 2 and record["names_fixed"] == 3 and record["screen_wait"] < 0.1
    assert "screen names status=ok app=OUTLOOK.EXE" in caplog.text and "fixed=3 (screen=3 near=0 preferred=0)" in caplog.text
    assert service.health()["screen_names"] is True


def test_screen_text_never_reaches_logs_disk_or_openai(make_service, caplog, tmp_path):
    # Done-when: screen text is never written to disk or logs, and only the picked-out names go to OpenAI.
    caplog.set_level(logging.DEBUG)
    engine = FakeEngine(text=HEARD)
    service = make_service(engine=engine, screen=screen_reader(), privacy={"delete_audio_after_success": False})
    result = dictate(service)
    service.pasted(result.dictation_id, 1500, 40, True)
    service.wait_for_background()

    sent = engine.calls[0]
    assert set(sent["keywords"]) <= {"Shawn Mulcahy", "Kelli Nguyen", "Matt", "Kavaliro", "SOCOM"}
    assert all(word not in " ".join(sent["keywords"]) for word in ["salary", "180k", "Private", "contoso", "Inbox"])
    assert PRIVATE not in str(sent)

    on_disk = "".join(
        path.read_text(encoding="utf-8", errors="ignore") for path in tmp_path.rglob("*") if path.is_file()
        and path.suffix in (".md", ".jsonl", ".log", ".txt", ".yaml")
    )
    for secret in ["salary", "180k", "contoso", "Mulcahy <"]:
        assert secret not in on_disk and secret not in caplog.text
    # Names stay out of the log too (the pasted text itself goes to the daily note, as before).
    assert "Shawn" not in caplog.text and "Kelli" not in caplog.text


def test_debug_log_transcripts_shows_the_names_and_changes(make_service, caplog):
    caplog.set_level(logging.INFO)
    service = make_service(engine=FakeEngine(text=HEARD), screen=screen_reader(), privacy={"debug_log_transcripts": True})
    dictate(service)
    assert "screen names: Matt, Shawn Mulcahy, Kelli Nguyen" in caplog.text and "Sean -> Shawn" in caplog.text
    assert "salary" not in caplog.text  # the window text itself, never


def test_a_slow_window_costs_at_most_the_deadline(make_service):
    # Done-when (service side): the screen read happens while recording, and release waits at most 0.08 s.
    gate = threading.Event()
    engine = FakeEngine(text=HEARD)
    service = make_service(engine=engine, screen=screen_reader(FakeWindow(text=SCREEN, gate=gate)))
    try:
        service.start()
        started = time.perf_counter()
        result = service.stop()
        took = time.perf_counter() - started
    finally:
        gate.set()
    assert result.status == "ok" and result.text == HEARD  # no names: nothing respelled
    assert took < 0.5
    service.pasted(result.dictation_id, 1500, 40, True)
    record = latency_records(service)[-1]
    assert record["screen"] == "late" and record["screen_wait"] <= 0.1
    assert engine.calls[0]["keywords"] == ["Kavaliro", "SOCOM"]


def test_skipped_apps_send_no_names(make_service):
    engine = FakeEngine(text=HEARD)
    service = make_service(engine=engine, screen=screen_reader(FakeWindow(app="KeePassXC.exe", text=SCREEN), ["KeePassXC.exe"]))
    result = dictate(service)
    assert result.text == HEARD and engine.calls[0]["keywords"] == ["Kavaliro", "SOCOM"]


def test_prefer_fills_in_when_the_screen_shows_no_spelling(make_service, tmp_path):
    path = tmp_path / "words.yaml"
    path.write_text('terms: []\nprefer:\n  "kelly": "Kelli"\n  "sean": "Shaun"\n', encoding="utf-8")
    words = DictionaryStore(path)
    service = make_service(engine=FakeEngine(text="Ask Kelly and Sean."), dictionary=words,
                           screen=screen_reader(FakeWindow(text="To: Sean Park")))
    assert dictate(service).text == "Ask Kelli and Sean."  # Sean is on screen as is: the screen wins
    no_screen = make_service(engine=FakeEngine(text="Ask Kelly and Sean."), dictionary=words)
    assert dictate(no_screen).text == "Ask Kelli and Shaun."


def test_auto_learn_hears_which_spellings_were_on_screen(make_service):
    learner = FakeLearner()
    service = make_service(engine=FakeEngine(text=HEARD), learner=learner, screen=screen_reader())
    result = dictate(service)
    assert service.watch_for_corrections(result.dictation_id) == 15000
    assert {"Shawn", "Mulcahy", "Kelli", "Nguyen"} <= learner.seen[-1]


def test_cancel_drops_the_screen_read(make_service):
    engine = FakeEngine(text=HEARD)
    service = make_service(engine=engine, screen=screen_reader())
    service.start()
    assert service.cancel() == "cancelled"
    assert service.stop().status == "error"
    assert engine.calls == []


def test_no_screen_reader_means_no_screen_fields(make_service):
    service = make_service(engine=FakeEngine(text=HEARD))
    result = dictate(service)
    service.pasted(result.dictation_id, 1500, 40, True)
    record = latency_records(service)[-1]
    assert "screen" not in record and "names_fixed" not in record
    assert service.health()["screen_names"] is False


@pytest.mark.parametrize("off", [{"screen_names": False}])
def test_screen_names_setting_parses(off, tmp_path):
    from test_service import make_config

    assert make_config(tmp_path).dictation.screen_names is True
    assert make_config(tmp_path, dictation=off).dictation.screen_names is False
    skip = make_config(tmp_path, dictation={"screen_names_skip": ["Chase", "KeePass.exe"]}).dictation.screen_names_skip
    assert skip == ("Chase", "KeePass.exe")
    assert "1Password.exe" in make_config(tmp_path).dictation.screen_names_skip


def test_linkedin_reply_gets_the_mumbled_name_from_the_open_conversation(make_service, caplog):
    # The real failure: replying to Lemonte Pegues on LinkedIn in Chrome pasted "Lamont, ...".
    from test_screen_zones import CONVERSATIONS, MESSAGE, assemble_parts, flatten, linkedin_window, node_zones

    class LinkedIn:
        app, title = "chrome.exe", "Messaging | LinkedIn - Google Chrome"

        def parts(self):
            nodes, focus = flatten(linkedin_window())
            return assemble_parts(nodes, node_zones(nodes, focus))

    caplog.set_level(logging.INFO)
    engine = FakeEngine(text="Lemon Tay, somehow this slipped past me. Lamont, thanks.")
    service = make_service(engine=engine, screen=ScreenReader(lambda: FakeWindowReader(LinkedIn())))
    result = dictate(service)

    assert result.text == "Lemonte, somehow this slipped past me. Lemonte, thanks."
    keywords = engine.calls[0]["keywords"]
    assert keywords[0] == "Lemonte Pegues" and not set(keywords[:4]) & set(CONVERSATIONS)
    assert "found=30 (near=4 title=0 page=26 far=0)" in caplog.text
    assert "fixed=2 (screen=1 near=1 preferred=0)" in caplog.text
    for private in ["Lemonte", "Pegues", "roadmap", MESSAGE[:20]]:
        assert private not in caplog.text
    service.pasted(result.dictation_id, 1500, 40, True)
    record = latency_records(service)[-1]
    assert record["screen_near"] == 4 and record["names_fixed"] == 2


def test_linkedin_header_spelling_beats_an_earlier_wrong_john(make_service):
    from test_screen_zones import assemble_parts, flatten, jon_window, node_zones

    class LinkedIn:
        app, title = "chrome.exe", "Messaging | LinkedIn - Google Chrome"

        def parts(self):
            nodes, focus = flatten(jon_window())
            return assemble_parts(nodes, node_zones(nodes, focus))

    engine = FakeEngine(text="John, the base salary works for me.")
    service = make_service(engine=engine, screen=ScreenReader(lambda: FakeWindowReader(LinkedIn())))
    assert dictate(service).text == "Jon, the base salary works for me."
    assert engine.calls[0]["keywords"][0] == "Jon Bruno"


def test_recruiter_inbox_john_does_not_block_the_open_conversations_jon(make_service, caplog):
    from test_screen_zones import assemble_parts, flatten, node_zones, recruiter_window

    class Recruiter:
        app, title = "chrome.exe", "Home | LinkedIn Talent Solutions - Google Chrome"

        def parts(self):
            nodes, focus = flatten(recruiter_window(open_with="Jon Bruno", listed="John Lacio"))
            return assemble_parts(nodes, node_zones(nodes, focus))

    caplog.set_level(logging.INFO)
    engine = FakeEngine(text="Hi John, thanks for getting back to me.")
    service = make_service(engine=engine, screen=ScreenReader(lambda: FakeWindowReader(Recruiter())))
    assert dictate(service).text == "Hi Jon, thanks for getting back to me."
    assert engine.calls[0]["keywords"][0] == "Jon Bruno"
    assert "fixed=1 (screen=1 near=0 preferred=0) ambiguous=0" in caplog.text
