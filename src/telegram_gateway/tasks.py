"""Task lifecycle: creation, local Hermes execution, remote agent dispatch, cancellation."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .agents import AgentManager
from .config import Config
from .database import Database
from .models import Task, utcnow
from .notifications import NotificationManager
from .security import redact, secrets_list
from .sessions import SessionManager
from .telegram import TelegramClient

log = logging.getLogger("tg.tasks")


def make_task_id(agent_id: str) -> str:
    now = datetime.now(timezone.utc)
    return f"{agent_id}-{now:%Y%m%d-%H%M%S}-{now.microsecond // 1000:03d}"


class TaskManager:
    def __init__(self, config: Config, db: Database, agents: AgentManager,
                 sessions: SessionManager, notifier: NotificationManager,
                 telegram: TelegramClient):
        self.config = config
        self.db = db
        self.agents = agents
        self.sessions = sessions
        self.notifier = notifier
        self.tg = telegram
        self._sem = asyncio.Semaphore(config.max_concurrent_tasks)
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()
        self._secrets = secrets_list(config)

    # -- creation -----------------------------------------------------------

    def create(self, agent_id: str, prompt: str, user_id: int | None = None,
               chat_id: int | None = None, conversation_key: str = "") -> Task:
        task = Task(
            task_id=make_task_id(agent_id),
            agent_id=agent_id,
            prompt=prompt,
            telegram_user_id=user_id,
            telegram_chat_id=chat_id,
            conversation_key=conversation_key,
        )
        self.db.insert_task(task)
        log.info("Task %s created for agent %s", task.task_id, agent_id)
        return task

    def get(self, task_id: str) -> Task | None:
        return self.db.get_task(task_id)

    def list(self, limit: int = 20, statuses: tuple[str, ...] | None = None) -> list[Task]:
        return self.db.list_tasks(limit=limit, statuses=statuses)

    def active(self) -> list[Task]:
        return self.db.list_tasks(limit=50, statuses=("queued", "running"))

    # -- dispatch ------------------------------------------------------------

    def is_local(self, agent_id: str) -> bool:
        agent = self.agents.get(agent_id)
        if agent is not None:
            return bool(agent.local)
        return agent_id in self.config.local_agents

    def start(self, task: Task) -> None:
        """Start execution: locally via Hermes, or leave queued for a remote agent."""
        atask = asyncio.create_task(self._run_guarded(task))
        self._running[task.task_id] = atask

    async def _run_guarded(self, task: Task) -> None:
        try:
            if self.is_local(task.agent_id):
                await self._run_local(task)
            # Remote agents pick tasks up from GET /agent/tasks; nothing to do here.
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Task %s crashed", task.task_id)
            self._finish(task, status="failed", error="Internal gateway error")
        finally:
            self._running.pop(task.task_id, None)

    async def _run_local(self, task: Task) -> None:
        async with self._sem:
            current = self.db.get_task(task.task_id)
            if current is None or current.status == "cancelled":
                return
            self.db.update_task(task.task_id, status="running", started_at=utcnow())
            await self._notify_started(task)
            progress_msg = await self._send_progress(task, "Task started, waiting for Hermes…")

            context = ""
            if task.conversation_key:
                context = self.sessions.context_block(task.conversation_key)
            prompt = context + task.prompt if context else task.prompt

            try:
                result_text = await self._exec_hermes(task, prompt)
            except asyncio.CancelledError:
                self._cancelled.discard(task.task_id)
                self._finish(task, status="cancelled")
                await self._update_progress(progress_msg, task, "🚫 Task cancelled.")
                return
            except asyncio.TimeoutError:
                self._finish(task, status="failed",
                             error=f"Task timed out after {self.config.task_timeout}s")
                await self._update_progress(progress_msg, task, "⌛ Task timed out.")
                await self._notify_failed(task, f"Task timed out after {self.config.task_timeout}s")
                return
            except Exception as exc:  # noqa: BLE001
                if task.task_id in self._cancelled:
                    self._cancelled.discard(task.task_id)
                    self._finish(task, status="cancelled")
                    await self._update_progress(progress_msg, task, "🚫 Task cancelled.")
                    return
                msg = redact(str(exc), self._secrets) or "Hermes execution failed"
                self._finish(task, status="failed", error=msg[:2000])
                await self._update_progress(progress_msg, task, "❌ Task failed.")
                await self._notify_failed(task, msg[:1000])
                return

            if result_text is None:  # cancelled while the process was killed
                self._cancelled.discard(task.task_id)
                self._finish(task, status="cancelled")
                await self._update_progress(progress_msg, task, "🚫 Task cancelled.")
                return

            self._cancelled.discard(task.task_id)
            if task.conversation_key:
                self.sessions.add_assistant_message(task.conversation_key, result_text)
            self._finish(task, status="completed", result=result_text[:8000])
            await self._update_progress(progress_msg, task, None)
            await self.notifier.notify(
                task.agent_id, "task_completed",
                f"Task:\n{task.prompt[:200]}\n\nResult:\n{result_text[:3500]}",
                priority="normal", chat_id=task.telegram_chat_id)

    async def _exec_hermes(self, task: Task, prompt: str) -> str | None:
        cmd = [self.config.hermes_bin, "-z", prompt, "--yolo"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise RuntimeError(f"Hermes binary not found: {self.config.hermes_bin}")
        self._processes[task.task_id] = proc
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.config.task_timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.kill()
            raise
        finally:
            self._processes.pop(task.task_id, None)

        if task.task_id in self._cancelled:
            return None  # killed on purpose by cancel()

        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", "replace").strip()
            raise RuntimeError(err or f"Hermes exited with code {proc.returncode}")
        return stdout.decode("utf-8", "replace").strip() or "(empty response)"

    # -- cancellation ---------------------------------------------------------

    def cancel(self, task_id: str) -> bool:
        """Cancel a task. Local: kill process. Remote: flag it; agent is told on poll."""
        task = self.db.get_task(task_id)
        if not task or task.status not in ("queued", "running"):
            return False
        self._cancelled.add(task_id)
        proc = self._processes.get(task_id)
        if proc and proc.returncode is None:
            proc.kill()  # _exec_hermes notices _cancelled and finalises as cancelled
            return True
        atask = self._running.get(task_id)
        if atask and not atask.done() and not self.is_local(task.agent_id):
            atask.cancel()
            self.db.update_task(task_id, status="cancelled", completed_at=utcnow())
        elif not proc:
            # queued (local waiting on semaphore, or remote not yet claimed)
            self.db.update_task(task_id, status="cancelled", completed_at=utcnow())
        return True

    # -- remote agent reporting -------------------------------------------------

    def claim_remote_tasks(self, agent_id: str, limit: int = 1) -> list[Task]:
        """Hand queued tasks to a polling remote agent."""
        rows = self.db.query(
            "SELECT task_id FROM tasks WHERE agent_id=? AND status='queued' "
            "ORDER BY created_at ASC LIMIT ?", (agent_id, limit))
        tasks = []
        for r in rows:
            self.db.update_task(r["task_id"], status="running", started_at=utcnow())
            t = self.db.get_task(r["task_id"])
            if t:
                tasks.append(t)
        return tasks

    def report_result(self, task_id: str, *, status: str, result: str | None = None,
                      error: str | None = None) -> Task | None:
        task = self.db.get_task(task_id)
        if not task:
            return None
        if task.status == "cancelled":
            return task
        if status in ("completed", "failed"):
            self._finish(task, status=status, result=result, error=error)
            task = self.db.get_task(task_id)
            if status == "completed":
                if task and task.conversation_key and result:
                    self.sessions.add_assistant_message(task.conversation_key, result)
                asyncio.create_task(self.notifier.notify(
                    task.agent_id, "task_completed",
                    f"Task:\n{(task.prompt if task else '')[:200]}\n\nResult:\n{(result or '')[:3500]}",
                    priority="normal", chat_id=task.telegram_chat_id if task else None))
            else:
                asyncio.create_task(self.notifier.notify(
                    task.agent_id, "task_failed",
                    f"Task:\n{(task.prompt if task else '')[:200]}\n\nError:\n{(error or 'unknown')[:1000]}",
                    priority="high", chat_id=task.telegram_chat_id if task else None))
        return task

    # -- helpers ------------------------------------------------------------------

    def _finish(self, task: Task, status: str, result: str | None = None,
                error: str | None = None) -> None:
        fields = {"status": status, "completed_at": utcnow()}
        if result is not None:
            fields["result"] = result
        if error is not None:
            fields["error"] = redact(error, self._secrets)
        self.db.update_task(task.task_id, **fields)
        log.info("Task %s finished: %s", task.task_id, status)

    async def _notify_started(self, task: Task) -> None:
        await self.notifier.notify(
            task.agent_id, "task_started",
            f"Task:\n{task.prompt[:200]}\n\nTask ID:\n{task.task_id}",
            priority="low", chat_id=task.telegram_chat_id)

    async def _notify_failed(self, task: Task, error: str) -> None:
        await self.notifier.notify(
            task.agent_id, "task_failed",
            f"Task:\n{task.prompt[:200]}\n\nError:\n{error}",
            priority="high", chat_id=task.telegram_chat_id)

    async def _send_progress(self, task: Task, text: str):
        chat_id = task.telegram_chat_id or self.config.target_chat_id()
        if chat_id is None:
            return None
        try:
            res = await self.tg.send_message(
                chat_id, f"⏳ [{task.agent_id.upper()}] {text}\n\nTask ID: {task.task_id}")
            msg_id = res.get("message_id")
            self.db.update_task(task.task_id, progress_message_id=msg_id)
            task.progress_message_id = msg_id
            return {"chat_id": chat_id, "message_id": msg_id}
        except Exception:  # noqa: BLE001
            log.warning("Could not send progress message for %s", task.task_id)
            return None

    async def _update_progress(self, ref, task: Task, final_text: str | None) -> None:
        if not ref:
            return
        text = final_text or "✅ Task finished."
        try:
            await self.tg.edit_message(ref["chat_id"], ref["message_id"],
                                       f"⏳ [{task.agent_id.upper()}] {text}\n\nTask ID: {task.task_id}")
        except Exception:  # noqa: BLE001
            pass
