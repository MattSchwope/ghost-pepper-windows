"""Phase 2b around the dictionary: prefer:, the gpw dictionary warnings, auto-learn into prefer:,
the cleanup hook and the latency comparison. No UI Automation, no network."""

import logging

import pytest
import yaml

from gpw import cli, logging_setup
from gpw.autolearn import Learner, find_corrections, is_name_fix
from gpw.cleanup import clean_dictation
from gpw.dictionary import TEMPLATE, DictionaryStore, dictionary_warnings, insert_replacement, parse_dictionary
from gpw.latency import format_report, summarize
from gpw.names import NameFix
from test_autolearn import FakeField, FakeReader
from test_cli import FakeEngine


# -- prefer: in the file --

def test_prefer_parses_and_the_template_has_an_empty_block():
    d = parse_dictionary({"prefer": {"kelly": "Kelli", " sean ": "Shawn"}})
    assert d.prefer == {"kelly": "Kelli", "sean": "Shawn"} and d.replace == {}
    starter = parse_dictionary(yaml.safe_load(TEMPLATE))
    assert starter.prefer == {} and starter.replace == {"cavaliro": "Kavaliro", "ts sci": "TS/SCI"}
    with pytest.raises(ValueError, match="prefer:"):
        parse_dictionary({"prefer": ["Kelli"]})
    with pytest.raises(ValueError, match="prefer:"):
        parse_dictionary({"prefer": {"kelly": None}})


def test_insert_into_prefer_keeps_replace_and_comments():
    text = insert_replacement(TEMPLATE, "kelly", "Kelli", key="prefer")
    text = insert_replacement(text, "sean", "Shawn", key="prefer")
    text = insert_replacement(text, "kelly", "Kellie", key="prefer")  # rewrites the line
    d = parse_dictionary(yaml.safe_load(text))
    assert d.prefer == {"kelly": "Kellie", "sean": "Shawn"}
    assert d.replace == {"cavaliro": "Kavaliro", "ts sci": "TS/SCI"}
    assert "# prefer: your usual spelling" in text


def test_store_add_preferred(tmp_path):
    store = DictionaryStore(tmp_path / "dictionary.yaml")
    store.ensure_file()
    assert store.add_preferred("Kelly", "Kelli") is True
    assert store.add_preferred("kelly", "Kelli") is False
    assert store.current().prefer == {"kelly": "Kelli"}
    assert store.current().replace == {"cavaliro": "Kavaliro", "ts sci": "TS/SCI"}


# -- gpw dictionary warnings --

def test_warnings_flag_name_variants_and_word_adding_fixes_only():
    d = parse_dictionary({
        "replace": {
            "kelly": "Kelli", "catherine": "Katherine",  # the user's two name entries
            "cavaliro": "Kavaliro", "ts sci": "TS/SCI",  # real fixes: no warning
            "clearance": "clearance TS/SCI",
        },
        "prefer": {"kelly green": "Kelli Green", "sean": "Shawn"},
    })
    warnings = dictionary_warnings(d)
    assert len(warnings) == 4
    assert sum('Move it to prefer:' in w for w in warnings) == 2
    assert any('"clearance": "clearance TS/SCI" adds words' in w for w in warnings)
    assert any('prefer: "kelly green"' in w for w in warnings)
    assert not any("cavaliro" in w or "ts sci" in w for w in warnings)


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_engine", lambda config: FakeEngine())
    monkeypatch.setattr(logging_setup, "configure_logging", lambda logs_dir: None)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f'app:\n  data_dir: "{(tmp_path / "data").as_posix()}"\n', encoding="utf-8")
    return cfg


def test_dictionary_command_lists_prefer_and_warns(tmp_path, config_file, capsys):
    path = tmp_path / "data" / "dictionary.yaml"
    path.parent.mkdir(parents=True)
    path.write_text('terms: [Kavaliro]\nreplace:\n  "kelly": "Kelli"\nprefer:\n  "sean": "Shawn"\n', encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--config", str(config_file), "dictionary"])
    out = capsys.readouterr().out
    assert exit_info.value.code == 0
    assert "Terms: 1   Fixes: 1   Preferred spellings: 1" in out
    assert "  sean -> Shawn" in out and "Names read from the window you dictate into go first" in out
    assert 'Warning: replace: "kelly": "Kelli" picks one spelling' in out
    assert path.read_text(encoding="utf-8").count("kelly") == 1  # warns, never edits


# -- auto-learn --

PASTED = "Please send the report to Kelly today."


@pytest.mark.parametrize(
    "wrong, right, name",
    [
        ("Kelly", "Kelli", True), ("Sean", "Shawn", True), ("Smith", "Smyth", True),
        ("kelly", "Kelli", False),  # lowercase as pasted: prefer: would never see it
        ("cavaliro", "Kavaliro", False), ("Dana", "Sarah", False), ("ts sci", "TS/SCI", False),
    ],
)
def test_is_name_fix(wrong, right, name):
    assert is_name_fix(wrong, right) is name


def test_find_corrections_keeps_the_pasted_case():
    assert find_corrections(PASTED, PASTED, PASTED.replace("Kelly", "Kelli")) == [("Kelly", "Kelli")]


def run_learner(store, after, seen=frozenset()):
    field = FakeField([PASTED, PASTED, after])
    learner = Learner(store, lambda: FakeReader(field), seconds=0.2, poll_seconds=0.01)
    try:
        assert learner.watch(1, PASTED, seen=seen)
        assert learner.wait_idle(3)
        return learner.take_learned()
    finally:
        learner.close()


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "dictionary.yaml"
    path.write_text("terms: []\nreplace: {}\n", encoding="utf-8")
    return DictionaryStore(path)


def test_auto_learn_saves_a_respelled_name_to_prefer(store, caplog):
    caplog.set_level(logging.INFO)
    assert run_learner(store, PASTED.replace("Kelly", "Kelli")) == [("kelly", "Kelli")]
    assert store.current().prefer == {"kelly": "Kelli"} and store.current().replace == {}
    assert "preferred=1" in caplog.text and "Kelli" not in caplog.text


def test_auto_learn_skips_a_spelling_the_screen_showed(store, caplog):
    caplog.set_level(logging.INFO)
    assert run_learner(store, PASTED.replace("Kelly", "Kelli"), seen=frozenset({"Kelli", "Kelly"})) == []
    assert store.current().prefer == {} and store.current().replace == {}
    assert "skipped_on_screen=1" in caplog.text


def test_auto_learn_still_saves_mishearings_to_replace(store):
    pasted = "Please send the cavaliro report."
    field = FakeField([pasted, pasted, pasted.replace("cavaliro", "Kavaliro")])
    learner = Learner(store, lambda: FakeReader(field), seconds=0.2, poll_seconds=0.01)
    try:
        learner.watch(1, pasted)
        assert learner.wait_idle(3)
    finally:
        learner.close()
    assert store.current().replace == {"cavaliro": "Kavaliro"} and store.current().prefer == {}


# -- cleanup hook --

def test_cleanup_respells_after_dictionary_fixes_and_again_after_the_text_model():
    fix = NameFix({"Shawn"}, prefer={"kelly": "Kelli"})
    result = clean_dictation("Um, tell sean and Sean and Kelly.", {"sean": "Sean"}, names=fix)
    assert result.text == "Tell Shawn and Shawn and Kelli."  # the replace: fix runs first, then the name

    def lowercase_it(text):
        return text.replace("Shawn", "shawn").replace("hi.", "hi!")  # a text model that re-cased the name

    fix = NameFix({"Shawn"})
    result = clean_dictation("Tell Sean hi.", {}, names=fix, ai=lowercase_it)
    assert (result.ai_status, result.text) == ("used", "Tell Shawn hi!")


# -- gpw latency --

def pasted(r2p, screen=None, **extra):
    record = {"status": "pasted", "audio": 5.0, "release_to_paste": r2p, "api": 1.0}
    if screen:
        record.update(screen=screen, screen_wait=extra.pop("wait", 0.0))
    return {**record, **extra}


def test_latency_compares_screen_names_on_and_off(tmp_path):
    records = [pasted(1.20), pasted(1.30), pasted(1.25, screen="off"),
               pasted(1.28, screen="ok", wait=0.0, names_fixed=1), pasted(1.33, screen="late", wait=0.08),
               pasted(1.31, screen="ok", wait=0.01), pasted(0.9, screen="ok", snippet=True)]
    s = summarize(records)["screen"]
    assert (s["on"], s["off"], s["late"], s["fixed"]) == (3, 3, 1, 1)
    assert s["on_median"] == 1.31 and s["off_median"] == 1.25 and s["slowest_wait"] == 0.08
    report = format_report(summarize(records), tmp_path / "x.jsonl")
    assert "Phase 2b target" in report and "on minus off = +0.06 s -> met (need 50 of each)." in report
    assert "Phase 2b" not in format_report(summarize([pasted(1.2)]), tmp_path / "x.jsonl")
