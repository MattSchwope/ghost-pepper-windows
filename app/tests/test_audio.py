import numpy as np
import pytest
import soundfile as sf

from gpw import audio


def test_plan_cuts_short_file_is_one_chunk():
    assert audio.plan_cuts(300.0, [], 600) == [0.0, 300.0]


def test_plan_cuts_avoids_tiny_final_chunk():
    # 680s is within 15% of a 600s chunk, so no cut.
    assert audio.plan_cuts(680.0, [], 600) == [0.0, 680.0]


def test_plan_cuts_without_pauses_cuts_at_target():
    assert audio.plan_cuts(1500.0, [], 600) == [0.0, 600.0, 1200.0, 1500.0]


def test_plan_cuts_prefers_longer_pause_near_target():
    silences = [(590.0, 590.4), (620.0, 622.0), (900.0, 905.0)]
    cuts = audio.plan_cuts(1000.0, silences, 600)
    assert cuts == [0.0, 621.0, 1000.0]


def test_parse_silences():
    stderr = (
        "[silencedetect @ 0x1] silence_start: -0.01\n"
        "[silencedetect @ 0x1] silence_end: 1.5 | silence_duration: 1.51\n"
        "[silencedetect @ 0x1] silence_start: 10.2\n"
        "[silencedetect @ 0x1] silence_end: 11 | silence_duration: 0.8\n"
    )
    assert audio.parse_silences(stderr) == [(0.0, 1.5), (10.2, 11.0)]


def test_filter_chain_rejects_unknown_level():
    with pytest.raises(ValueError, match="Unknown audio prep level"):
        audio.filter_chain("extreme")


@pytest.mark.parametrize("level", ["off", "light", "strong"])
def test_clean_to_wav_and_export_chunk(tmp_path, level):
    """Runs the bundled ffmpeg on a synthetic tone. No network."""
    src = tmp_path / "tone.wav"
    t = np.linspace(0, 2.0, 2 * 44_100, endpoint=False)
    sf.write(str(src), np.stack([0.2 * np.sin(2 * np.pi * 440 * t)] * 2, axis=1), 44_100)

    ff = audio.find_ffmpeg()
    wav = tmp_path / "clean.wav"
    audio.clean_to_wav(ff, src, wav, level)
    assert audio.wav_duration(wav) == pytest.approx(2.0, abs=0.05)

    mp3 = tmp_path / "chunk.mp3"
    audio.export_chunk(ff, wav, 0.5, 1.5, mp3)
    assert mp3.stat().st_size > 0
