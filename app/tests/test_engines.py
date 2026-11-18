from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gpw.cleaner import clean_text, load_prompt
from gpw.config import PROMPT_DIR, OpenAIConfig
from gpw.engines import OpenAIEngine, clean_keywords, estimate_cost
from gpw.openai_client import load_dotenv, make_client, parse_dotenv, require_api_key
from gpw.text_client import OpenAITextClient


def test_clean_keywords_strips_bad_chars_and_dupes():
    assert clean_keywords(["Kavaliro", " <SOCOM>\n", "", "Kavaliro", "TS/SCI"]) == ["Kavaliro", "SOCOM", "TS/SCI"]


def test_estimate_cost():
    assert estimate_cost("gpt-transcribe", 10) == pytest.approx(0.045)
    assert estimate_cost("unknown-model", 10) is None


def test_openai_engine_sends_keywords_languages_prompt(tmp_path):
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"fake")
    client = MagicMock()
    client.audio.transcriptions.create.return_value = SimpleNamespace(text="  hello world \n")

    engine = OpenAIEngine(client, "gpt-transcribe")
    out = engine.transcribe(clip, "A talk.", ["Kavaliro"], ["en"])

    assert out == "hello world"
    kwargs = client.audio.transcriptions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-transcribe"
    assert kwargs["keywords"] == ["Kavaliro"]
    assert kwargs["languages"] == ["en"]
    assert kwargs["prompt"] == "A talk."


def test_openai_engine_omits_empty_options(tmp_path):
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"fake")
    client = MagicMock()
    client.audio.transcriptions.create.return_value = SimpleNamespace(text=None)

    assert OpenAIEngine(client).transcribe(clip, None, [], []) == ""
    kwargs = client.audio.transcriptions.create.call_args.kwargs
    assert set(kwargs) == {"model", "file"}


def test_text_client_uses_responses_api():
    client = MagicMock()
    client.responses.create.return_value = SimpleNamespace(output_text=" Clean. ")
    out = OpenAITextClient(client, "gpt-6-luna", "none").complete("Fix it.", "um clean")
    assert out == "Clean."
    kwargs = client.responses.create.call_args.kwargs
    assert kwargs == {"model": "gpt-6-luna", "instructions": "Fix it.", "input": "um clean", "reasoning": {"effort": "none"}}


def test_clean_text_skips_empty_input():
    client = MagicMock()
    assert clean_text(OpenAITextClient(client, "m"), "   ", "prompt") == ""
    client.responses.create.assert_not_called()


def test_load_prompt_profile():
    assert "Return only the cleaned text" in load_prompt(PROMPT_DIR, "general_cleanup")
    with pytest.raises(FileNotFoundError):
        load_prompt(PROMPT_DIR, "does_not_exist")


def test_parse_dotenv():
    text = "# comment\nOPENAI_API_KEY='sk-abc'\n\nOTHER = \"x=y\"\nnot a pair\n"
    assert parse_dotenv(text) == {"OPENAI_API_KEY": "sk-abc", "OTHER": "x=y"}


def test_load_dotenv_does_not_override_existing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GPW_TEST_A=fromfile\nGPW_TEST_B=fromfile\n", encoding="utf-8")
    monkeypatch.setenv("GPW_TEST_A", "fromenv")
    monkeypatch.delenv("GPW_TEST_B", raising=False)
    load_dotenv(env)
    import os

    assert os.environ["GPW_TEST_A"] == "fromenv"
    assert os.environ["GPW_TEST_B"] == "fromfile"
    monkeypatch.delenv("GPW_TEST_B")


def test_require_api_key_errors_clearly(monkeypatch):
    monkeypatch.delenv("GPW_TEST_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GPW_TEST_KEY is not set"):
        require_api_key("GPW_TEST_KEY")
    monkeypatch.setenv("GPW_TEST_KEY", "sk-...")  # the .env.example placeholder
    with pytest.raises(RuntimeError):
        require_api_key("GPW_TEST_KEY")


def test_make_client_reads_key_from_configured_env(monkeypatch):
    monkeypatch.setenv("GPW_TEST_KEY", "sk-test-not-real")
    client = make_client(OpenAIConfig(api_key_env="GPW_TEST_KEY", max_retries=2, timeout_seconds=30))
    assert client.api_key == "sk-test-not-real"
    assert client.max_retries == 2


def _raw_client(text, headers):
    client = MagicMock()
    client.with_options.return_value = client
    raw = MagicMock()
    raw.parse.return_value = SimpleNamespace(text=text)
    raw.headers = headers
    client.audio.transcriptions.with_raw_response.create.return_value = raw
    return client


def test_transcribe_clip_sends_bytes_and_reads_server_time():
    client = _raw_client(" Hello there. ", {"openai-processing-ms": "850"})
    result = OpenAIEngine(client).transcribe_clip("dictation.flac", b"fLaC", ["Kavaliro"], ["en"], timeout=31.5)
    assert result.text == "Hello there."
    assert result.server_seconds == 0.85
    assert result.api_seconds >= 0
    client.with_options.assert_called_once_with(timeout=31.5, max_retries=1)
    kwargs = client.audio.transcriptions.with_raw_response.create.call_args.kwargs
    assert kwargs == {"file": ("dictation.flac", b"fLaC"), "model": "gpt-transcribe", "keywords": ["Kavaliro"], "languages": ["en"]}


def test_transcribe_clip_without_server_header():
    client = _raw_client(None, {})
    result = OpenAIEngine(client).transcribe_clip("dictation.wav", b"RIFF", [], [], timeout=30)
    assert result.text == "" and result.server_seconds is None
    assert set(client.audio.transcriptions.with_raw_response.create.call_args.kwargs) == {"file", "model"}


def test_server_seconds_ignores_junk():
    from gpw.engines import server_seconds

    assert server_seconds({"openai-processing-ms": "1234"}) == 1.234
    assert server_seconds({"openai-processing-ms": "soon"}) is None
    assert server_seconds(None) is None


def test_prewarm_lists_models_with_short_timeout():
    client = MagicMock()
    client.with_options.return_value = client
    OpenAIEngine(client).prewarm()
    client.with_options.assert_called_once_with(timeout=5, max_retries=0)
    client.models.list.assert_called_once_with()


def test_dry_run_engine_reports_duration_without_network():
    import io

    import numpy as np
    import soundfile as sf

    from gpw.engines import DryRunEngine

    buf = io.BytesIO()
    sf.write(buf, np.zeros(32_000, dtype=np.float32), 16_000, format="FLAC", subtype="PCM_16")
    result = DryRunEngine().transcribe_clip("dictation.flac", buf.getvalue(), [], [], timeout=30)
    assert result.text == "Dry run: heard 2.0 seconds of audio."


def test_make_client_with_keepalive(monkeypatch):
    from openai import DefaultHttpxClient

    monkeypatch.setenv("GPW_TEST_KEY", "sk-test-not-real")
    client = make_client(OpenAIConfig(api_key_env="GPW_TEST_KEY"), keepalive_seconds=120)
    assert isinstance(client._client, DefaultHttpxClient)
