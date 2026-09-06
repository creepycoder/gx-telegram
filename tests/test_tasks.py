"""Task lifecycle tests: creation, statuses, remote claim/report, cancellation."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from telegram_gateway.agents import AgentManager
from telegram_gateway.notifications import NotificationManager
from telegram_gateway.sessions import SessionManager
from telegram_gateway.tasks import TaskManager, make_task_id


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.edited = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append((chat_id, text))
        return {"message_id": 100 + len(self.sent)}

    async def edit_message(self, chat_id, message_id, text):
        self.edited.append((chat_id, message_id, text))
        return {}


@pytest.fixture()
def managers(config, db):
    agents = AgentManager(config, db)
    agents.register("gx10", local=True)
    agents.register("scar16")
    sessions = SessionManager(config, db)
    tg = FakeTelegram()
    notifier = NotificationManager(config, db, tg)
    tasks = TaskManager(config, db, agents, sessions, notifier, tg)
    return agents, sessions, notifier, tg, tasks


def test_task_id_format(config):
    tid = make_task_id("gx10")
    assert tid.startswith("gx10-")
    # gx10-YYYYMMDD-HHMMSS-mmm
    parts = tid.split("-")
    assert len(parts) == 4
    assert len(parts[1]) == 8 and len(parts[2]) == 6


def test_create_and_status_transitions(config, db, managers):
    *_, tasks = managers
    t = tasks.create("gx10", "test prompt", user_id=111, chat_id=222)
    assert t.status == "queued"
    db.update_task(t.task_id, status="running")
    assert db.get_task(t.task_id).status == "running"
    db.update_task(t.task_id, status="completed", result="ok")
    got = db.get_task(t.task_id)
    assert got.status == "completed"
    assert got.result == "ok"


def test_local_task_with_fake_hermes(config, db, managers, tmp_path, monkeypatch):
    """Local execution against a fake hermes binary → completed + notifications."""
    *_, tg, tasks = managers
    fake = tmp_path / "hermes"
    fake.write_text("#!/bin/sh\necho HERMES_OK\n")
    fake.chmod(0o755)
    config.hermes_bin = str(fake)

    t = tasks.create("gx10", "say ok", user_id=111, chat_id=222)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(tasks._run_local(t))
    finally:
        loop.close()
    got = db.get_task(t.task_id)
    assert got.status == "completed"
    assert "HERMES_OK" in got.result
    # task_completed notification delivered to chat
    assert any("TASK COMPLETED" in text for _, text in tg.sent)


def test_local_task_failure(config, db, managers, tmp_path):
    *_, tg, tasks = managers
    fake = tmp_path / "hermes"
    fake.write_text("#!/bin/sh\necho 'kaboom' >&2\nexit 1\n")
    fake.chmod(0o755)
    config.hermes_bin = str(fake)

    t = tasks.create("gx10", "fail please", user_id=111, chat_id=222)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(tasks._run_local(t))
    finally:
        loop.close()
    got = db.get_task(t.task_id)
    assert got.status == "failed"
    assert "kaboom" in got.error
    # spec Test 5: failed task produces ❌ TASK FAILED
    assert any("TASK FAILED" in text and "❌" in text for _, text in tg.sent)


def test_remote_claim_and_report(config, db, managers):
    agents, sessions, notifier, tg, tasks = managers
    t = tasks.create("scar16", "remote work", user_id=111, chat_id=222)
    claimed = tasks.claim_remote_tasks("scar16")
    assert [x.task_id for x in claimed] == [t.task_id]
    assert db.get_task(t.task_id).status == "running"

    loop = asyncio.new_event_loop()
    try:
        updated = loop.run_until_complete(_report(tasks, t.task_id))
    finally:
        loop.close()
    assert updated.status == "completed"
    assert updated.result == "done"


async def _report(tasks, task_id):
    tasks.report_result(task_id, status="completed", result="done")
    await asyncio.sleep(0)  # let notify task run
    return tasks.get(task_id)


def test_cancel_queued_local_task(config, db, managers):
    *_, tasks = managers
    t = tasks.create("gx10", "cancel me", user_id=111, chat_id=222)
    assert tasks.cancel(t.task_id) is True
    assert db.get_task(t.task_id).status == "cancelled"


def test_cancel_finished_task_fails(config, db, managers):
    *_, tasks = managers
    t = tasks.create("gx10", "x", user_id=111, chat_id=222)
    db.update_task(t.task_id, status="completed")
    assert tasks.cancel(t.task_id) is False


def test_stale_tasks_failed_on_startup(config, db, managers):
    *_, tasks = managers
    t = tasks.create("gx10", "interrupted", user_id=111, chat_id=222)
    db.update_task(t.task_id, status="running")
    count = db.fail_stale_running_tasks()
    assert count == 1
    assert db.get_task(t.task_id).status == "failed"


def test_conversation_context_isolated_per_agent(config, db, managers):
    agents, sessions, notifier, tg, tasks = managers
    c1 = sessions.touch(111, 222, "gx10")
    c2 = sessions.touch(111, 222, "scar16")
    sessions.add_user_message(c1.key, "gx only")
    assert "gx only" in sessions.context_block(c1.key)
    assert "gx only" not in sessions.context_block(c2.key)
