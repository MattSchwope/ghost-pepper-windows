"""Auto-learn with a fake text field. No UI Automation, no other apps."""

import time

import pytest

from gpw.autolearn import Learner, find_corrections, learnable, locate, tokens
from gpw.dictionary import DictionaryStore

PASTED = "Please send the cavaliro report to Dana."


def test_locate_finds_the_last_run_even_when_glued():
    hay = tokens("Hi, send it. Notes: send it.")
    assert locate(hay, tokens("send it.")) == 4
    assert locate(tokens("Hi,send it now"), tokens("send it now")) == 0
    assert locate(tokens("nothing here"), tokens("send it")) is None
    assert locate(tokens("abc"), []) is None


@pytest.mark.parametrize(
    "wrong, right, start, ok",
    [
        ("cavaliro", "Kavaliro", False, True),
        ("ts sci", "TS/SCI", False, True),
        ("socom", "SOCOM", False, True),
        ("their", "there", False, False),  # lowercase: grammar, not a name
        ("bill", "Bill", False, False),  # case-only with a plain capital could be a word
        ("cavaliro", "Kavaliro", True, False),  # sentence start: the capital proves nothing
        ("socom", "SOCOM", True, True),  # inner capitals still prove it
        ("dana", "Sarah", False, False),  # a different name, not a misspelling
        ("x", "X1", False, False),  # too short to match safely
    ],
)
def test_learnable(wrong, right, start, ok):
    assert learnable(wrong, right, start) is ok


def test_find_corrections_learns_a_fixed_name():
    before = f"Hi Dana,\n{PASTED}\nThanks"
    after = f"Hi Dana,\n{PASTED.replace('cavaliro', 'Kavaliro')}\nThanks"
    assert find_corrections(PASTED, before, after) == [("cavaliro", "Kavaliro")]


def test_find_corrections_handles_two_word_fixes_and_ignores_edits_elsewhere():
    pasted = "He holds a ts sci clearance."
    before = f"Intro line.\n{pasted}"
    after = f"Intro line, edited.\nHe holds a TS/SCI clearance. More typing after it."
    assert find_corrections(pasted, before, after) == [("ts sci", "TS/SCI")]


@pytest.mark.parametrize(
    "after",
    [
        "Please send the Kavaliro report to Dana!",  # punctuation only at the end
        "Please email the cavaliro summary to Dana today.",  # a rewrite
        "Please send the report to Dana.",  # a word deleted
        "Please send the cavaliro report to Dana.",  # nothing changed
    ],
)
def test_find_corrections_ignores_punctuation_rewrites_and_deletions(after):
    pairs = find_corrections(PASTED, PASTED, after)
    assert pairs in ([], [("cavaliro", "Kavaliro")])
    if "Kavaliro" not in after:
        assert pairs == []


def test_find_corrections_needs_the_paste_in_the_first_read():
    assert find_corrections(PASTED, "Something else entirely.", PASTED) == []


def test_find_corrections_skips_words_glued_to_typed_text():
    assert find_corrections("cavaliro called", "Note:cavaliro called", "Note:Kavaliro called") == []


class FakeField:
    app = "notepad.exe"

    def __init__(self, reads):
        self.reads = list(reads)

    def read(self):
        if len(self.reads) > 1:
            return self.reads.pop(0)
        return self.reads[0] if self.reads else None


class FakeReader:
    def __init__(self, field):
        self.field = field

    def focused(self):
        return self.field


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "dictionary.yaml"
    path.write_text("terms: []\nreplace: {}\n", encoding="utf-8")
    return DictionaryStore(path)


def run_learner(store, reads, seconds=0.2):
    learner = Learner(store, lambda: FakeReader(FakeField(reads)), seconds=seconds, poll_seconds=0.01)
    try:
        assert learner.watch(1, PASTED)
        assert learner.wait_idle(3)
        return learner.take_learned(), learner
    finally:
        learner.close()


def test_learner_saves_a_fix_and_reports_it_once(store, caplog):
    caplog.set_level("INFO")
    learned, learner = run_learner(store, [PASTED, PASTED, PASTED.replace("cavaliro", "Kavaliro")])
    assert learned == [("cavaliro", "Kavaliro")]
    assert store.current().replace == {"cavaliro": "Kavaliro"}
    assert learner.take_learned() == []
    assert "Kavaliro" not in caplog.text and "app=notepad.exe" in caplog.text


def test_learner_waits_for_the_paste_to_land(store):
    learned, _ = run_learner(store, ["", "", PASTED, PASTED.replace("cavaliro", "Kavaliro")])
    assert learned == [("cavaliro", "Kavaliro")]


def test_learner_stops_when_the_field_closes(store):
    field = FakeField([PASTED])
    field.read = iter([PASTED, None]).__next__
    learner = Learner(store, lambda: FakeReader(field), seconds=5, poll_seconds=0.01)
    try:
        learner.watch(1, PASTED)
        assert learner.wait_idle(2)  # well before the 5 s window
    finally:
        learner.close()
    assert store.current().replace == {}


def test_a_new_paste_ends_the_previous_watch(store):
    field = FakeField([PASTED, PASTED.replace("cavaliro", "Kavaliro")])
    learner = Learner(store, lambda: FakeReader(field), seconds=30, poll_seconds=0.01)
    try:
        learner.watch(1, PASTED)
        time.sleep(0.2)  # watch 1 sees the paste, then the fix
        learner.watch(2, "An unrelated second paste.")  # ends watch 1 now instead of in 30 s
        deadline = time.monotonic() + 3
        while not learner._unreported and time.monotonic() < deadline:
            time.sleep(0.01)
        assert learner.take_learned() == [("cavaliro", "Kavaliro")]
    finally:
        learner.close()


def test_learner_turns_itself_off_without_ui_automation(store, caplog):
    def broken():
        raise ImportError("No module named 'comtypes'")

    learner = Learner(store, broken, seconds=1)
    try:
        assert learner.watch(1, PASTED)
        assert learner.wait_idle(2)
        assert learner.available is False
        assert learner.watch(2, PASTED) is False
    finally:
        learner.close()
    assert "pip install" in caplog.text
