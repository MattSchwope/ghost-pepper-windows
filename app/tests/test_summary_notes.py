from datetime import date

import pytest
import yaml

from gpw.config import PROMPT_DIR
from gpw.notes import file_title, front_matter, render_note, unique_path, write_note
from gpw.summary import Summary, clean_title, demote_headings, load_profile, parse_summary, summarize


def test_parse_summary_title_line():
    s = parse_summary("Title: Q4 plan with Acme\n\n## Overview\nText.\n\n### Detail\nMore.")
    assert s == Summary("Q4 plan with Acme", "### Overview\nText.\n\n#### Detail\nMore.")


def test_parse_summary_heading_bold_or_missing_title():
    assert parse_summary("# Budget call\n### Overview\nx").title == "Budget call"
    assert parse_summary("**Title:** *Hiring sync*\n### Overview\nx").title == "Hiring sync"
    assert parse_summary("### Overview\nx") == Summary("Meeting", "### Overview\nx")
    assert parse_summary("").title == "Meeting"


def test_demote_and_clean():
    assert demote_headings("### Already\nx") == "### Already\nx"
    assert demote_headings("no headings") == "no headings"
    assert demote_headings("# A\n## B\n#hashtag") == "### A\n#### B\n#hashtag"
    assert clean_title('"' + "word " * 40 + '"').endswith("word") and len(clean_title("word " * 40)) <= 80


def test_summarize_sends_context_then_transcript():
    class Model:
        def complete(self, instructions, text):
            self.args = (instructions, text)
            return "Title: T\n### Overview\nok"

    model = Model()
    result = summarize(model, "INSTR", "[00:00:00] Me: hi", "Date: 2026-11-18")
    assert model.args == ("INSTR", "Date: 2026-11-18\n\n[00:00:00] Me: hi")
    assert result.title == "T"


def test_meeting_profile_exists_and_asks_for_the_sections():
    profile = load_profile(PROMPT_DIR, "meeting_capture")
    for part in ("Title:", "### Overview", "### Decisions", "### Action items", "### Open questions", "Them"):
        assert part in profile
    with pytest.raises(FileNotFoundError):
        load_profile(PROMPT_DIR, "no_such_profile")


def test_front_matter_matches_the_phase_4_format():
    text = front_matter({"type": "meeting", "date": date(2026, 11, 18), "start": "14:05", "duration": "00:47:12",
                         "speakers": ["Me", "Them"]})
    assert text.startswith("---\n") and text.endswith("---\n")
    assert "date: 2026-11-18\n" in text and "speakers: [Me, Them]\n" in text
    assert 'start: "14:05"\n' in text and 'duration: "00:47:12"\n' in text
    loaded = yaml.safe_load(text.strip("-\n"))
    assert loaded["duration"] == "00:47:12" and loaded["start"] == "14:05"  # read back as text, not numbers
    assert loaded["date"] == date(2026, 11, 18) and loaded["speakers"] == ["Me", "Them"]
    assert front_matter({"app": "Zoom: it's"}).count("\n") == 3


def test_render_note():
    note = render_note({"type": "meeting"}, "Q4 plan", "### Overview\nok", "[00:00:00] Me: hi\n")
    assert note == "---\ntype: meeting\n---\n# Q4 plan\n\n## Summary\n\n### Overview\nok\n\n## Transcript\n\n[00:00:00] Me: hi\n"


def test_file_title():
    assert file_title('Q4 plan: Acme / Kavaliro?') == "Q4 plan Acme Kavaliro"
    assert file_title("  ...  ") == "Untitled"
    assert file_title("CON") == "Untitled"
    assert len(file_title("x" * 200)) == 80


def test_write_note_never_overwrites(tmp_path):
    a = write_note(tmp_path / "notes", "2026-11-18 1405 Q4", "one")
    b = write_note(tmp_path / "notes", "2026-11-18 1405 Q4", "two")
    assert a.name == "2026-11-18 1405 Q4.md" and b.name == "2026-11-18 1405 Q4 (2).md"
    assert a.read_text(encoding="utf-8") == "one" and unique_path(tmp_path / "notes", "x").name == "x.md"
    assert not list((tmp_path / "notes").glob("*.tmp"))
