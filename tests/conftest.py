"""Shared test fixtures."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from telegram_gateway.config import Config  # noqa: E402
from telegram_gateway.database import Database  # noqa: E402


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    return Config(
        bot_token="123456:TEST-token-secret",
        allowed_user_ids=[111],
        allowed_chat_ids=[222],
        default_chat_id=222,
        admin_token="admin-secret",
        local_agents=["gx10"],
        default_agent="gx10",
        agent_offline_after=90,
        db_path=str(tmp_path / "test.db"),
    )


@pytest.fixture(autouse=True)
def _clean_agent_token_env(monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN_SCAR16", "scar16-super-secret")
    monkeypatch.delenv("AGENT_TOKEN_GX10", raising=False)
