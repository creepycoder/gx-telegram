#!/usr/bin/env python3
"""Live smoke test: run ApiServer standalone (no Telegram), exercise REST over real HTTP."""
import asyncio
import json
import os
import sys
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

BASE = "http://127.0.0.1:31998"
TOKEN = "live-smoke-token"


def req(method, path, body=None, headers=None, expect=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    r = urllib.request.Request(BASE + path, method=method,
                               data=json.dumps(body).encode() if body is not None else None,
                               headers=h)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            status, data = resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        status, data = e.code, json.loads(e.read() or b"{}")
    ok = (expect is None or status == expect)
    print(f"{'PASS' if ok else 'FAIL'} {method} {path} -> {status} {json.dumps(data)[:120]}")
    assert ok, f"expected {expect}, got {status}"
    return status, data


async def areq(*args, **kwargs):
    """Run blocking HTTP request in a thread so the in-loop aiohttp server can answer."""
    return await asyncio.to_thread(req, *args, **kwargs)


async def main():
    from telegram_gateway.agents import AgentManager
    from telegram_gateway.api import ApiServer
    from telegram_gateway.config import Config
    from telegram_gateway.database import Database
    from telegram_gateway.notifications import NotificationManager
    from telegram_gateway.sessions import SessionManager
    from telegram_gateway.tasks import TaskManager

    os.environ["AGENT_TOKEN_SCAR16"] = TOKEN
    cfg = Config(bot_token="", default_chat_id=111, allowed_user_ids=[111],
                 host="127.0.0.1", port=31998, admin_token="adm",
                 db_path="/tmp/tg-smoke/live.db")
    os.makedirs("/tmp/tg-smoke", exist_ok=True)
    db = Database(cfg.db_path)
    agents = AgentManager(cfg, db)
    agents.register("gx10", local=True)
    sessions = SessionManager(cfg, db)

    class NullTG:
        async def start(self): pass
        async def stop(self): pass
        async def get_me(self): return {"username": "inferont"}
        async def send_message(self, c, t, parse_mode=None): return {"message_id": 1}
        async def edit_message(self, c, m, t): return {}

    tg = NullTG()
    notifier = NotificationManager(cfg, db, tg)
    tasks = TaskManager(cfg, db, agents, sessions, notifier, tg)
    api = ApiServer(cfg, db, agents, tasks, notifier, tg)
    await api.start()
    print("Server up on", BASE)

    ah = {"X-Agent-Id": "scar16", "X-Agent-Token": TOKEN}
    adm = {"X-Admin-Token": "adm"}

    await areq("GET", "/health")                                     # spec test 1
    await areq("POST", "/agent/register", {}, {"X-Agent-Id": "scar16", "X-Agent-Token": "bad"}, 401)
    await areq("POST", "/agent/register", {"hostname": "scar16-live", "os": "Linux"}, ah, 200)
    await areq("POST", "/agent/heartbeat", {}, ah, 200)
    await areq("GET", "/agents", None, adm, 200)                     # spec test 6
    await areq("GET", "/agents", None, None, 401)
    s, r = await areq("POST", "/telegram/notify",
                      {"agent_id": "gx10", "type": "warning", "message": "GPU hot",
                       "priority": "high", "notification_id": "smoke-1"}, adm, 200)  # spec test 7
    s, r = await areq("POST", "/telegram/notify",
                      {"agent_id": "gx10", "type": "warning", "message": "GPU hot",
                       "priority": "high", "notification_id": "smoke-1"}, adm, 200)  # duplicate
    assert r.get("duplicate"), "idempotency failed"
    s, r = await areq("POST", "/task", {"agent_id": "scar16", "prompt": "live remote task"}, adm, 201)
    tid = r["task"]["task_id"]
    s, r = await areq("GET", "/agent/tasks", None, ah, 200)          # spec test 8 claim
    assert any(t["task_id"] == tid for t in r["tasks"]), "claim failed"
    await areq("POST", "/agent/task/result", {"task_id": tid, "status": "completed",
                                              "result": "live done"}, ah, 200)
    s, r = await areq("GET", f"/task/{tid}", None, adm, 200)
    assert r["task"]["status"] == "completed" and r["task"]["result"] == "live done"
    # cancel flow
    s, r = await areq("POST", "/task", {"agent_id": "scar16", "prompt": "cancel me"}, adm, 201)
    tid2 = r["task"]["task_id"]
    await areq("POST", f"/task/{tid2}/cancel", None, adm, 200)
    s, r = await areq("GET", f"/task/{tid2}", None, adm, 200)
    assert r["task"]["status"] == "cancelled"
    s, r = await areq("POST", "/agent/heartbeat", {}, ah, 200)       # cancel via heartbeat
    assert tid2 in r["cancelled_tasks"]
    await areq("GET", "/tasks?limit=5", None, adm, 200)

    await api.stop()
    db.close()
    print("\nALL LIVE SMOKE CHECKS PASSED")


asyncio.run(main())
