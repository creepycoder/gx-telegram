"""HTTP API: health, agents, tasks, notifications, telegram send (admin + agent auth)."""

from __future__ import annotations

import logging
import secrets as pysecrets

from aiohttp import web

from .agents import AgentManager
from .config import Config
from .database import Database
from .notifications import NotificationManager
from .security import redact, verify_agent_token
from .tasks import TaskManager
from .telegram import TelegramClient

log = logging.getLogger("tg.api")


def _check_admin(config: Config, request: web.Request) -> bool:
    """Admin endpoints: if no admin token configured, only allow loopback."""
    token = request.headers.get("X-Admin-Token", "")
    if config.admin_token:
        return pysecrets.compare_digest(token, config.admin_token)
    peer = request.transport.get_extra_info("peername") if request.transport else None
    ip = peer[0] if peer else ""
    return ip in ("127.0.0.1", "::1")


def _agent_auth_error(config: Config, request: web.Request) -> bool:
    """Extract agent id + token; return True when unauthorized."""
    agent_id = request.headers.get("X-Agent-Id", "").strip()
    if not agent_id:
        return True
    return not verify_agent_token(config, agent_id, request.headers.get("X-Agent-Token"))


class ApiServer:
    def __init__(self, config: Config, db: Database, agents: AgentManager,
                 tasks: TaskManager, notifier: NotificationManager,
                 telegram: TelegramClient):
        self.config = config
        self.db = db
        self.agents = agents
        self.tasks = tasks
        self.notifier = notifier
        self.tg = telegram
        self.app = web.Application()
        self._runner: web.AppRunner | None = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        r = self.app.router
        # public
        r.add_get("/health", self.health)
        # agent API (per-agent token)
        r.add_post("/agent/register", self.agent_register)
        r.add_post("/agent/heartbeat", self.agent_heartbeat)
        r.add_post("/agent/task/result", self.agent_task_result)
        r.add_post("/agent/notification", self.agent_notification)
        r.add_get("/agent/tasks", self.agent_tasks)
        # admin API
        r.add_get("/agents", self.get_agents)
        r.add_post("/telegram/notify", self.telegram_notify)
        r.add_post("/telegram/message", self.telegram_message)
        r.add_post("/task", self.create_task)
        r.add_get("/tasks", self.get_tasks)
        r.add_get("/task/{id}", self.get_task)
        r.add_post("/task/{id}/cancel", self.cancel_task)

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.config.host, self.config.port)
        await site.start()
        log.info("HTTP API listening on %s:%d", self.config.host, self.config.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    # -- public --------------------------------------------------------------

    async def health(self, request: web.Request) -> web.Response:
        telegram_ok = "unknown"
        if self.config.bot_token:
            try:
                await self.tg.start()
                await self.tg.get_me()
                telegram_ok = "ok"
            except Exception:  # noqa: BLE001
                telegram_ok = "error"
        else:
            telegram_ok = "not_configured"
        agents = {a.agent_id: a.status for a in self.agents.list_agents()}
        status = "ok" if telegram_ok in ("ok", "not_configured") else "degraded"
        return web.json_response({"status": status, "telegram": telegram_ok, "agents": agents})

    # -- agent API --------------------------------------------------------------

    def _agent_id_or_401(self, request: web.Request):
        if _agent_auth_error(self.config, request):
            return None
        return request.headers["X-Agent-Id"].strip()

    async def agent_register(self, request: web.Request) -> web.Response:
        agent_id = self._agent_id_or_401(request)
        if not agent_id:
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await request.json()
        agent = self.agents.register(
            agent_id,
            hostname=str(body.get("hostname", "")),
            os_name=str(body.get("os", "")),
            model=str(body.get("model", "")),
            capabilities=[str(c) for c in body.get("capabilities", [])][:20],
            version=str(body.get("version", "")),
        )
        return web.json_response({"ok": True, "agent": agent.to_dict()})

    async def agent_heartbeat(self, request: web.Request) -> web.Response:
        agent_id = self._agent_id_or_401(request)
        if not agent_id:
            return web.json_response({"error": "unauthorized"}, status=401)
        agent = self.agents.heartbeat(agent_id)
        if not agent:
            # auto-register on heartbeat to be forgiving
            agent = self.agents.register(agent_id)
        # deliver cancel signals
        cancelled = self.db.query(
            "SELECT task_id FROM tasks WHERE agent_id=? AND status='cancelled'", (agent_id,))
        return web.json_response({"ok": True, "cancelled_tasks": [r["task_id"] for r in cancelled]})

    async def agent_tasks(self, request: web.Request) -> web.Response:
        agent_id = self._agent_id_or_401(request)
        if not agent_id:
            return web.json_response({"error": "unauthorized"}, status=401)
        tasks = self.tasks.claim_remote_tasks(agent_id, limit=2)
        return web.json_response({
            "ok": True,
            "tasks": [t.to_dict() for t in tasks],
        })

    async def agent_task_result(self, request: web.Request) -> web.Response:
        agent_id = self._agent_id_or_401(request)
        if not agent_id:
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await request.json()
        task_id = str(body.get("task_id", ""))
        status = str(body.get("status", ""))
        if status not in ("completed", "failed"):
            return web.json_response({"error": "status must be completed|failed"}, status=400)
        task = self.tasks.report_result(
            task_id, status=status,
            result=redact(str(body.get("result") or ""), [self.config.bot_token]) or None,
            error=redact(str(body.get("error") or ""), [self.config.bot_token]) or None)
        if not task:
            return web.json_response({"error": "task not found"}, status=404)
        return web.json_response({"ok": True, "task": task.to_dict()})

    async def agent_notification(self, request: web.Request) -> web.Response:
        agent_id = self._agent_id_or_401(request)
        if not agent_id:
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await request.json()
        ntype = str(body.get("type", "info"))
        priority = str(body.get("priority", "normal"))
        n = await self.notifier.notify(
            agent_id, ntype,
            redact(str(body.get("message", "")), [self.config.bot_token]),
            priority=priority,
            chat_id=body.get("chat_id"),
            notification_id=str(body.get("notification_id") or "") or None,
        )
        if n is None:
            return web.json_response({"ok": True, "duplicate": True})
        return web.json_response({"ok": True, "notification_id": n.notification_id,
                                  "status": n.status})

    # -- admin API ---------------------------------------------------------------

    async def get_agents(self, request: web.Request) -> web.Response:
        if not _check_admin(self.config, request):
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response({"agents": [a.to_dict() for a in self.agents.list_agents()]})

    async def telegram_notify(self, request: web.Request) -> web.Response:
        if not _check_admin(self.config, request):
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await request.json()
        n = await self.notifier.notify(
            str(body.get("agent_id", self.config.default_agent)),
            str(body.get("type", "info")),
            str(body.get("message", "")),
            priority=str(body.get("priority", "normal")),
            chat_id=body.get("chat_id"),
            notification_id=str(body.get("notification_id") or "") or None,
        )
        if n is None:
            return web.json_response({"ok": True, "duplicate": True})
        return web.json_response({"ok": True, "notification_id": n.notification_id,
                                  "status": n.status})

    async def telegram_message(self, request: web.Request) -> web.Response:
        if not _check_admin(self.config, request):
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await request.json()
        chat_id = body.get("chat_id") or self.config.target_chat_id()
        if not chat_id:
            return web.json_response({"error": "no chat_id and no default configured"}, status=400)
        await self.tg.start()
        result = await self.tg.send_message(int(chat_id), str(body.get("text", "")))
        return web.json_response({"ok": True, "message_id": result.get("message_id")})

    async def create_task(self, request: web.Request) -> web.Response:
        if not _check_admin(self.config, request):
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await request.json()
        prompt = str(body.get("prompt", "")).strip()
        if not prompt:
            return web.json_response({"error": "prompt required"}, status=400)
        agent_id = self.agents.resolve(str(body.get("agent_id") or "") or None)
        if not agent_id:
            return web.json_response({"error": "no agents registered"}, status=400)
        chat_id = body.get("chat_id") or self.config.target_chat_id()
        task = self.tasks.create(agent_id, prompt, user_id=None,
                                 chat_id=int(chat_id) if chat_id else None)
        self.tasks.start(task)
        return web.json_response({"ok": True, "task": task.to_dict()}, status=201)

    async def get_tasks(self, request: web.Request) -> web.Response:
        if not _check_admin(self.config, request):
            return web.json_response({"error": "unauthorized"}, status=401)
        limit = int(request.query.get("limit", "20"))
        status = request.query.get("status")
        statuses = (status,) if status else None
        return web.json_response({"tasks": [t.to_dict() for t in self.tasks.list(limit, statuses)]})

    async def get_task(self, request: web.Request) -> web.Response:
        if not _check_admin(self.config, request):
            return web.json_response({"error": "unauthorized"}, status=401)
        task = self.tasks.get(request.match_info["id"])
        if not task:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response({"task": task.to_dict()})

    async def cancel_task(self, request: web.Request) -> web.Response:
        if not _check_admin(self.config, request):
            return web.json_response({"error": "unauthorized"}, status=401)
        ok = self.tasks.cancel(request.match_info["id"])
        return web.json_response({"ok": ok})
