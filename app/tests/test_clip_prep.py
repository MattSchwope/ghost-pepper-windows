import io

import numpy as np
import pytest
import soundfile as sf

from gpw import assets, audio
from gpw.clip_prep import (
    _next_fast_len,
    assess,
    encode,
    ffmpeg_filter,
    frame_peaks,
    highpass,
    level,
    prepare_clip,
    trim,
)

SR = 16_000


def tone(freq, seconds, amp=0.2, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds, sr=SR):
    return np.zeros(int(seconds * sr), dtype=np.float32)


def rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(np.square(x, dtype=np.float64))))


def speech_like(seconds=1.0, lead=0.5, tail=0.5, amp=0.2):
    # Speech from the USB Condenser mic peaked near -14 dBFS (amp 0.2); its silence is near-digital zero.
    return np.concatenate([silence(lead), tone(220, seconds, amp), silence(tail)])


def test_frame_peaks():
    x = np.array([0.1, -0.5, 0.2, 0.0, 0.3], dtype=np.float32)
    assert frame_peaks(x, 2).tolist() == pytest.approx([0.5, 0.2, 0.3])
    assert frame_peaks(np.zeros(0, dtype=np.float32), 2).size == 0


def test_assess_skips_digital_silence():
    check = assess(silence(2.0), SR)
    assert check.skip_reason == "quiet"
    assert check.peak_dbfs == -120.0


def test_assess_skips_quiet_room():
    rng = np.random.default_rng(1)
    room = (rng.standard_normal(2 * SR) * 10 ** (-55 / 20)).astype(np.float32)
    assert assess(room, SR).skip_reason == "quiet"


def test_assess_skips_a_click():
    x = silence(1.0)
    x[8000:8000 + 480] = 0.5  # 30 ms
    check = assess(x, SR)
    assert check.skip_reason == "too_little_speech"
    assert check.speech_ms < 200


def test_assess_skips_the_start_cue_alone():
    # A tap records only the start beep. It must not reach the API.
    beep = assets.tone(*assets.SOUNDS["start"], sample_rate=SR)
    assert assess(np.concatenate([silence(0.3), beep, silence(0.3)]), SR).skip_reason == "too_little_speech"


def test_assess_keeps_speech():
    check = assess(speech_like(0.6), SR)
    assert check.skip_reason is None
    assert check.peak_dbfs == pytest.approx(-14.0, abs=0.1)
    assert 560 <= check.speech_ms <= 640


def test_assess_threshold_is_configurable():
    quiet_speech = speech_like(1.0, amp=0.01)  # -40 dBFS
    assert assess(quiet_speech, SR, threshold_dbfs=-35).skip_reason == "quiet"
    assert assess(quiet_speech, SR, threshold_dbfs=-45).skip_reason is None


def test_trim_keeps_padding_around_speech():
    x = speech_like(1.0, lead=1.0, tail=1.0)
    out = trim(x, SR, pad_ms=300)
    assert len(out) / SR == pytest.approx(1.6, abs=0.05)
    assert np.abs(out).max() == pytest.approx(0.2, abs=0.01)


def test_trim_without_speech_returns_input():
    x = silence(1.0)
    assert trim(x, SR) is x


def test_next_fast_len_is_smallest_5_smooth():
    def smooth(n):
        for p in (2, 3, 5):
            while n % p == 0:
                n //= p
        return n == 1

    for n in range(1, 400):
        got = _next_fast_len(n)
        assert got >= n and smooth(got)
        assert not any(smooth(m) for m in range(n, got))


def test_highpass_cuts_rumble_and_keeps_voice():
    rumble, voice = tone(30, 2.0), tone(1000, 2.0)
    mid = slice(SR // 2, -SR // 2)
    assert rms_db(highpass(rumble, SR)[mid]) - rms_db(rumble[mid]) < -15
    assert rms_db(highpass(voice, SR)[mid]) - rms_db(voice[mid]) == pytest.approx(0, abs=0.1)
    # Butterworth: -3 dB at the cutoff, like ffmpeg highpass=f=80.
    at_cutoff = tone(80, 2.0)
    assert rms_db(highpass(at_cutoff, SR)[mid]) - rms_db(at_cutoff[mid]) == pytest.approx(-3.0, abs=0.3)
    assert len(highpass(voice, SR)) == len(voice)


def test_level_lifts_quiet_speech_to_target_and_caps_gain():
    out = level(tone(300, 12.0, amp=0.05), SR)
    assert np.abs(out[5 * SR:-5 * SR]).max() == pytest.approx(0.9, abs=0.05)
    # Gain stops at 20x, so a near-silent hiss is not blown up to full scale.
    hiss = level(tone(300, 12.0, amp=0.001), SR)
    assert np.abs(hiss).max() == pytest.approx(0.02, abs=0.002)


def test_level_evens_out_a_quiet_then_loud_speaker():
    x = np.concatenate([tone(300, 8.0, amp=0.05), tone(300, 8.0, amp=0.4)])
    out = level(x, SR)
    before = rms_db(x[11 * SR:12 * SR]) - rms_db(x[4 * SR:5 * SR])
    after = rms_db(out[11 * SR:12 * SR]) - rms_db(out[4 * SR:5 * SR])
    assert before == pytest.approx(18.1, abs=0.1)
    assert abs(after) < 3


def test_light_prep_tracks_ffmpeg_light_chain(tmp_path):
    """Open question 5: numpy light prep should land near ffmpeg's, so eval results carry over."""
    x = np.concatenate([tone(300, 4.0, amp=0.05), tone(300, 4.0, amp=0.4), tone(300, 4.0, amp=0.1)])
    ours = level(highpass(x, SR), SR)

    src, dst = tmp_path / "in.wav", tmp_path / "out.wav"
    sf.write(str(src), x, SR, subtype="FLOAT")
    audio.clean_to_wav(audio.find_ffmpeg(), src, dst, "light")
    theirs, rate = sf.read(str(dst), dtype="float32")
    assert rate == SR

    frame = SR // 4
    n = min(len(ours), len(theirs)) // frame
    ours_db = np.array([rms_db(ours[i * frame:(i + 1) * frame]) for i in range(2, n - 2)])
    theirs_db = np.array([rms_db(theirs[i * frame:(i + 1) * frame]) for i in range(2, n - 2)])
    assert np.median(np.abs(ours_db - theirs_db)) < 1.5
    assert np.abs(ours_db - theirs_db).max() < 4.0


def test_light_prep_leaves_short_clips_unboosted_like_ffmpeg():
    # dynaudnorm fades gain in from 1.0 over ~2 s at each end, so a 3 s clip gets no boost at all.
    x = tone(300, 3.0, amp=0.05)
    assert rms_db(level(x, SR)) == pytest.approx(rms_db(x), abs=0.2)


def test_prepare_clip_light_trims_and_levels():
    clip = prepare_clip(speech_like(10.0, lead=1.0, tail=1.0, amp=0.05), SR, "light")
    assert clip.skip_reason is None
    assert clip.raw_seconds == pytest.approx(12.0)
    assert clip.seconds == pytest.approx(10.6, abs=0.05)
    assert np.abs(clip.audio).max() == pytest.approx(0.9, abs=0.1)


def test_prepare_clip_off_sends_raw_audio():
    x = speech_like(1.0)
    clip = prepare_clip(x, SR, "off")
    np.testing.assert_array_equal(clip.audio, x)


def test_prepare_clip_skipped_clip_is_empty():
    clip = prepare_clip(silence(2.0), SR, "light")
    assert clip.skip_reason == "quiet"
    assert clip.audio.size == 0
    assert clip.raw_seconds == pytest.approx(2.0)


def test_prepare_clip_strong_uses_ffmpeg_filter():
    calls = []

    def strong(x, sr):
        calls.append(len(x))
        return x * 0.5, 16_000

    clip = prepare_clip(speech_like(1.0), SR, "strong", strong=strong)
    assert len(calls) == 1
    assert clip.sample_rate == 16_000
    with pytest.raises(ValueError, match="ffmpeg"):
        prepare_clip(speech_like(1.0), SR, "strong")
    with pytest.raises(ValueError, match="Unknown audio prep level"):
        prepare_clip(speech_like(1.0), SR, "extreme")


def test_ffmpeg_filter_runs_strong_chain(tmp_path):
    out, rate = ffmpeg_filter(tone(300, 1.0), SR, "strong", tmp_path)
    assert rate == audio.SAMPLE_RATE
    assert len(out) / rate == pytest.approx(1.0, abs=0.05)
    assert list(tmp_path.iterdir()) == []  # temp files cleaned up


@pytest.mark.parametrize("fmt", ["flac", "wav"])
def test_encode_round_trips(fmt):
    x = speech_like(1.0)
    data = encode(x, SR, fmt)
    back, rate = sf.read(io.BytesIO(data), dtype="float32")
    assert rate == SR
    assert len(back) == len(x)
    np.testing.assert_allclose(back, x, atol=1e-4)


def test_flac_is_smaller_than_wav():
    x = speech_like(3.0)
    assert len(encode(x, SR, "flac")) < 0.7 * len(encode(x, SR, "wav"))
