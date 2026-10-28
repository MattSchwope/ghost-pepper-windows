from pathlib import Path

import pytest

from gpw.config import DEFAULT_CONFIG, load_config, parse_config


def test_load_example_config():
    config = load_config(Path("app/config.example.yaml"))
    assert config.openai.transcribe_model == "gpt-transcribe"
    assert config.openai.text_model == "gpt-6-luna"
    assert config.openai.summary_model == "gpt-6.1-sol"
    assert config.openai.api_key_env == "OPENAI_API_KEY"
    assert config.audio_prep == "light"
    assert config.languages == ["en"]
    assert config.debug_log_transcripts is False


def test_default_config_path_exists():
    assert DEFAULT_CONFIG.is_file()


def test_defaults_when_sections_missing():
    config = parse_config({})
    assert config.openai.transcribe_model == "gpt-transcribe"
    assert config.chunk_minutes == 10
    assert config.cleanup_enabled is False
    assert config.work_dir == config.data_dir / ".work"


def test_keywords_accept_comma_string():
    config = parse_config({"transcription": {"keywords": "Kavaliro, SOCOM ,"}})
    assert config.keywords == ["Kavaliro", "SOCOM"]


@pytest.mark.parametrize("value", [None, 3, "Poly BT700"])
def test_input_device_accepts_default_index_or_name(value):
    assert parse_config({"recording": {"input_device": value}}).input_device == value


@pytest.mark.parametrize("value", [True, 1.5, ["mic"]])
def test_bad_input_device_rejected(value):
    with pytest.raises(ValueError, match="input_device"):
        parse_config({"recording": {"input_device": value}})


def test_bad_audio_prep_rejected():
    with pytest.raises(ValueError, match="audio_prep"):
        parse_config({"transcription": {"audio_prep": "extreme"}})


def test_bad_chunk_minutes_rejected():
    with pytest.raises(ValueError, match="chunk_minutes"):
        parse_config({"transcription": {"chunk_minutes": 90}})


def test_missing_config_file_is_clear(tmp_path):
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        load_config(tmp_path / "nope.yaml")
