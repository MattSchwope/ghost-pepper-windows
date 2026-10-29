from pathlib import Path

import pytest
import yaml

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


def test_bare_off_audio_prep_in_yaml_means_off():
    raw = yaml.safe_load("transcription:\n  audio_prep: off\n")
    assert raw["transcription"]["audio_prep"] is False
    assert parse_config(raw).audio_prep == "off"


def test_bad_audio_prep_rejected():
    with pytest.raises(ValueError, match="audio_prep"):
        parse_config({"transcription": {"audio_prep": True}})
    with pytest.raises(ValueError, match="audio_prep"):
        parse_config({"transcription": {"audio_prep": "extreme"}})


def test_bad_chunk_minutes_rejected():
    with pytest.raises(ValueError, match="chunk_minutes"):
        parse_config({"transcription": {"chunk_minutes": 90}})


def test_missing_config_file_is_clear(tmp_path):
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        load_config(tmp_path / "nope.yaml")


def test_example_config_dictation_settings():
    config = load_config(Path("app/config.example.yaml"))
    assert config.service_port == 8765
    assert config.hotkeys.dictate == "RCtrl" and config.hotkeys.cancel == "Esc"
    assert (config.hotkeys.tap_ms, config.hotkeys.double_tap_ms) == (250, 400)
    d = config.dictation
    assert d.audio_prep == "light" and d.upload_format == "flac"
    assert d.silence_peak_dbfs == -35 and d.min_speech_ms == 200
    assert d.daily_log is True and d.warm_mic is False and d.sounds is True
    assert d.max_seconds == 600


def test_dictation_defaults_when_sections_missing():
    config = parse_config({})
    assert config.service_port == 8765
    assert config.hotkeys.dictate == "RCtrl"
    assert config.dictation.audio_prep == "light"
    assert config.dictation.prewarm_connection is True


def test_dictation_bare_off_audio_prep_means_off():
    raw = yaml.safe_load("dictation:\n  audio_prep: off\n")
    assert parse_config(raw).dictation.audio_prep == "off"


@pytest.mark.parametrize(
    "raw, match",
    [
        ({"dictation": {"audio_prep": "extreme"}}, "dictation.audio_prep"),
        ({"dictation": {"upload_format": "mp3"}}, "dictation.upload_format"),
        ({"dictation": {"hands_free_max_minutes": 0}}, "hands_free_max_minutes"),
        ({"dictation": {"mic_open_timeout_seconds": -1}}, "mic_open_timeout_seconds"),
        ({"hotkeys": {"tap_ms": 0}}, "hotkeys.tap_ms"),
        ({"service": {"port": 80}}, "service.port"),
        ({"service": {"port": 70000}}, "service.port"),
    ],
)
def test_bad_dictation_settings_rejected(raw, match):
    with pytest.raises(ValueError, match=match):
        parse_config(raw)
