"""Prompt 02 tests — configuration loading."""
import os

import pytest

from core.config import Config


def test_defaults_are_sane():
    cfg = Config.default()
    assert cfg.service_start == "16:00"
    assert cfg.mock_vision is True
    assert cfg.conf_threshold == 0.6


def test_default_reads_env_var_for_global_recipes_db(monkeypatch):
    monkeypatch.setenv("OPEN_GLOBAL_RECIPES_DB", "/tmp/recipes.db")
    assert Config.default().global_recipes_db == "/tmp/recipes.db"


def test_missing_yaml_file_returns_defaults():
    cfg = Config.from_yaml("/nonexistent/path/config.yaml")
    assert cfg.db_path == Config.default().db_path


def test_empty_path_returns_defaults():
    assert Config.from_yaml("").serve_port == 8788


def test_yaml_overrides_known_keys_only(tmp_path):
    pytest.importorskip("yaml")
    path = tmp_path / "config.yaml"
    path.write_text(
        "db_path: /tmp/pass.db\nservice_start: '17:30'\n"
        "wash_backlog_threshold: 6\nbogus_key: 99\n",
        encoding="utf-8",
    )
    cfg = Config.from_yaml(str(path))
    assert cfg.db_path == "/tmp/pass.db"
    assert cfg.service_start == "17:30"
    assert cfg.wash_backlog_threshold == 6
    assert not hasattr(cfg, "bogus_key")


def test_with_overrides_returns_new_object():
    base = Config.default()
    other = base.with_overrides(serve_port=9000)
    assert other.serve_port == 9000
    assert base.serve_port == 8788
    assert other is not base
