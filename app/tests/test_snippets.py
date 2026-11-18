import os

import pytest
import yaml

from gpw.snippets import TEMPLATE, Snippet, SnippetStore, match_snippet, parse_snippets, spoken_name

SNIPPETS = parse_snippets({"signature": "Matt\nKavaliro", "Follow-Up": "Checking in.", "Q3 outreach": "Hello"})


def test_parse_snippets_keys_by_what_is_said_and_keeps_the_text():
    parsed = parse_snippets({"Follow-Up": "  Line one\n\nLine two\n", "PIN": 1234})
    assert parsed["follow up"] == Snippet("Follow-Up", "  Line one\n\nLine two")
    assert parsed["pin"].text == "1234"
    assert parse_snippets(None) == {}
    assert parse_snippets({}) == {}


@pytest.mark.parametrize(
    "raw",
    [
        ["signature"],
        {"signature": None},
        {"signature": ""},
        {"signature": "  \n"},
        {"signature": ["a", "b"]},
        {"signature": {"a": 1}},
        {"signature": True},
        {"---": "text"},
        {None: "text"},
        {"follow-up": "a", "Follow Up": "b"},
    ],
)
def test_parse_snippets_rejects_bad_shapes(raw):
    with pytest.raises(ValueError):
        parse_snippets(raw)


def test_template_parses_to_the_example():
    parsed = parse_snippets(yaml.safe_load(TEMPLATE))
    assert list(parsed) == ["signature"]
    assert parsed["signature"].text == "Your Name\nYour Title\nyou@example.com"


@pytest.mark.parametrize(
    "transcript, name",
    [
        ("insert signature", "signature"),
        ("Insert signature.", "signature"),
        ("INSERT SIGNATURE!", "signature"),
        ("Insert, signature.", "signature"),
        ("  insert\nsignature  ", "signature"),
        ("Um, insert signature.", "signature"),
        ("Insert, uh, signature.", "signature"),
        ("Insert follow up.", "Follow-Up"),
        ("Insert follow-up.", "Follow-Up"),
        ("Insert Q3 outreach.", "Q3 outreach"),
    ],
)
def test_the_whole_dictation_fires_a_snippet(transcript, name):
    assert match_snippet(transcript, SNIPPETS).name == name


@pytest.mark.parametrize(
    "transcript",
    [
        "",
        "insert",
        "signature",
        "Please insert signature.",
        "Insert signature here.",
        "Insert the signature.",
        "Thanks for your time. Insert signature.",
        "Insert signature. Thanks for your time.",
        "Insert a comma after that word.",
        "Inserts signature.",
        "Insert signatures.",
        "Insert outreach.",  # only part of a name
    ],
)
def test_anything_else_is_just_words(transcript):
    assert match_snippet(transcript, SNIPPETS) is None


def test_spoken_name():
    assert spoken_name("Insert a comma here.") == "a comma here"
    assert spoken_name("Insert.") is None
    assert spoken_name("Add a comma here.") is None


def write(path, text, tick=[1_000_000_000]):  # noqa: B006 - a counter, so every write gets a new mtime
    path.write_text(text, encoding="utf-8")
    tick[0] += 10
    os.utime(path, (tick[0], tick[0]))


def test_store_creates_the_starter_once_and_keeps_edits(tmp_path):
    store = SnippetStore(tmp_path / "data" / "snippets.yaml")
    assert store.ensure_file() is True
    assert list(store.current()) == ["signature"]
    write(store.path, "bio: Short bio.\n")
    assert store.ensure_file() is False
    assert store.path.read_text(encoding="utf-8") == "bio: Short bio.\n"


def test_store_rereads_when_the_file_changes(tmp_path):
    store = SnippetStore(tmp_path / "snippets.yaml")
    write(store.path, "bio: One.\n")
    assert store.current()["bio"].text == "One."
    write(store.path, "bio: Two.\nintro: Hi.\n")
    assert store.current()["bio"].text == "Two." and "intro" in store.current()


def test_store_keeps_the_last_good_copy_through_a_typo(tmp_path, caplog):
    store = SnippetStore(tmp_path / "snippets.yaml")
    write(store.path, "bio: Secret bio text.\n")
    assert store.current()["bio"].text == "Secret bio text."
    write(store.path, "bio: [oops\n")
    assert store.current()["bio"].text == "Secret bio text."
    assert store.error and "has an error" in caplog.text
    write(store.path, "bio: Fixed.\n")
    assert store.current()["bio"].text == "Fixed." and store.error == ""
    assert "Secret bio text" not in caplog.text


def test_store_copes_with_a_missing_file(tmp_path, caplog):
    store = SnippetStore(tmp_path / "snippets.yaml")
    assert store.current() == {} and store.error == ""
    write(store.path, "bio: One.\n")
    assert store.current()
    store.path.unlink()
    assert store.current() == {} and "is gone" in caplog.text
