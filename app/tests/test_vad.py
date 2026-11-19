import numpy as np
import pytest

from gpw.vad import Span, frame_db, speech_spans, split_long, threshold_for, total_seconds, utterances

SR = 16_000


def tone(seconds, amp=0.2, freq=300):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    return (noise * rng.standard_normal(int(seconds * SR))).astype(np.float32)


def db_of(audio, block=SR):
    return frame_db((audio[i:i + block] for i in range(0, len(audio), block)), SR)


def test_frame_db_is_the_same_whatever_the_block_size():
    audio = np.concatenate([tone(1.0), silence(0.5)])
    a, b = db_of(audio, 777), db_of(audio, SR * 10)
    assert len(a) == len(b) == pytest.approx(1.5 / 0.03, abs=1)
    assert a == pytest.approx(b)
    assert a[5] == pytest.approx(20 * np.log10(0.2 / np.sqrt(2)), abs=0.1)
    assert a[-2] == pytest.approx(-120.0)


def test_threshold_follows_the_noise_floor_within_limits():
    assert threshold_for(np.full(100, -120.0)) == -50.0  # digital silence: the absolute floor
    assert threshold_for(np.full(100, -55.0)) == -43.0  # hiss 12 dB below
    assert threshold_for(np.full(100, -20.0)) == -30.0  # never above the cap
    assert threshold_for(np.zeros(0)) == -50.0


def test_speech_spans_find_speech_with_padding():
    audio = np.concatenate([silence(1.0, 0.001), tone(2.0), silence(2.0, 0.001), tone(1.0), silence(1.0, 0.001)])
    spans = speech_spans(db_of(audio))
    assert len(spans) == 2
    assert spans[0].start == pytest.approx(0.8, abs=0.05) and spans[0].end == pytest.approx(3.2, abs=0.05)
    assert spans[1].start == pytest.approx(4.8, abs=0.05) and spans[1].end == pytest.approx(6.2, abs=0.05)


def test_short_blips_are_dropped_and_short_gaps_bridged():
    audio = np.concatenate([silence(1.0), tone(0.1), silence(1.0), tone(1.0), silence(0.2), tone(1.0), silence(1.0)])
    spans = speech_spans(db_of(audio))
    assert len(spans) == 1
    assert spans[0].start == pytest.approx(1.9, abs=0.05) and spans[0].end == pytest.approx(4.5, abs=0.05)


def test_utterances_join_at_short_pauses_and_split_at_long_ones():
    spans = [Span(0, 2), Span(2.3, 4), Span(6, 7)]
    assert utterances(spans) == [Span(0, 4), Span(6, 7)]
    assert utterances(spans, pause=3.0) == [Span(0, 7)]


def test_long_speech_is_cut_at_its_quietest_moment():
    db = np.full(int(70 / 0.03), -20.0)
    quiet = int(25 / 0.03)
    db[quiet] = -45.0
    pieces = split_long(Span(0, 70), db)
    assert pieces[0] == Span(0, pytest.approx(25.0, abs=0.04))
    assert all(p.seconds <= 30.0 for p in pieces)
    assert pieces[-1].end == 70 and total_seconds(pieces) == pytest.approx(70)
    assert utterances([Span(0, 70)], db) == pieces
