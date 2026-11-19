"""Turns, batching, the chunk cache, echo and line rendering. OpenAI is mocked by ToneEngine."""

import pytest

from gpw.longform import ChunkCache
from gpw.meeting_capture import TrackFile
from gpw.meeting_transcript import (
    MAX_TURN_SECONDS,
    Line,
    Turn,
    batch_turns,
    build_turns,
    detect_speech,
    drop_echo,
    is_echo,
    merge_lines,
    render_lines,
    to_lines,
    transcribe_turns,
    words,
)
from gpw.vad import Span
from meeting_fakes import APIConnectionError, ME_WORDS, THEM_WORDS, ToneEngine, make_meeting


def test_turns_follow_the_conversation():
    turns = build_turns({
        "me": [Span(1, 2), Span(2.5, 3), Span(9, 10)],
        "them": [Span(4, 7), Span(7.5, 8)],
    })
    assert [(t.track, t.start, t.end, len(t.parts)) for t in turns] == [
        ("me", 1, 3, 2), ("them", 4, 8, 2), ("me", 9, 10, 1),
    ]
    assert turns[0].speaker == "Me" and turns[1].speaker == "Them"
    assert turns[0].speech_seconds == pytest.approx(1.0 + 0.5 + 0.3)


def test_a_long_pause_or_a_long_turn_starts_a_new_line():
    assert len(build_turns({"them": [Span(0, 5), Span(20, 25)]})) == 2  # 15 s of silence
    many = [Span(i * 10, i * 10 + 9) for i in range(20)]
    turns = build_turns({"them": many})
    assert all(t.end - t.start <= MAX_TURN_SECONDS for t in turns) and len(turns) == 2


def test_cache_keys_name_the_audio():
    a = Turn("me", (Span(1, 2),))
    assert a.key("gpt-transcribe") == Turn("me", (Span(1, 2),)).key("gpt-transcribe")
    assert a.key("gpt-transcribe") != Turn("me", (Span(1, 2.5),)).key("gpt-transcribe")
    assert a.key("gpt-transcribe") != Turn("them", (Span(1, 2),)).key("gpt-transcribe")
    assert a.key("gpt-transcribe") != a.key("other-model")


def test_batches_hold_about_the_chunk_length_of_speech():
    turns = [Turn("me", (Span(i * 100, i * 100 + 60),)) for i in range(25)]  # 60 s of speech each
    batches = batch_turns(turns, 600)
    assert [len(b) for b in batches] == [10, 10, 5]
    assert sum(batches, []) == list(range(25))


def tracks_for(folder):
    return {t: TrackFile(folder / f"{t}.wav") for t in ("me", "them") if (folder / f"{t}.wav").is_file()}


def test_detect_speech_on_the_synthetic_call(tmp_path):
    folder = make_meeting(tmp_path)
    tracks = tracks_for(folder)
    me, them = detect_speech(tracks["me"]), detect_speech(tracks["them"])
    assert [(round(u.start, 1), round(u.end, 1)) for u in them.utterances] == [(3.8, 7.2), (11.8, 14.2)]
    assert len(me.utterances) == 3  # two real ones and the echo
    assert me.seconds == pytest.approx(16.0)


def test_transcribe_sends_only_speech_with_hints_and_context(tmp_path):
    folder = make_meeting(tmp_path, echo=False)
    tracks = tracks_for(folder)
    turns = build_turns({t: detect_speech(f).utterances for t, f in tracks.items()})
    engine = ToneEngine()
    texts, stats = transcribe_turns(
        turns, tracks, engine, ChunkCache(tmp_path / "work"), tmp_path / "work" / "audio",
        keywords=["Kavaliro"], languages=["en"], context="A Teams call", batch_seconds=5, workers=1,
    )
    assert texts == [ME_WORDS, THEM_WORDS, ME_WORDS, THEM_WORDS]
    assert (stats.requests, stats.cached, stats.batches) == (4, 0, 2)
    assert sum(c["seconds"] for c in engine.calls) == pytest.approx(stats.speech_seconds, abs=0.01)
    assert sum(c["seconds"] for c in engine.calls) < 11  # of a 16-second call: silence is not sent
    assert all(c["keywords"] == ["Kavaliro"] and c["languages"] == ["en"] for c in engine.calls)
    first, second = engine.calls[0]["prompt"], engine.calls[-1]["prompt"]
    assert first == "A Teams call."
    assert second.startswith("A Teams call. The preceding audio ended with:") and "legal" in second
    assert not list((tmp_path / "work" / "audio").iterdir())  # the uploaded clips are cleaned up


def test_a_failed_run_resumes_from_the_cache(tmp_path):
    folder = make_meeting(tmp_path, echo=False)
    tracks = tracks_for(folder)
    turns = build_turns({t: detect_speech(f).utterances for t, f in tracks.items()})
    cache = ChunkCache(tmp_path / "work")
    with pytest.raises(APIConnectionError):
        transcribe_turns(turns, tracks, ToneEngine(fail_on=2), cache, tmp_path / "audio", keywords=[], languages=[], workers=1)
    engine = ToneEngine()
    texts, stats = transcribe_turns(turns, tracks, engine, cache, tmp_path / "audio", keywords=[], languages=[], workers=1)
    # The first two came back before the failure; the worker may have finished the fourth too.
    assert stats.cached >= 2 and stats.cached + stats.requests == 4 and len(engine.calls) == stats.requests
    assert texts == [ME_WORDS, THEM_WORDS, ME_WORDS, THEM_WORDS]


def test_refused_hints_are_dropped_for_the_rest(tmp_path):
    class BadRequestError(Exception):
        pass

    class Picky(ToneEngine):
        def transcribe(self, path, prompt, keywords, languages):
            if keywords:
                self.calls.append({"refused": True})
                raise BadRequestError("too many keywords")
            return super().transcribe(path, prompt, keywords, languages)

    folder = make_meeting(tmp_path, echo=False)
    tracks = tracks_for(folder)
    turns = build_turns({t: detect_speech(f).utterances for t, f in tracks.items()})
    engine = Picky()
    texts, stats = transcribe_turns(turns, tracks, engine, ChunkCache(tmp_path / "w"), tmp_path / "a",
                                    keywords=["Kavaliro"], languages=[], workers=1)
    assert stats.keywords_dropped and sum(1 for c in engine.calls if c.get("refused")) == 1
    assert texts[0] == ME_WORDS


def line(speaker, start, end, text):
    return Line(speaker, start, end, text)


def test_echo_is_a_me_line_repeating_overlapping_them_speech():
    them = [line("Them", 12, 14, THEM_WORDS)]
    assert is_echo(line("Me", 12, 14, THEM_WORDS), them)
    assert is_echo(line("Me", 12.3, 13.9, "we will review the contract next week"), them)
    assert not is_echo(line("Me", 12, 14, "Sounds good, I will call you after lunch."), them)  # talking over them
    assert not is_echo(line("Me", 20, 22, THEM_WORDS), them)  # same words, but later: quoting them
    assert not is_echo(line("Me", 13, 13.5, "yes"), [line("Them", 12, 14, "No, not this week.")])
    assert is_echo(line("Me", 13, 13.5, "next week"), them)  # a short echo inside their line


def test_drop_echo_then_merge_rejoins_the_them_turn():
    lines = [
        line("Them", 0, 5, "First part."),
        line("Me", 5.2, 6, "first part"),  # the echo split the Them turn in two
        line("Them", 6.1, 9, "Second part."),
        line("Me", 10, 12, "My answer."),
    ]
    kept, dropped = drop_echo(lines)
    assert dropped == 1
    assert merge_lines(kept) == [line("Them", 0, 9, "First part. Second part."), line("Me", 10, 12, "My answer.")]


def test_lines_are_ordered_labeled_and_fixed():
    turns = [Turn("them", (Span(4, 7),)), Turn("me", (Span(0.8, 3),)), Turn("me", (Span(9, 10),))]
    lines = to_lines(turns, ["Them  says\nhi.", "Me about cavaliro.", "  "], lambda t: t.replace("cavaliro", "Kavaliro"))
    assert render_lines(lines) == "[00:00:00] Me: Me about Kavaliro.\n\n[00:00:04] Them: Them says hi.\n"
    assert render_lines([]) == ""


def test_words():
    assert words("It's 5 o'clock, OK?") == ["it's", "5", "o'clock", "ok"]
