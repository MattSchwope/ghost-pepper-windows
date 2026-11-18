"""Recent dictations (Phase 2c): the in-memory list, the daily-log refill, /recent and the hotkeys."""

import logging
import threading
from datetime import date, datetime

import pytest

from gpw.config import hotkey_identity, parse_config
from gpw.markdown_store import append_daily_dictation, daily_log_path
from gpw.recent import (
    RecentDictation,
    RecentDictations,
    clean_app_name,
    escape_field,
    from_daily_log,
    parse_daily_log,
    render_recent,
)
from test_service import SECRET, FakeEngine, FakeMic, latency_records, make_service  # noqa: F401 - fixture

DAY = date(2026, 10, 28)
MULTILINE = "Dear Sam,\n\nSee you at 9.\nMatt"


def at(h, m, s=0):
    return datetime(2026, 10, 28, h, m, s)


def dictate(service):
    service.start()
    return service.stop()


# -- the list --

def test_keeps_the_last_ten_newest_first():
    recent = RecentDictations()
    for i in range(12):
        recent.add(RecentDictation(at(9, i), f"text {i}", dictation_id=i + 1))
    items = recent.newest_first()
    assert [x.text for x in items] == [f"text {i}" for i in range(11, 1, -1)]


def test_app_is_set_by_dictation_id_and_removed_entries_go():
    recent = RecentDictations()
    recent.add(RecentDictation(at(9, 0), "one", dictation_id=1))
    recent.add(RecentDictation(at(9, 1), "two", dictation_id=2))
    assert recent.set_app(1, "OUTLOOK.EXE")
    assert not recent.set_app(7, "chrome.exe")
    assert not recent.set_app(0, "chrome.exe")  # refilled entries have no id
    recent.remove(2)
    assert [(x.text, x.app) for x in recent.newest_first()] == [("one", "OUTLOOK")]


@pytest.mark.parametrize(
    ("raw", "shown"),
    [("OUTLOOK.EXE", "OUTLOOK"), ("chrome.exe", "chrome"), ("ms-teams.exe", "ms-teams"), ("a\tb\nc", "abc"),
     ("x" * 100, "x" * 60), ("", "")],
)
def test_app_names(raw, shown):
    assert clean_app_name(raw) == shown


# -- the /recent body --

def test_render_escapes_so_text_survives_exactly():
    text = "Line one\nLine two\r\n\tTabbed C:\\new\\path"
    body = render_recent([RecentDictation(at(14, 5, 33), text, app="Code"),
                          RecentDictation(at(9, 0), "Matt\nTitle", label="[snippet: signature]")])
    lines = body.split("\n")
    assert lines[-1] == "" and len(lines) == 3  # one line per dictation, so line breaks are escaped
    assert lines[0] == "14:05\tCode\t\tLine one\\nLine two\\r\\n\\tTabbed C:\\\\new\\\\path"
    assert lines[1] == "09:00\t\t[snippet: signature]\tMatt\\nTitle"


def test_escape_field_round_trips_with_a_simple_unescape():
    text = "a\\nb\\\\n\tc\n"  # backslashes followed by n must not turn into line breaks
    escaped = escape_field(text)
    assert "\n" not in escaped and "\t" not in escaped
    out, i = [], 0
    while i < len(escaped):
        if escaped[i] == "\\":
            out.append({"\\": "\\", "t": "\t", "n": "\n", "r": "\r"}[escaped[i + 1]])
            i += 2
        else:
            out.append(escaped[i])
            i += 1
    assert "".join(out) == text


def test_render_empty():
    assert render_recent([]) == ""


# -- the daily log --

def test_daily_log_round_trip(tmp_path):
    append_daily_dictation(tmp_path, at(9, 15, 2), "First one.", "first one", 8.4)
    append_daily_dictation(tmp_path, at(9, 20, 0), MULTILINE, MULTILINE, 12)
    append_daily_dictation(tmp_path, at(10, 0, 0), "[snippet: signature]", "Insert signature.", 2)
    append_daily_dictation(tmp_path, at(11, 0, 0), "No seconds, no raw.")
    text = daily_log_path(tmp_path, DAY).read_text(encoding="utf-8")
    assert parse_daily_log(text, DAY) == [
        (at(9, 15, 2), "First one."),
        (at(9, 20, 0), MULTILINE),
        (at(10, 0, 0), "[snippet: signature]"),
        (at(11, 0, 0), "No seconds, no raw."),
    ]


def test_daily_log_parse_tolerates_hand_edits():
    text = (
        "---\r\ntype: dictation-log\r\n---\r\n\r\n# Dictation 2026-10-28\r\n"
        "\r\n## 08:00:00 (3 s)\r\n\r\nKept, with CRLF.\r\n"
        "\r\n## 08:05:00\r\n\r\n\r\n"  # an entry whose text was deleted
        "\r\n## 25:00:00\r\n\r\nNot a time.\r\n"
        "\r\n## 08:10:00 (4 s)\r\n\r\nLast.\r\n\r\n**Raw transcript:** last\r\n"
    )
    assert parse_daily_log(text, DAY) == [(at(8, 0), "Kept, with CRLF."), (at(8, 10), "Last.")]


def test_refill_takes_the_last_ten_and_resolves_snippets(tmp_path):
    for i in range(12):
        append_daily_dictation(tmp_path, at(9, i), f"Dictation {i}.")
    append_daily_dictation(tmp_path, at(10, 0), "[snippet: signature]", "insert signature")
    append_daily_dictation(tmp_path, at(10, 1), "[snippet: gone]", "insert gone")
    names = {"signature": "Matt\nTitle"}
    items = from_daily_log(tmp_path, DAY, names.get)
    assert len(items) == 10
    assert items[-2] == RecentDictation(at(10, 0), "Matt\nTitle", label="[snippet: signature]")
    assert items[-1] == RecentDictation(at(10, 1), "[snippet: gone]", label="[snippet: gone]")
    assert items[0].text == "Dictation 4."


def test_refill_with_no_log_is_empty(tmp_path):
    assert from_daily_log(tmp_path, DAY, lambda name: None) == []


# -- the service --

def test_each_pasted_dictation_is_listed_with_its_app(make_service):
    service = make_service(engine=FakeEngine(text=MULTILINE))
    result = dictate(service)
    assert [x.text for x in service.recent.newest_first()] == [MULTILINE]
    assert service.pasted(result.dictation_id, 900, 20, True, app="OUTLOOK.EXE")
    item = service.recent.newest_first()[0]
    assert (item.when, item.app, item.label) == (at(9, 15), "OUTLOOK", "")
    assert "app" not in latency_records(service)[0]


def test_a_dictation_that_never_pasted_is_still_listed(make_service):
    service = make_service()
    dictate(service)  # no /pasted report: the paste failed or went to a window with no text field
    assert [x.text for x in service.recent.newest_first()] == [SECRET]


def test_snippet_is_listed_by_name_and_copies_its_text(make_service, tmp_path):
    from gpw.snippets import SnippetStore

    path = tmp_path / "snippets.yaml"
    path.write_text("signature: |\n  Matt\n  Title\n", encoding="utf-8")
    service = make_service(engine=FakeEngine(text="Insert signature."), snippets=SnippetStore(path))
    result = dictate(service)
    item = service.recent.newest_first()[0]
    assert (item.label, item.text) == ("[snippet: signature]", result.text)
    assert item.text == "Matt\nTitle"


def test_skipped_and_cancelled_clips_are_not_listed(make_service):
    from test_service import speech

    service = make_service(mic=FakeMic(audio=speech(amp=0.001)))
    assert dictate(service).status == "skipped"
    assert len(service.recent) == 0

    service = make_service(engine=FakeEngine(text=""))
    assert dictate(service).status == "skipped"
    assert len(service.recent) == 0

    service = make_service()
    service.start()
    service.cancel()
    assert len(service.recent) == 0


def test_cancel_during_transcription_is_not_listed(make_service):
    gate = threading.Event()
    service = make_service(engine=FakeEngine(gate=gate))
    service.start()
    done = {}
    worker = threading.Thread(target=lambda: done.update(result=service.stop()))
    worker.start()
    while service.state != "processing":
        pass
    service.cancel()
    gate.set()
    worker.join(5)
    assert done["result"].status == "cancelled"
    assert len(service.recent) == 0


def test_cancel_just_after_transcription_removes_it_again(make_service):
    service = make_service()
    finish = service._finish

    def finish_then_cancel(dictation_id, *args):
        result = finish(dictation_id, *args)
        service._cancelled.add(dictation_id)  # Esc lands between the transcript and the reply
        return result

    service._finish = finish_then_cancel
    assert dictate(service).status == "cancelled"
    assert len(service.recent) == 0


def test_open_refills_from_todays_daily_log(make_service):
    service = make_service()
    append_daily_dictation(service.config.notes_dir, at(8, 0), "Before the restart.")
    append_daily_dictation(service.config.notes_dir, datetime(2026, 10, 27, 23, 0), "Yesterday.")
    service.open()
    assert [x.text for x in service.recent.newest_first()] == ["Before the restart."]
    dictate(service)
    assert [x.text for x in service.recent.newest_first()] == [SECRET, "Before the restart."]


def test_open_starts_empty_when_the_daily_log_is_off(make_service):
    service = make_service(dictation={"daily_log": False})
    append_daily_dictation(service.config.notes_dir, at(8, 0), "Not read.")
    service.open()
    assert len(service.recent) == 0


def test_refill_never_logs_text_and_writes_nothing(make_service, caplog):
    service = make_service()
    path = append_daily_dictation(service.config.notes_dir, at(8, 0), SECRET)
    before = path.read_bytes()
    caplog.set_level(logging.DEBUG)
    service.open()
    assert SECRET not in caplog.text and "1 refilled" in caplog.text
    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


def test_refill_survives_an_unreadable_log(make_service, caplog):
    service = make_service()
    path = daily_log_path(service.config.notes_dir, DAY)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe not utf-8 \x80")
    service.open()
    assert len(service.recent) == 0 and "Could not read" in caplog.text


def test_health_reports_the_recent_keys(make_service):
    health = make_service().health()
    assert (health["recent_key"], health["paste_last_key"]) == ("^!h", "^!l")


# -- config --

def config(**hotkeys):
    return parse_config({"hotkeys": hotkeys}).hotkeys


def test_recent_hotkeys_default_and_can_be_turned_off():
    assert (config().recent, config().paste_last) == ("^!h", "^!l")
    assert (config(recent="", paste_last=None).recent, config(paste_last=None).paste_last) == ("", "")
    assert config(recent="#v").recent == "#v"


@pytest.mark.parametrize(
    ("hotkeys", "names"),
    [
        ({"recent": "^!d"}, "add_word and hotkeys.recent"),
        ({"paste_last": "!^D"}, "add_word and hotkeys.paste_last"),
        ({"recent": "^!l"}, "recent and hotkeys.paste_last"),
        ({"recent": "RCtrl"}, "dictate and hotkeys.recent"),
        ({"paste_last": "Escape"}, "cancel and hotkeys.paste_last"),
        ({"recent": "*^!d"}, "add_word and hotkeys.recent"),
    ],
)
def test_colliding_hotkeys_are_refused(hotkeys, names):
    with pytest.raises(ValueError, match=f"hotkeys.{names}"):
        config(**hotkeys)


def test_turned_off_keys_never_collide():
    assert config(add_word="", recent="", paste_last="").recent == ""


def test_hotkey_identity():
    assert hotkey_identity("^!h") == hotkey_identity("!^H") == hotkey_identity("~*^!h")
    assert hotkey_identity("^!h") != hotkey_identity("^+h")
    assert hotkey_identity("RControl") == hotkey_identity("rctrl")
    assert hotkey_identity("^") == (frozenset(), "^")
