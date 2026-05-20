import pytest

from gpw.recorder import record_wav


def test_record_wav_rejects_non_positive_duration(tmp_path):
    with pytest.raises(ValueError, match="duration"):
        record_wav(tmp_path, duration_seconds=0)
