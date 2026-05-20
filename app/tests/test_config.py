from pathlib import Path
from gpw.config import load_config


def test_load_example_config():
    config = load_config(Path("app/config.example.yaml"))
    assert config.model_size
    assert config.audio_dir
    assert config.cleanup_model
