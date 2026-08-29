"""Shared fixtures. Every test runs against a throwaway AI OS home."""

from __future__ import annotations

import pytest

from ai_os.app import AiOS
from ai_os.config import Config


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate AI_OS_HOME and the user's home directory for the whole test."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    data = tmp_path / "aios"
    data.mkdir()
    monkeypatch.setenv("AI_OS_HOME", str(data))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: fake_home))
    return fake_home


@pytest.fixture
def config(home):
    return Config(provider="local", allowed_folders=[str(home)])


@pytest.fixture
def app(tmp_path, config):
    instance = AiOS.create(
        config=config,
        config_path=tmp_path / "config.json",
        db_path=tmp_path / "audit.sqlite3",
    )
    yield instance
    instance.close()
