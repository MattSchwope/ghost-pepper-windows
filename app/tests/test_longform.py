import numpy as np
import pytest
import soundfile as sf

from gpw.longform import (
    Transcript,
    build_prompt,
    fmt_ts,
    paragraphs,
    render_timestamped,
    transcribe_file,
    work_key,
)


class FakeEngine:
    model = "gpt-transcribe"

    def __init__(self, fail_on: int | None = None):
        self.calls: list[tuple[str, str | None, list[str], list[str]]] = []
        self.fail_on = fail_on

    def transcribe(self, path, prompt, keywords, languages):
        n = len(self.calls)
        self.calls.append((path.name, prompt, keywords, languages))
        if self.fail_on is not None and n == self.fail_on:
            raise RuntimeError("network down")
        return f"Chunk {n} text."


def _tone(path, seconds, sr=16_000):
    t = np.linspace(0, seconds, int(seconds * sr), endpoint=False)
    sf.write(str(path), 0.2 * np.sin(2 * np.pi * 300 * t), sr)
    return path


def test_fmt_ts():
    assert fmt_ts(0) == "00:00:00"
    assert fmt_ts(3725.9) == "01:02:05"


def test_build_prompt():
    assert build_prompt(None, "") is None
    assert build_prompt("SOF Week panel", "") == "SOF Week panel."
    words = " ".join(f"w{i}" for i in range(100))
    prompt = build_prompt("Talk!", words)
    assert prompt.startswith("Talk! The preceding audio ended with:")
    assert "w39" not in prompt and "w40" in prompt and "w99" in prompt


def test_paragraphs_groups_sentences():
    text = "One. Two! Three? Four. Five. Six."
    assert paragraphs(text, per_para=5) == ["One. Two! Three? Four. Five.", "Six."]


def test_render_timestamped():
    out = render_timestamped([(0.0, "Hi. There."), (600.0, "Later.")])
    assert out == "[00:00:00]\nHi. There.\n\n[00:10:00]\nLater.\n"


def test_transcript_render_single_vs_multi():
    assert Transcript(5.0, [(0.0, "Just text.")]).render() == "Just text."
    multi = Transcript(900.0, [(0.0, "A."), (600.0, "B.")])
    assert multi.text == "A. B."
    assert multi.render().startswith("[00:00:00]")


def test_work_key_changes_with_settings(tmp_path):
    p = tmp_path / "my talk.m4a"
    a = work_key(p, 10, 1, "gpt-transcribe", "light", 10)
    assert " " not in a
    assert a != work_key(p, 10, 1, "gpt-transcribe", "strong", 10)
    assert a != work_key(p, 11, 1, "gpt-transcribe", "light", 10)


def test_transcribe_short_file_one_call(tmp_path):
    src = _tone(tmp_path / "clip.wav", 3)
    engine = FakeEngine()
    t = transcribe_file(engine, src, tmp_path / "work", prep="light", keywords=["Kavaliro"], languages=["en"])
    assert t.text == "Chunk 0 text."
    assert t.duration == pytest.approx(3.0, abs=0.05)
    assert engine.calls == [("chunk_000.mp3", None, ["Kavaliro"], ["en"])]
    assert not any((tmp_path / "work").iterdir())  # work folder cleaned up


def test_transcribe_long_file_resumes_from_cache(tmp_path):
    src = _tone(tmp_path / "talk.wav", 150)
    work = tmp_path / "work"

    failing = FakeEngine(fail_on=1)
    with pytest.raises(RuntimeError, match="network down"):
        transcribe_file(failing, src, work, prep="off", chunk_minutes=1, context="A panel")
    assert len(failing.calls) == 2

    engine = FakeEngine()
    t = transcribe_file(engine, src, work, prep="off", chunk_minutes=1, context="A panel")
    # Chunk 0 came from the cache; only chunks 1 and 2 were sent.
    assert [c[0] for c in engine.calls] == ["chunk_001.mp3", "chunk_002.mp3"]
    assert [s for s, _ in t.segments] == [0.0, 60.0, 120.0]
    assert t.segments[0][1] == "Chunk 0 text."
    # Context and the previous chunk's tail carry over.
    assert engine.calls[0][1] == 'A panel. The preceding audio ended with: "Chunk 0 text."'


def test_transcribe_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        transcribe_file(FakeEngine(), tmp_path / "nope.m4a", tmp_path / "work")
