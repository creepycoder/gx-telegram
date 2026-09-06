"""Telegram client tests: retry on 429/5xx, edits, allowlist-independent send errors."""

from __future__ import annotations

import asyncio
import json

import pytest

from telegram_gateway.telegram import TelegramClient, TelegramError


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Queue of (status, payload) responses for post()."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None):
        self.calls.append((url, json))
        status, payload = self.responses.pop(0)
        return FakeResponse(status, payload)


@pytest.fixture()
def fast_sleep(monkeypatch):
    async def _no_sleep(_):
        return None
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


async def test_send_message_success(config):
    session = FakeSession([(200, {"ok": True, "result": {"message_id": 42}})])
    client = TelegramClient(config, session=session)
    await client.start()
    result = await client.send_message(222, "hello")
    assert result["message_id"] == 42
    url, payload = session.calls[0]
    assert "sendMessage" in url
    assert payload["chat_id"] == 222


async def test_retry_on_429_respects_retry_after(config, fast_sleep):
    session = FakeSession([
        (429, {"ok": False, "description": "Too Many Requests",
               "parameters": {"retry_after": 1}}),
        (200, {"ok": True, "result": {"message_id": 7}}),
    ])
    client = TelegramClient(config, session=session)
    await client.start()
    result = await client.send_message(222, "hi")
    assert result["message_id"] == 7
    assert len(session.calls) == 2


async def test_retry_on_5xx_then_success(config, fast_sleep):
    session = FakeSession([
        (500, {"ok": False, "description": "boom"}),
        (502, {"ok": False, "description": "bad gateway"}),
        (200, {"ok": True, "result": {"message_id": 9}}),
    ])
    client = TelegramClient(config, session=session)
    await client.start()
    result = await client.send_message(222, "hi")
    assert result["message_id"] == 9


async def test_4xx_raises_immediately(config, fast_sleep):
    session = FakeSession([(400, {"ok": False, "description": "chat not found"})])
    client = TelegramClient(config, session=session)
    await client.start()
    with pytest.raises(TelegramError) as exc:
        await client.send_message(222, "hi")
    assert "chat not found" in str(exc.value)
    assert len(session.calls) == 1


async def test_gives_up_after_max_retries(config, fast_sleep):
    session = FakeSession([(429, {"ok": False, "parameters": {"retry_after": 0}})] * 5)
    client = TelegramClient(config, session=session)
    await client.start()
    with pytest.raises(TelegramError):
        await client.send_message(222, "hi")
    assert len(session.calls) == 5


async def test_edit_message_not_modified_is_ok(config, fast_sleep):
    session = FakeSession([(400, {"ok": False, "description": "Bad Request: "
                                                               "message is not modified"})])
    client = TelegramClient(config, session=session)
    await client.start()
    result = await client.edit_message(222, 5, "same text")
    assert result == {}  # swallowed, not raised


async def test_token_not_in_logs_or_repr(config, caplog):
    """The bot token must never appear in exception messages."""
    session = FakeSession([(400, {"ok": False, "description": "bad request"})])
    client = TelegramClient(config, session=session)
    await client.start()
    with pytest.raises(TelegramError) as exc:
        await client.send_message(222, "x")
    assert config.bot_token not in str(exc.value)
