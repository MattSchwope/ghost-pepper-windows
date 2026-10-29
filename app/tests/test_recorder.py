import numpy as np
import pytest

from gpw.recorder import peak_dbfs, pick_input_device

# Shaped like sounddevice.query_devices(): hostapi 0 is MME (the default), 1 is WASAPI.
DEVICES = [
    {"name": "Microsoft Sound Mapper - Input", "max_input_channels": 2, "hostapi": 0},
    {"name": "Microphone (USB Condenser Micro", "max_input_channels": 1, "hostapi": 0},
    {"name": "Headset Microphone (Poly BT700)", "max_input_channels": 1, "hostapi": 0},
    {"name": "Speakers (Realtek(R) Audio)", "max_input_channels": 0, "hostapi": 0},
    {"name": "Headset Microphone (Poly BT700)", "max_input_channels": 2, "hostapi": 1},
    {"name": "Microphone (USB Condenser Microphone)", "max_input_channels": 2, "hostapi": 1},
]


@pytest.mark.parametrize("spec", [None, ""])
def test_pick_input_device_unset_means_windows_default(spec):
    assert pick_input_device(spec, DEVICES, default_hostapi=0) is None


def test_pick_input_device_name_is_case_insensitive_and_prefers_default_api():
    assert pick_input_device("poly bt700", DEVICES, default_hostapi=0) == 2
    assert pick_input_device("poly bt700", DEVICES, default_hostapi=1) == 4


def test_pick_input_device_falls_back_to_other_api():
    # Only the WASAPI name is long enough to match.
    assert pick_input_device("Condenser Microphone", DEVICES, default_hostapi=0) == 5


def test_pick_input_device_index_must_be_a_mic():
    assert pick_input_device(1, DEVICES, default_hostapi=0) == 1
    with pytest.raises(ValueError, match="not a microphone"):
        pick_input_device(3, DEVICES, default_hostapi=0)
    with pytest.raises(ValueError, match="not a microphone"):
        pick_input_device(99, DEVICES, default_hostapi=0)


def test_pick_input_device_unknown_name_lists_mics():
    with pytest.raises(ValueError, match="Poly BT700") as err:
        pick_input_device("Blue Yeti", DEVICES, default_hostapi=0)
    assert "Speakers" not in str(err.value)


def test_peak_dbfs():
    assert peak_dbfs(np.zeros(1600, dtype=np.float32)) == -120.0
    assert peak_dbfs(np.array([], dtype=np.float32)) == -120.0
    assert peak_dbfs(np.array([0.0, -0.5, 0.25])) == pytest.approx(-6.02, abs=0.01)
    # The failed 2026-10-28 test clip peaked at 0.00964: well under the quiet warning line.
    assert peak_dbfs(np.array([0.00964])) == pytest.approx(-40.3, abs=0.1)
