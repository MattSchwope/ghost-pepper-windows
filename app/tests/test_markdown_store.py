from gpw.markdown_store import safe_slug, render_dictation_note


def test_safe_slug():
    assert safe_slug("Hello, World!") == "hello-world"


def test_render_dictation_note_contains_raw_and_cleaned():
    note = render_dictation_note("raw text", "cleaned text", profile="general_cleanup")
    assert "raw text" in note
    assert "cleaned text" in note
    assert "profile: general_cleanup" in note
