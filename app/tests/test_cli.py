"""CLI end-to-end with the OpenAI engine mocked. No network calls."""

import logging
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from gpw import cli, clipboard, logging_setup, recorder
from gpw.config import REPO_ROOT


class FakeEngine:
    model = "gpt-transcribe"

    def transcribe(self, path, prompt, keywords, languages):
        return "Hello from the fake engine."


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_engine", lambda config: FakeEngine())
    monkeypatch.setattr(logging_setup, "configure_logging", lambda logs_dir: None)
    data = tmp_path / "data"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f'app:\n  data_dir: "{data.as_posix()}"\n'
        "transcription:\n  audio_prep: light\n",
        encoding="utf-8",
    )
    return cfg


def _tone(path, seconds=2.0, sr=16_000):
    t = np.linspace(0, seconds, int(seconds * sr), endpoint=False)
    sf.write(str(path), 0.2 * np.sin(2 * np.pi * 300 * t), sr)
    return path


def test_transcribe_file_prints_text_and_saves_note(tmp_path, config_file, capsys):
    clip = _tone(tmp_path / "clip.wav")
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--config", str(config_file), "transcribe-file", str(clip)])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == "Hello from the fake engine."
    notes = list((tmp_path / "data" / "notes").glob("*.md"))
    assert len(notes) == 1


def test_note_file_name_stays_out_of_logs(tmp_path, config_file, caplog):
    # The note is named after its first words; logging that name would leak the transcript.
    caplog.set_level(logging.INFO)
    with pytest.raises(SystemExit):
        cli.main(["--config", str(config_file), "transcribe-file", str(_tone(tmp_path / "clip.wav"))])
    assert "Saved Markdown note" in caplog.text
    assert "fake" not in caplog.text.lower()


def test_transcribe_file_missing_audio_fails_clearly(tmp_path, config_file, capsys, caplog):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--config", str(config_file), "transcribe-file", str(tmp_path / "nope.m4a")])
    assert exit_info.value.code == 1
    assert "Audio file not found" in capsys.readouterr().err + caplog.text


def test_record_once_cues_and_uses_configured_mic(tmp_path, config_file, monkeypatch, capsys):
    beeps, calls, copied = [], {}, []
    monkeypatch.setitem(sys.modules, "winsound", SimpleNamespace(Beep=lambda hz, ms: beeps.append(hz)))

    def fake_record_wav(audio_dir, duration_seconds, sample_rate, channels, device, on_start):
        calls.update(device=device, seconds=duration_seconds)
        on_start()
        return _tone(tmp_path / "rec.wav")

    monkeypatch.setattr(recorder, "record_wav", fake_record_wav)
    monkeypatch.setattr(clipboard, "copy_text", copied.append)
    config_file.write_text(config_file.read_text() + 'recording:\n  input_device: "USB Condenser"\n')

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--config", str(config_file), "record-once", "--duration", "3"])
    assert exit_info.value.code == 0
    err = capsys.readouterr().err
    assert err.index("LISTENING for 3 seconds") < err.index("Done listening")
    assert beeps == [880, 440]
    assert calls == {"device": "USB Condenser", "seconds": 3}
    assert copied == ["Hello from the fake engine."]


def test_eval_prints_wer_table(tmp_path, config_file, capsys):
    clips = tmp_path / "clips"
    clips.mkdir()
    _tone(clips / "dictation-01.wav")
    (clips / "dictation-01.ref.txt").write_text("Hello from the fake engine.", encoding="utf-8")
    _tone(clips / "call-01.wav")
    (clips / "call-01.ref.txt").write_text("Hello from a real phone call.", encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        cli.main([
            "--config", str(config_file), "eval",
            "--clips", str(clips), "--prep", "off,light,strong", "--results", str(tmp_path / "results"),
        ])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    header = next(line for line in out.splitlines() if line.startswith("clip"))
    assert header.split() == ["clip", "off", "light", "strong"]
    assert re.search(r"dictation-01\s+0\.0%\s+0\.0%\s+0\.0%", out)
    # "a real phone call" vs "the fake engine": 3 substitutions + 1 deletion over 6 words.
    assert re.search(r"call-01\s+66\.7%\s+66\.7%\s+66\.7%", out)
    assert "mean all (2)" in out


def test_eval_without_clips_fails_clearly(tmp_path, config_file):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--config", str(config_file), "eval", "--clips", str(tmp_path / "empty")])
    assert exit_info.value.code == 1


def test_no_ollama_references_in_app():
    paths = [REPO_ROOT / "pyproject.toml", *(REPO_ROOT / "app").rglob("*")]
    hits = [
        p for p in paths
        if p.is_file() and p.suffix in {".py", ".yaml", ".toml", ".ps1", ".ahk", ".md", ".txt"}
        and p.name != Path(__file__).name
        and re.search(r"ollama|qwen", p.read_text(encoding="utf-8", errors="ignore"), re.I)
    ]
    assert hits == []


def test_latency_command_prints_report(tmp_path, config_file, capsys):
    from gpw.latency import append_record

    logs = tmp_path / "data" / "logs" / "dictation-latency.jsonl"
    for r2p in (1.2, 1.4, 1.6):
        append_record(logs, {"status": "pasted", "audio": 10.0, "release_to_paste": r2p, "api": 1.0})
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--config", str(config_file), "latency", "--last", "2"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "Result: 1.50 s -> met (only 2 so far; need 50)." in out


def test_build_service_dry_run_needs_no_key(tmp_path, config_file, monkeypatch):
    from gpw.config import load_config

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    service = cli.build_service(load_config(config_file), dry_run=True)
    try:
        assert service.engine.model == "dry-run"
        assert (tmp_path / "data" / "assets" / "start.wav").is_file()
        assert service.health()["dry_run"] is True
    finally:
        service.close()


def test_serve_without_api_key_fails_clearly(tmp_path, config_file, monkeypatch, capsys, caplog):
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)  # no repo .env to fall back on
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--config", str(config_file), "serve"])
    assert exit_info.value.code == 1
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().err + caplog.text


def test_dictionary_command_creates_the_file_and_reports(tmp_path, config_file, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--config", str(config_file), "dictionary"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "Created a starter dictionary." in out and "Terms: 5   Fixes: 2" in out
    assert (tmp_path / "data" / "dictionary.yaml").is_file()

    (tmp_path / "data" / "dictionary.yaml").write_text("terms: [Acme", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--config", str(config_file), "dictionary"])
    assert exit_info.value.code == 1
    assert "Error:" in capsys.readouterr().out


def test_transcribe_file_sends_dictionary_terms(tmp_path, config_file, monkeypatch):
    sent = []

    class Recording(FakeEngine):
        def transcribe(self, path, prompt, keywords, languages):
            sent.append(keywords)
            return super().transcribe(path, prompt, keywords, languages)

    monkeypatch.setattr(cli, "_engine", lambda config: Recording())
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "dictionary.yaml").write_text("terms: [Kavaliro, SOCOM]", encoding="utf-8")
    with pytest.raises(SystemExit):
        cli.main(["--config", str(config_file), "transcribe-file", str(_tone(tmp_path / "clip.wav"))])
    assert sent and sent[0] == ["Kavaliro", "SOCOM"]


def test_build_service_sets_up_the_dictionary_and_skips_auto_learn_in_dry_runs(tmp_path, config_file, monkeypatch):
    from gpw.config import load_config

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config_file.write_text(config_file.read_text(encoding="utf-8") + "dictionary:\n  auto_learn: true\n", encoding="utf-8")
    service = cli.build_service(load_config(config_file), dry_run=True)
    try:
        assert (tmp_path / "data" / "dictionary.yaml").is_file()
        assert service.dictionary is not None and service.learner is None
    finally:
        service.close()
