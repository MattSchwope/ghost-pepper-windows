import pytest

from gpw.latency import append_record, format_report, load_records, percentile, summarize


def pasted(audio, r2p, **extra):
    return {"status": "pasted", "audio": audio, "release_to_paste": r2p, "api": r2p - 0.2, "server": 0.8,
            "prep": 0.01, "record_end": 0.03, "paste": 0.05, "clipboard_restored": True, **extra}


def test_percentile():
    assert percentile([], 50) is None
    assert percentile([3.0], 90) == 3.0
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([1, 2, 3, 4, 5], 90) == pytest.approx(4.6)


def test_records_round_trip_and_bad_lines_are_skipped(tmp_path):
    path = tmp_path / "logs" / "dictation-latency.jsonl"
    append_record(path, {"id": 1, "status": "pasted"})
    with path.open("a", encoding="utf-8") as f:
        f.write("not json\n[1, 2]\n")
    append_record(path, {"id": 2, "status": "skipped"})
    assert [r["id"] for r in load_records(path)] == [1, 2]
    assert load_records(tmp_path / "missing.jsonl") == []


def test_summarize_buckets_ten_second_clips():
    records = [
        pasted(10.2, 1.2), pasted(9.0, 1.4), pasted(12.9, 1.6),  # ~10 s clips
        pasted(3.0, 0.9), pasted(60.0, 4.0, clipboard_restored=False),
        {"status": "skipped", "audio": 1.0},
        {"status": "error", "audio": 8.0},
        {"status": "ready", "audio": 10.0},  # never pasted (cancelled), not counted
    ]
    s = summarize(records)
    assert (s["pasted"], s["skipped"], s["errors"]) == (5, 1, 1)
    assert s["all"]["count"] == 5 and s["all"]["median"] == 1.4
    assert s["ten"]["count"] == 3 and s["ten"]["median"] == 1.4
    assert s["ten"]["api"] == pytest.approx(1.2)
    assert (s["clipboard_checked"], s["clipboard_restored"]) == (5, 4)


def test_dry_runs_are_left_out(tmp_path):
    records = [
        pasted(4.9, 0.08, api=0.0, model="dry-run", server=None),
        pasted(5.9, 0.06, api=0.0, server=None),  # logged before records carried the model
        {"status": "skipped", "audio": 1.0, "model": "dry-run"},
        pasted(3.1, 1.56, model="gpt-transcribe"),
        pasted(2.3, 1.73, model="gpt-transcribe"),
        pasted(3.6, 1.50, model="gpt-transcribe"),
    ]
    s = summarize(records)
    assert (s["pasted"], s["skipped"], s["dry_runs"]) == (3, 0, 3)
    assert s["all"]["median"] == 1.56
    assert "Dry runs (not counted): 3" in format_report(s, tmp_path / "x.jsonl")


def test_report_says_when_the_target_is_met_but_too_few(tmp_path):
    text = format_report(summarize([pasted(10.0, 1.3)] * 12), tmp_path / "x.jsonl")
    assert "Median release-to-paste" in text
    assert "Result: 1.30 s -> met (only 12 so far; need 50)." in text
    assert "Clipboard restored after paste: 12 of 12" in text


def test_report_says_when_the_target_is_missed(tmp_path):
    text = format_report(summarize([pasted(10.0, 2.1)] * 50), tmp_path / "x.jsonl")
    assert "Result: 2.10 s -> missed." in text


def test_report_with_no_data(tmp_path):
    text = format_report(summarize([]), tmp_path / "x.jsonl")
    assert "Pasted: 0" in text
    assert "no ~10 s dictations logged yet" in text
