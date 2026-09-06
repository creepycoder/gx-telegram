"""HTTP API integration tests: health, auth, agent flow, task lifecycle."""

from __future__ import annotations

import pytest

from telegram_gateway.agents import AgentManager
from telegram_gateway.api import ApiServer
from telegram_gateway.notifications import NotificationManager
from telegram_gateway.sessions import SessionManager
from telegram_gateway.tasks import TaskManager


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def start(self):
        pass

    async def get_me(self):
        return {"username": "inferont"}

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append((chat_id, text))
        return {"message_id": 100 + len(self.sent)}

    async def edit_message(self, chat_id, message_id, text):
        return {}


@pytest.fixture()
def stack(config, db):
    agents = AgentManager(config, db)
    agents.register("gx10", local=True)
    sessions = SessionManager(config, db)
    tg = FakeTelegram()
    notifier = NotificationManager(config, db, tg)
    tasks = TaskManager(config, db, agents, sessions, notifier, tg)
    api = ApiServer(config, db, agents, tasks, notifier, tg)
    return config, db, agents, tasks, notifier, tg, api


@pytest.fixture()
async def client(aiohttp_client, stack):
    _, _, _, _, _, _, api = stack
    return await aiohttp_client(api.app)


def hdr(agent_id=None, token=None, admin=None):
    h = {}
    if agent_id:
        h["X-Agent-Id"] = agent_id
    if token:
        h["X-Agent-Token"] = token
    if admin:
        h["X-Admin-Token"] = admin
    return h


# Spec Test 1: GET /health returns status, telegram state, agents
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"
    assert data["telegram"] == "ok"
    assert data["agents"]["gx10"] == "online"


# --- agent auth (spec Test 8) -----------------------------------------------

async def test_agent_endpoints_require_token(client):
    resp = await client.post("/agent/register", json={})
    assert resp.status == 401
    resp = await client.get("/agent/tasks")
    assert resp.status == 401
    resp = await client.get("/agent/tasks",
                            headers=hdr("scar16", "wrong-token"))
    assert resp.status == 401


async def test_agent_register_heartbeat_flow(client, db):
    h = hdr("scar16", "scar16-super-secret")
    resp = await client.post("/agent/register", headers=h, json={
        "hostname": "scar16-pc", "os": "Linux", "model": "qwen", "version": "0.20"})
    assert resp.status == 200
    body = await resp.json()
    assert body["agent"]["hostname"] == "scar16-pc"

    resp = await client.post("/agent/heartbeat", headers=h, json={})
    assert resp.status == 200
    assert (await resp.json())["cancelled_tasks"] == []
    assert db.get_agent("scar16").status == "online"


# --- admin auth ---------------------------------------------------------------

async def test_admin_requires_token_when_configured(client):
    resp = await client.get("/agents")
    assert resp.status == 401  # non-loopback in test transport? loop is loopback...
    # With correct token it always works
    resp = await client.get("/agents", headers=hdr(admin="admin-secret"))
    assert resp.status == 200


async def test_admin_agents_list(client):
    resp = await client.get("/agents", headers=hdr(admin="admin-secret"))
    ids = {a["agent_id"] for a in (await resp.json())["agents"]}
    assert "gx10" in ids


# --- notifications (spec Test 7) ----------------------------------------------

async def test_api_notify_and_idempotency(client, db):
    h = hdr(admin="admin-secret")
    resp = await client.post("/telegram/notify", headers=h, json={
        "agent_id": "gx10", "type": "warning", "message": "GPU hot",
        "priority": "high", "notification_id": "dup-1"})
    assert resp.status == 200
    assert (await resp.json())["status"] == "sent"
    # duplicate suppressed
    resp = await client.post("/telegram/notify", headers=h, json={
        "agent_id": "gx10", "type": "warning", "message": "GPU hot",
        "priority": "high", "notification_id": "dup-1"})
    assert (await resp.json()).get("duplicate") is True


async def test_agent_notification_endpoint(client):
    h = hdr("scar16", "scar16-super-secret")
    await client.post("/agent/register", headers=h, json={})
    resp = await client.post("/agent/notification", headers=h, json={
        "type": "info", "message": "backup done", "priority": "normal",
        "notification_id": "agent-n-1"})
    body = await resp.json()
    assert body["ok"] and body["status"] == "sent"


# --- tasks (spec Test 8 lifecycle via remote agent) ---------------------------

async def test_task_api_remote_lifecycle(client, db):
    h = hdr("scar16", "scar16-super-secret")
    await client.post("/agent/register", headers=h, json={})

    # admin creates task for scar16 (must NOT execute locally)
    resp = await client.post("/task", headers=hdr(admin="admin-secret"), json={
        "agent_id": "scar16", "prompt": "remote echo test"})
    assert resp.status == 201
    task = (await resp.json())["task"]
    tid = task["task_id"]
    assert task["status"] in ("queued", "running")
    assert task["agent_id"] == "scar16"

    # agent claims it
    resp = await client.get("/agent/tasks", headers=h)
    claimed = (await resp.json())["tasks"]
    assert [t["task_id"] for t in claimed] == [tid]

    # agent reports result
    resp = await client.post("/agent/task/result", headers=h, json={
        "task_id": tid, "status": "completed", "result": "remote done"})
    assert resp.status == 200

    # admin sees completed
    resp = await client.get(f"/task/{tid}", headers=hdr(admin="admin-secret"))
    body = await resp.json()
    assert body["task"]["status"] == "completed"
    assert body["task"]["result"] == "remote done"


async def test_task_cancel_api(client, db):
    h = hdr("scar16", "scar16-super-secret")
    await client.post("/agent/register", headers=h, json={})
    resp = await client.post("/task", headers=hdr(admin="admin-secret"), json={
        "agent_id": "scar16", "prompt": "to cancel"})
    tid = (await resp.json())["task"]["task_id"]
    resp = await client.post(f"/task/{tid}/cancel", headers=hdr(admin="admin-secret"))
    assert resp.status == 200
    resp = await client.get(f"/task/{tid}", headers=hdr(admin="admin-secret"))
    assert (await resp.json())["task"]["status"] == "cancelled"


async def test_heartbeat_returns_cancelled_tasks(client):
    h = hdr("scar16", "scar16-super-secret")
    await client.post("/agent/register", headers=h, json={})
    resp = await client.post("/task", headers=hdr(admin="admin-secret"), json={
        "agent_id": "scar16", "prompt": "x"})
    tid = (await resp.json())["task"]["task_id"]
    await client.post(f"/task/{tid}/cancel", headers=hdr(admin="admin-secret"))
    resp = await client.post("/agent/heartbeat", headers=h, json={})
    assert tid in (await resp.json())["cancelled_tasks"]


async def test_result_redacts_bot_token(client, db):
    h = hdr("scar16", "scar16-super-secret")
    await client.post("/agent/register", headers=h, json={})
    resp = await client.get("/agent/tasks", headers=h)
    # create then claim
    resp = await client.post("/task", headers=hdr(admin="admin-secret"), json={
        "agent_id": "scar16", "prompt": "leak test"})
    tid = (await resp.json())["task"]["task_id"]
    await client.get("/agent/tasks", headers=h)
    resp = await client.post("/agent/task/result", headers=h, json={
        "task_id": tid, "status": "failed",
        "error": "boom with 123456:TEST-token-secret inside"})
    task = (await resp.json())["task"]
    assert "TEST-token-secret" not in task["error"]
    assert "***REDACTED***" in task["error"]
