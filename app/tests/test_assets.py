import struct

import numpy as np
import pytest
import soundfile as sf

from gpw.assets import ICONS, SOUND_RATE, SOUNDS, ico_bytes, tone, write_assets


def test_tone_length_volume_and_gaps():
    x = tone([880, 0, 440], 50)
    assert len(x) == 3 * (SOUND_RATE * 50 // 1000)
    assert np.abs(x).max() <= 0.2 + 1e-6
    third = len(x) // 3
    assert np.all(x[third:2 * third] == 0)
    assert x[0] == 0 and abs(x[-1]) < 1e-3  # faded, so no click


def test_cues_are_short():
    # The start cue plays while the mic listens; it must stay under the 200 ms speech minimum.
    for freqs, ms in SOUNDS.values():
        assert len(freqs) * ms <= 240
    assert len(SOUNDS["start"][0]) * SOUNDS["start"][1] < 200


def test_ico_bytes_is_a_valid_32_bit_icon():
    data = ico_bytes((229, 57, 53), size=32)
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    width, height, _, _, planes, bits, size, offset = struct.unpack_from("<BBBBHHII", data, 6)
    assert (reserved, kind, count) == (0, 1, 1)
    assert (width, height, planes, bits, offset) == (32, 32, 1, 32, 22)
    assert size == len(data) - 22
    header_size, w, h = struct.unpack_from("<Iii", data, 22)
    assert (header_size, w, h) == (40, 32, 64)
    pixels = np.frombuffer(data, dtype=np.uint8, count=32 * 32 * 4, offset=62).reshape(32, 32, 4)
    assert pixels[16, 16].tolist() == [53, 57, 229, 255]  # BGRA, opaque centre
    assert pixels[0, 0, 3] == 0  # transparent corner


def test_write_assets(tmp_path):
    folder = write_assets(tmp_path / "assets")
    for name in SOUNDS:
        info = sf.info(str(folder / f"{name}.wav"))
        assert info.samplerate == SOUND_RATE
    for name in ICONS:
        assert (folder / f"{name}.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"
    write_assets(folder)  # safe to run on every start


@pytest.mark.parametrize("name", ["idle", "recording", "handsfree", "busy"])
def test_every_tray_state_has_an_icon(name):
    assert name in ICONS
