from pathlib import Path

import pytest

from gpw.evaluation import (
    Clip,
    discover_clips,
    format_table,
    normalize_for_wer,
    parse_prep_list,
    run_eval,
    source_type,
    word_error_rate,
)


def test_normalize_for_wer():
    assert normalize_for_wer("Hello, World!  It’s TS/SCI. 'quoted'") == "hello world it's ts sci quoted"


def test_word_error_rate_ignores_case_and_punctuation():
    assert word_error_rate("Hello there, Matt.", "hello there matt") == 0.0
    assert word_error_rate("one two three four", "one two tree four") == pytest.approx(0.25)


def test_word_error_rate_empty_reference():
    assert word_error_rate("", "") == 0.0
    assert word_error_rate("", "Thank you.") == 1.0


def test_source_type():
    assert source_type("dictation-car-01.m4a") == "dictation"
    assert source_type("Talk-panel-01.m4a") == "talk"
    assert source_type("call-01.mp3") == "call"
    assert source_type("random.m4a") == "other"


def test_discover_clips(tmp_path):
    (tmp_path / "call-01.m4a").write_bytes(b"x")
    (tmp_path / "call-01.ref.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "talk-01.mp3").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")
    clips, missing = discover_clips(tmp_path)
    assert [c.name for c in clips] == ["call-01"]
    assert [p.name for p in missing] == ["talk-01.mp3"]
    assert discover_clips(tmp_path / "absent") == ([], [])


def test_parse_prep_list():
    allowed = ("off", "light", "strong")
    assert parse_prep_list("off, light,off", allowed) == ["off", "light"]
    with pytest.raises(ValueError, match="Unknown prep"):
        parse_prep_list("off,max", allowed)
    with pytest.raises(ValueError):
        parse_prep_list(" , ", allowed)


def test_format_table_has_rows_and_means():
    clips = [Clip(Path("dictation-a.m4a"), Path("a.ref.txt")), Clip(Path("call-b.m4a"), Path("b.ref.txt"))]
    results = {
        ("dictation-a", "off"): 0.10, ("dictation-a", "light"): 0.05,
        ("call-b", "off"): 0.30,  # light failed for this clip
    }
    table = format_table(clips, ["off", "light"], results)
    lines = table.splitlines()
    assert lines[0].split() == ["clip", "off", "light"]
    assert "dictation-a" in table and "10.0%" in table and "5.0%" in table
    call_row = next(line for line in lines if line.startswith("call-b"))
    assert call_row.split()[1:] == ["30.0%", "-"]
    all_row = next(line for line in lines if line.startswith("mean all"))
    assert all_row.split()[-2:] == ["20.0%", "5.0%"]
    assert any(line.startswith("mean dictation (1)") for line in lines)


def test_run_eval_caches_and_survives_failures(tmp_path):
    clips_dir = tmp_path / "clips"
    clips_dir.mkdir()
    for name, ref in [("talk-1", "the quick brown fox"), ("talk-2", "jumps over")]:
        (clips_dir / f"{name}.m4a").write_bytes(b"x")
        (clips_dir / f"{name}.ref.txt").write_text(ref, encoding="utf-8")
    clips, _ = discover_clips(clips_dir)

    calls = []

    def transcribe(path, prep):
        calls.append((path.stem, prep))
        if path.stem == "talk-2" and prep == "strong":
            raise RuntimeError("boom")
        return "The quick brown box." if path.stem == "talk-1" else "jumps over"

    results = run_eval(clips, ["off", "strong"], transcribe, tmp_path / "results", "gpt-transcribe")
    assert results[("talk-1", "off")] == pytest.approx(0.25)
    assert results[("talk-2", "off")] == 0.0
    assert ("talk-2", "strong") not in results
    assert (tmp_path / "results" / "off" / "talk-1.gpt-transcribe.txt").is_file()

    calls.clear()
    run_eval(clips, ["off", "strong"], transcribe, tmp_path / "results", "gpt-transcribe")
    assert calls == [("talk-2", "strong")]  # only the failed one is retried
