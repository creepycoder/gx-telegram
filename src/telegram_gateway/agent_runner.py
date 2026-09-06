"""Remote agent runner (e.g. SCAR16): register, heartbeat, claim tasks, run Hermes, report.

Runs on the remote machine and talks to the GX10 gateway over the LAN using a
per-agent shared token. Hermes is invoked locally with `hermes -z`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import socket
from typing import Any

import aiohttp

log = logging.getLogger("tg.agent")


class RemoteAgent:
    def __init__(self, gateway_url: str, agent_id: str, token: str,
                 hermes_bin: str = "hermes", task_timeout: int = 1800):
        self.gateway_url = gateway_url.rstrip("/")
        self.agent_id = agent_id
        self.token = token
        self.hermes_bin = hermes_bin
        self.task_timeout = task_timeout
        self._running: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()

    def _headers(self) -> dict[str, str]:
        return {"X-Agent-Id": self.agent_id, "X-Agent-Token": self.token}

    async def _post(self, session: aiohttp.ClientSession, path: str,
                    body: dict) -> dict[str, Any]:
        async with session.post(f"{self.gateway_url}{path}", json=body,
                                headers=self._headers) as resp:
            return await resp.json(content_type=None)

    async def _get(self, session: aiohttp.ClientSession, path: str) -> dict[str, Any]:
        async with session.get(f"{self.gateway_url}{path}",
                               headers=self._headers) as resp:
            return await resp.json(content_type=None)

    async def register(self, session: aiohttp.ClientSession, model: str) -> None:
        body = {
            "hostname": socket.gethostname(),
            "os": f"{platform.system()} {platform.release()}",
            "model": model,
            "capabilities": ["shell", "filesystem", "llm"],
            "version": "0.1.0",
        }
        res = await self._post(session, "/agent/register", body)
        log.info("Register: %s", res.get("ok", res))

    async def heartbeat(self, session: aiohttp.ClientSession) -> list[str]:
        """Send heartbeat; returns task ids the gateway says to cancel."""
        try:
            res = await self._post(session, "/agent/heartbeat", {})
            return list(res.get("cancelled_tasks", []))
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("Heartbeat failed: %s", exc)
            return []

    async def poll_tasks(self, session: aiohttp.ClientSession) -> list[dict]:
        try:
            res = await self._get(session, "/agent/tasks")
            return list(res.get("tasks", []))
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("Task poll failed: %s", exc)
            return []

    async def execute(self, session: aiohttp.ClientSession, task: dict) -> None:
        task_id = task["task_id"]
        prompt = task["prompt"]
        await self._post(session, "/agent/notification", {
            "type": "task_started", "priority": "low",
            "message": f"Task:\n{prompt[:200]}\n\nTask ID:\n{task_id}"})
        try:
            proc = await asyncio.create_subprocess_exec(
                self.hermes_bin, "-z", prompt, "--yolo",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL)
            self._running[task_id] = proc
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self.task_timeout)
            except asyncio.TimeoutError:
                proc.kill()
                raise
            except asyncio.CancelledError:
                proc.kill()
                raise
            finally:
                self._running.pop(task_id, None)

            if task_id in self._cancelled:
                self._cancelled.discard(task_id)
                return  # gateway already knows it is cancelled

            if proc.returncode != 0:
                err = (stderr or b"").decode("utf-8", "replace").strip()
                await self._post(session, "/agent/task/result", {
                    "task_id": task_id, "status": "failed",
                    "error": err or f"Hermes exited with code {proc.returncode}"})
            else:
                result = stdout.decode("utf-8", "replace").strip() or "(empty response)"
                await self._post(session, "/agent/task/result", {
                    "task_id": task_id, "status": "completed", "result": result})
        except asyncio.TimeoutError:
            await self._post(session, "/agent/task/result", {
                "task_id": task_id, "status": "failed",
                "error": f"Task timed out after {self.task_timeout}s"})
        except FileNotFoundError:
            await self._post(session, "/agent/task/result", {
                "task_id": task_id, "status": "failed",
                "error": f"Hermes binary not found: {self.hermes_bin}"})
        except Exception as exc:  # noqa: BLE001
            log.exception("Task %s failed", task_id)
            await self._post(session, "/agent/task/result", {
                "task_id": task_id, "status": "failed", "error": str(exc)[:500]})

    async def run(self, interval: int = 30) -> None:
        model = os.environ.get("LOCAL_AGENT_MODEL", "")
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # register with retries
            while True:
                try:
                    await self.register(session, model)
                    break
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    log.warning("Gateway unreachable (%s), retrying in %ds", exc, interval)
                    await asyncio.sleep(interval)
            # startup notification
            try:
                await self._post(session, "/agent/notification", {
                    "type": "startup", "priority": "normal",
                    "message": f"Hermes Telegram agent started.\n\nModel:\n{model or 'local'}"})
            except Exception:  # noqa: BLE001
                pass

            in_flight: set[asyncio.Task] = set()
            while True:
                cancelled = await self.heartbeat(session)
                for task_id in cancelled:
                    proc = self._running.get(task_id)
                    if proc and proc.returncode is None:
                        self._cancelled.add(task_id)
                        proc.kill()
                for task in await self.poll_tasks(session):
                    t = asyncio.create_task(self.execute(session, task))
                    in_flight.add(t)
                    t.add_done_callback(in_flight.discard)
                await asyncio.sleep(interval)


async def run_agent(gateway_url: str, agent_id: str, token: str,
                    hermes_bin: str = "hermes", interval: int = 30) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    agent = RemoteAgent(gateway_url, agent_id, token, hermes_bin=hermes_bin)
    await agent.run(interval=interval)
