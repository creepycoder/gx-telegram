"""Gateway core: Telegram long-polling loop, command parsing, agent routing."""

from __future__ import annotations

import asyncio
import logging

from .agents import AgentManager
from .config import Config
from .database import Database
from .notifications import NotificationManager
from .security import RateLimiter
from .sessions import SessionManager
from .telegram import TelegramClient, TelegramError
from .tasks import TaskManager

log = logging.getLogger("tg.gateway")

HELP_TEXT = """🤖 inferont — Hermes remote console

Commands:
/help — this help
/status — gateway & agent status
/agents — list agents
/tasks — recent tasks
/cancel [task_id] — cancel a running task

Route a prompt to an agent:
/gx10 <prompt> — send to GX10
/scar16 <prompt> — send to SCAR16
@agent <prompt> — same as /agent
<plain text> — sent to the default agent"""


class Gateway:
    def __init__(self, config: Config, db: Database, telegram: TelegramClient,
                 agents: AgentManager, sessions: SessionManager,
                 notifier: NotificationManager, tasks: TaskManager):
        self.config = config
        self.db = db
        self.tg = telegram
        self.agents = agents
        self.sessions = sessions
        self.notifier = notifier
        self.tasks = tasks
        self.limiter = RateLimiter(config.rate_limit_per_minute)
        self._offset: int | None = int(db.get_setting("tg_offset") or 0) or None
        self._stop = asyncio.Event()

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        await self.tg.start()
        await self.tg.delete_webhook()
        try:
            me = await self.tg.get_me()
            log.info("Telegram API OK — bot @%s", me.get("username"))
        except TelegramError as exc:
            log.error("Telegram API check failed: %s", exc.description)
        # Register local agents (e.g. gx10 on this host)
        import os
        for agent_id in self.config.local_agents:
            self.agents.register(agent_id, model=os.environ.get("LOCAL_AGENT_MODEL", ""),
                                 capabilities=["shell", "filesystem", "llm"], local=True)
        stale = self.db.fail_stale_running_tasks()
        if stale:
            log.warning("Marked %d stale task(s) failed after restart", stale)
        if self.config.bot_token:
            chat = self.config.target_chat_id()
            if chat:
                await self.notifier.notify("gx10" if not self.config.local_agents
                                           else self.config.local_agents[0],
                                           "startup", "Telegram gateway started.",
                                           priority="normal", chat_id=chat)
        asyncio.create_task(self._heartbeat_watchdog())

    async def stop(self) -> None:
        self._stop.set()
        await self.tg.close()

    async def _heartbeat_watchdog(self) -> None:
        while not self._stop.is_set():
            try:
                self.agents.check_timeouts()
            except Exception:  # noqa: BLE001
                log.exception("Heartbeat watchdog error")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass

    async def run_forever(self) -> None:
        await self.start()
        while not self._stop.is_set():
            try:
                updates = await self.tg.get_updates(self._offset, timeout=50)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("getUpdates failed: %s", exc)
                await asyncio.sleep(5)
                continue
            for update in updates:
                self._offset = max(self._offset or 0, update["update_id"] + 1)
                self.db.set_setting("tg_offset", str(self._offset))
                msg = update.get("message")
                if msg:
                    asyncio.create_task(self._safe_handle(msg))
        await self.stop()

    # -- inbound handling ------------------------------------------------------

    async def _safe_handle(self, msg: dict) -> None:
        try:
            await self.handle_message(msg)
        except Exception:  # noqa: BLE001
            log.exception("Error handling update")

    async def handle_message(self, msg: dict) -> None:
        chat_id = msg["chat"]["id"]
        user = msg.get("from") or {}
        user_id = user.get("id")
        text = (msg.get("text") or "").strip()
        if not text:
            return

        self.db.upsert_telegram_user(user_id or 0, user.get("username", ""),
                                     user.get("first_name", ""), "")

        if not self.config.is_allowed(user_id, chat_id):
            log.warning("Unauthorized access attempt: user=%s chat=%s", user_id, chat_id)
            try:
                await self.tg.send_message(chat_id, "⛔ Not authorized.")
            except TelegramError:
                pass
            return

        if not self.limiter.allow(f"{user_id}"):
            await self.tg.send_message(chat_id, "🐌 Rate limit reached, slow down.")
            return

        handler, arg = self._parse(text)
        await handler(arg, user_id, chat_id, text)

    def _parse(self, text: str):
        first, _, rest = text.partition(" ")
        rest = rest.strip()
        cmd = first.lower().lstrip("@") if first.startswith(("/", "@")) else None
        if cmd in ("help", "start"):
            return self._cmd_help, None
        if cmd == "status":
            return self._cmd_status, None
        if cmd == "agents":
            return self._cmd_agents, None
        if cmd == "tasks":
            return self._cmd_tasks, None
        if cmd == "cancel":
            return self._cmd_cancel, rest
        if cmd and first.startswith(("/", "@")):
            agent = self.agents.resolve(cmd)
            if agent:
                return (lambda a, u, c, t: self._cmd_prompt(agent, a or t[len(first):].strip(), u, c)), rest
        # plain text → default agent
        return (lambda a, u, c, t: self._cmd_prompt(None, t, u, c)), text

    # -- commands ---------------------------------------------------------------

    async def _cmd_help(self, _arg, _user, chat, _raw):
        await self.tg.send_message(chat, HELP_TEXT)

    async def _cmd_status(self, _arg, _user, chat, _raw):
        lines = ["📡 Gateway status\n"]
        for agent in self.agents.list_agents():
            icon = "🟢" if agent.status == "online" else "🔴"
            lines.append(f"{agent.agent_id.upper()}: {icon} {agent.status}")
        default = self.config.default_agent
        lines.append(f"\nDefault agent: {default}")
        active = len(self.tasks.active())
        lines.append(f"Active tasks: {active}")
        await self.tg.send_message(chat, "\n".join(lines))

    async def _cmd_agents(self, _arg, _user, chat, _raw):
        agents = self.agents.list_agents()
        if not agents:
            await self.tg.send_message(chat, "No agents registered.")
            return
        lines = ["Available agents:"]
        for a in agents:
            icon = "🟢" if a.status == "online" else "🔴"
            lines.append(f"\n{icon} {a.agent_id}\n{a.model or 'unknown model'}")
        await self.tg.send_message(chat, "\n".join(lines))

    async def _cmd_tasks(self, _arg, _user, chat, _raw):
        tasks = self.tasks.list(limit=10)
        if not tasks:
            await self.tg.send_message(chat, "No tasks yet.")
            return
        icons = {"queued": "🕐", "running": "⏳", "completed": "✅",
                 "failed": "❌", "cancelled": "🚫"}
        lines = ["Recent tasks:"]
        for t in tasks:
            lines.append(f"{icons.get(t.status, '?')} {t.task_id} [{t.agent_id}] "
                         f"{t.status}\n   {t.prompt[:80]}")
        await self.tg.send_message(chat, "\n".join(lines)[:4000])

    async def _cmd_cancel(self, arg, user, chat, _raw):
        task_id = arg.strip()
        if not task_id:
            active = self.tasks.active()
            mine = [t for t in active if t.telegram_user_id == user]
            if len(mine) == 1:
                task_id = mine[0].task_id
            elif not mine:
                await self.tg.send_message(chat, "No active task to cancel.")
                return
            else:
                ids = "\n".join(t.task_id for t in mine)
                await self.tg.send_message(chat, f"Multiple active tasks — specify one:\n{ids}")
                return
        if self.tasks.cancel(task_id):
            await self.notifier.notify(
                self.agents.resolve(None) or "?", "info",
                f"Task {task_id} cancelled by user.", priority="normal", chat_id=chat)
            await self.tg.send_message(chat, f"🚫 Cancel requested: {task_id}")
        else:
            await self.tg.send_message(chat, f"Cannot cancel {task_id} (not active or not found).")

    async def _cmd_prompt(self, agent_id: str | None, prompt: str, user, chat):
        prompt = (prompt or "").strip()
        if not prompt:
            await self.tg.send_message(chat, "Usage: /<agent> <prompt> — see /help")
            return
        resolved = self.agents.resolve(agent_id)
        if not resolved:
            await self.tg.send_message(chat, "No agents registered. Use /agents.")
            return
        agent = self.agents.get(resolved)
        if agent and agent.status != "online":
            await self.tg.send_message(
                chat, f"⚠️ Agent {resolved} is {agent.status}; task will be queued.")
        conv = self.sessions.touch(user, chat, resolved)
        self.sessions.add_user_message(conv.key, prompt)
        task = self.tasks.create(resolved, prompt, user_id=user, chat_id=chat,
                                 conversation_key=conv.key)
        self.tasks.start(task)
        await self.tg.send_message(
            chat,
            f"🚀 Task started\n\nAgent: {resolved.upper()}\nTask ID: {task.task_id}")
