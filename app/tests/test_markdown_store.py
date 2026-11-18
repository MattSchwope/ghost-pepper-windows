from gpw.markdown_store import safe_slug, render_dictation_note


def test_safe_slug():
    assert safe_slug("Hello, World!") == "hello-world"


def test_render_dictation_note_contains_raw_and_cleaned():
    note = render_dictation_note("raw text", "cleaned text", profile="general_cleanup")
    assert "raw text" in note
    assert "cleaned text" in note
    assert "profile: general_cleanup" in note


from datetime import date, datetime

from gpw.markdown_store import append_daily_dictation, daily_log_path, render_daily_entry, render_daily_header


def test_render_daily_header_is_plain_markdown_with_front_matter():
    assert render_daily_header(date(2026, 10, 28)) == (
        "---\ntype: dictation-log\ndate: 2026-10-28\n---\n\n# Dictation 2026-10-28\n"
    )


def test_render_daily_entry():
    when = datetime(2026, 10, 28, 14, 3, 12)
    assert render_daily_entry(when, "Hello there.", "Hello there.", 9.6) == "\n## 14:03:12 (10 s)\n\nHello there.\n"
    with_raw = render_daily_entry(when, "Hello there.", "um hello there")
    assert with_raw.endswith("\n**Raw transcript:** um hello there\n")


def test_append_daily_dictation_writes_header_once(tmp_path):
    first = append_daily_dictation(tmp_path, datetime(2026, 10, 28, 9, 0, 0), "One.")
    second = append_daily_dictation(tmp_path, datetime(2026, 10, 28, 9, 5, 0), "Two.")
    assert first == second == daily_log_path(tmp_path, date(2026, 10, 28))
    assert first == tmp_path / "dictation" / "2026-10-28.md"
    text = first.read_text(encoding="utf-8")
    assert text.count("# Dictation 2026-10-28") == 1
    assert text.index("One.") < text.index("Two.")
    other_day = append_daily_dictation(tmp_path, datetime(2026, 10, 29, 9, 0, 0), "Three.")
    assert other_day.name == "2026-10-29.md"
