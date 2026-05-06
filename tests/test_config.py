"""Tests for configuration module."""

from pathlib import Path

import pytest

from musiktool.config import Config, get_config


class TestGetConfig:
    def setup_method(self):
        get_config.cache_clear()

    def teardown_method(self):
        get_config.cache_clear()

    def test_no_config_file_uses_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

        cfg = get_config()
        assert cfg.data_dir == tmp_path / "data" / "musiktool"
        assert cfg.render_dir is None

    def test_reads_toml_values(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config" / "musiktool"
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text(
            'data_dir = "/custom/data"\nrender_dir = "/custom/render"\n'
        )
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

        cfg = get_config()
        assert cfg.data_dir == Path("/custom/data")
        assert cfg.render_dir == Path("/custom/render")

    def test_tilde_expansion(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config" / "musiktool"
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text('data_dir = "~/mydata"\n')
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

        cfg = get_config()
        assert cfg.data_dir == Path.home() / "mydata"

    def test_xdg_data_home_respected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))

        cfg = get_config()
        assert cfg.data_dir == tmp_path / "share" / "musiktool"

    def test_caching(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

        cfg1 = get_config()
        cfg2 = get_config()
        assert cfg1 is cfg2

    def test_cache_clear_reloads(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

        cfg1 = get_config()
        get_config.cache_clear()

        # Create config after first load
        config_dir = tmp_path / "config" / "musiktool"
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text('render_dir = "/new/render"\n')

        cfg2 = get_config()
        assert cfg1.render_dir is None
        assert cfg2.render_dir == Path("/new/render")

    def test_partial_config(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config" / "musiktool"
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text('render_dir = "/renders"\n')
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

        cfg = get_config()
        # data_dir falls back to XDG default when not in config
        assert cfg.data_dir == tmp_path / "data" / "musiktool"
        assert cfg.render_dir == Path("/renders")

    def test_frozen_dataclass(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

        cfg = get_config()
        with pytest.raises(AttributeError):
            cfg.data_dir = Path("/other")
