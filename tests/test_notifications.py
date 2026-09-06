"""Notification tests: idempotency, priority filtering, formatting."""

from __future__ import annotations

import pytest

from telegram_gateway.notifications import NotificationManager, format_notification


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append((chat_id, text))
        return {"message_id": 100 + len(self.sent)}

    async def edit_message(self, chat_id, message_id, text):
        return {}


@pytest.fixture()
def tg():
    return FakeTelegram()


@pytest.fixture()
def notifier(config, db, tg):
    return NotificationManager(config, db, tg)


async def test_notify_sends_formatted(config, db, notifier, tg):
    n = await notifier.notify("gx10", "warning", "GPU memory high", priority="high")
    assert n.status == "sent"
    chat, text = tg.sent[0]
    assert chat == 222
    assert "[GX10]" in text
    assert "⚠️" in text
    assert "WARNING" in text


async def test_idempotent_duplicate_ignored(config, db, notifier, tg):
    n1 = await notifier.notify("gx10", "info", "hello", notification_id="abc-123")
    n2 = await notifier.notify("gx10", "info", "hello", notification_id="abc-123")
    assert n1 is not None
    assert n2 is None  # duplicate
    assert len(tg.sent) == 1


async def test_low_priority_ignored_by_default(config, db, notifier, tg):
    n = await notifier.notify("gx10", "info", "noise", priority="low",
                              notification_id="low-1")
    assert n.status == "ignored"
    assert tg.sent == []
    assert db.list_notifications()[0].status == "ignored"


async def test_low_priority_sent_when_configured(config, db, notifier, tg):
    config.low_priority_mode = "telegram"
    n = await notifier.notify("gx10", "task_started", "starting", priority="low",
                              notification_id="low-2")
    assert n.status == "sent"
    assert len(tg.sent) == 1


async def test_critical_priority_always_sent(config, db, notifier, tg):
    n = await notifier.notify("scar16", "error", "disk full", priority="critical")
    assert n.status == "sent"
    assert "[SCAR16]" in tg.sent[0][1]


async def test_no_target_chat_marks_failed(config, db, notifier, tg):
    config.default_chat_id = None
    config.allowed_chat_ids = []
    config.allowed_user_ids = []
    n = await notifier.notify("gx10", "info", "orphan", priority="normal",
                              notification_id="orph-1")
    assert n.status == "failed"


async def test_startup_format(config, db, notifier, tg):
    await notifier.notify("gx10", "startup", "Hermes Telegram agent started.")
    text = tg.sent[0][1]
    assert "🟢" in text
    assert "ONLINE" in text
    assert "[GX10]" in text


def test_format_all_types():
    from telegram_gateway.models import NOTIFICATION_TYPES, Notification
    for t in NOTIFICATION_TYPES:
        n = Notification(notification_id="x", agent_id="gx10", type=t,
                         priority="normal", message="m")
        text = format_notification(n)
        assert "[GX10]" in text
