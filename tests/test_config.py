from pathlib import Path

import pytest

from ayran_network.config import _CONFIG_PATH_ENV, load_config


def test_default_config() -> None:
    config = load_config(None)
    assert config.poll_interval == 1.0
    assert config.history_size == 60
    assert config.focus_extend_defaults is True
    assert config.geoip_enabled is True
    assert config.focus_apps == ()


def test_load_explicit_path(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text('[general]\npoll_interval = 2.5\n[focus]\napps = ["TestApp"]\n')
    config = load_config(config_file)
    assert config.poll_interval == 2.5
    assert config.focus_apps == ("TestApp",)
    assert config.source_path == str(config_file)


def test_load_env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = tmp_path / "env_config.toml"
    config_file.write_text("[general]\nhistory_size = 100\n")
    monkeypatch.setenv(_CONFIG_PATH_ENV, str(config_file))

    config = load_config()
    assert config.history_size == 100


def test_invalid_toml(tmp_path: Path) -> None:
    config_file = tmp_path / "invalid.toml"
    config_file.write_text("[general]\ninvalid toml")

    config = load_config(config_file)
    assert config.poll_interval == 1.0
    assert config.source_path == str(config_file)


def test_missing_file() -> None:
    config = load_config("nonexistent_file.toml")
    assert config.poll_interval == 1.0


def test_types_parsing(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
    [general]
    poll_interval = "not_a_float"
    history_size = "not_an_int"
    
    [focus]
    apps = "app1, app2"
    extend_defaults = "not_a_bool"
    """)

    config = load_config(config_file)
    assert config.poll_interval == 1.0  # default fallback
    assert config.history_size == 60  # default fallback
    assert config.focus_apps == ("app1", "app2")
    assert config.focus_extend_defaults is True  # default fallback
