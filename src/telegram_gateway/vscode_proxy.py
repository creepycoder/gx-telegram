"""VS Code ↔ Qwen Telegram mirror: OpenAI-compatible passthrough proxy.

Sits between VS Code Copilot Chat and llama-server (the "customendpoint"
model in chatLanguageModels.json) and mirrors each conversation turn to
Telegram through the gateway's admin API:

    VS Code ─POST /v1/chat/completions─► proxy :30001 ─► llama-server :30000
                                           │
                                           └─ notify ─► gateway :40000 ─► Telegram

Design notes:
- Fail-open: mirror errors are logged, never surfaced to VS Code. A stopped
  gateway must never break the chat.
- In agent mode a turn spans many requests; the user message is mirrored once
  (first request, last message role "user") and the assistant reply is
  mirrored only when it is a *final* answer — responses that contain tool
  calls are intermediate steps and stay silent, so Telegram gets one clean
  message per turn instead of spam.
- Requests with a tiny ``max_tokens`` (title generation, helper calls) and
  completions with empty content are skipped.

Environment:
- VSCODE_PROXY_UPSTREAM      default http://localhost:30000
- VSCODE_PROXY_HOST          default 127.0.0.1
- VSCODE_PROXY_PORT          default 30001
- VSCODE_PROXY_SKIP_MAX_TOKENS  default 128 (0 disables the filter)
- VSCODE_PROXY_NOTIFY_PRIORITY  default "normal"
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from typing import Any

import aiohttp
from aiohttp import web

from .config import Config

log = logging.getLogger("tg.vscode_proxy")

TRUNCATE = 3200
# Headers that must not be forwarded in either direction.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length",
    "content-encoding", "accept-encoding", "date", "server", "host",
}


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def content_text(content: Any) -> str:
    """Flatten an OpenAI message content field (str or list of parts) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content
                       if isinstance(p, dict) and p.get("type") == "text")
    return ""


# Copilot Chat prepends boilerplate context blocks (environment, editor state,
# memory, instructions) to every user prompt. Mirror only the real message.
_CONTEXT_BLOCK_RE = re.compile(
    r"<(context|editorContext|reminderInstructions|userMemory|sessionMemory|"
    r"repoMemory|instructions|toolUseInstructions|editFileInstructions|"
    r"notebookInstructions|outputFormatting|memoryInstructions|skills|agents|"
    r"attached_files|selected_codes|workspace_info|environment_info)\b[^>]*>"
    r".*?</\1>",
    re.DOTALL | re.IGNORECASE)
_USER_REQUEST_RE = re.compile(r"<userRequest>\s*(.*?)\s*</userRequest>",
                              re.DOTALL | re.IGNORECASE)


def clean_user_text(text: str) -> str:
    """Extract the user's actual message from a Copilot prompt envelope."""
    m = _USER_REQUEST_RE.search(text)
    if m:
        return m.group(1).strip()
    cleaned = _CONTEXT_BLOCK_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


class TelegramMirror:
    """Push chat events to Telegram through the gateway admin API (fail-open)."""

    def __init__(self, config: Config, session: aiohttp.ClientSession):
        host = "127.0.0.1" if config.host in ("0.0.0.0", "") else config.host
        self.url = f"http://{host}:{config.port}/telegram/notify"
        self.headers = {"X-Admin-Token": config.admin_token} if config.admin_token else {}
        self.session = session
        self.priority = _env("VSCODE_PROXY_NOTIFY_PRIORITY", "normal")

    async def notify(self, title: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if len(text) > TRUNCATE:
            text = text[:TRUNCATE] + "\n…[troncato]"
        body = {
            "agent_id": "vscode",
            "type": "info",
            "priority": self.priority,
            "message": f"{title}\n\n{text}",
            "notification_id": str(uuid.uuid4()),
        }
        try:
            async with self.session.post(
                    self.url, json=body, headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json(content_type=None)
            if not data.get("ok"):
                log.warning("Telegram mirror rejected notify: %s", data.get("error"))
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("Telegram mirror unreachable (chat unaffected): %s", exc)


class VscodeProxy:
    def __init__(self, config: Config):
        self.config = config
        self.upstream = _env("VSCODE_PROXY_UPSTREAM", "http://localhost:30000").rstrip("/")
        self.skip_max_tokens = int(_env("VSCODE_PROXY_SKIP_MAX_TOKENS", "128"))
        self.session: aiohttp.ClientSession | None = None
        self.mirror: TelegramMirror | None = None
        self.app = web.Application()
        self.app.on_startup.append(self._on_startup)
        self.app.on_cleanup.append(self._on_cleanup)
        r = self.app.router
        r.add_post("/v1/chat/completions", self.chat)
        r.add_route("*", "/{tail:.*}", self.passthrough)

    async def _on_startup(self, _app: web.Application) -> None:
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=15))
        self.mirror = TelegramMirror(self.config, self.session)

    async def _on_cleanup(self, _app: web.Application) -> None:
        if self.session:
            await self.session.close()

    # -- helpers ---------------------------------------------------------------

    def _forward_headers(self, request: web.BaseRequest) -> dict[str, str]:
        return {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}

    def _response_headers(self, upstream: aiohttp.ClientResponse) -> dict[str, str]:
        return {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}

    def _max_tokens_ok(self, body: dict[str, Any]) -> bool:
        max_tokens = body.get("max_tokens")
        return not (self.skip_max_tokens and isinstance(max_tokens, int)
                    and 0 < max_tokens <= self.skip_max_tokens)

    def _is_user_turn_start(self, body: dict[str, Any]) -> bool:
        """First request of a turn: the user just sent a message."""
        messages = body.get("messages") or []
        return bool(messages) and messages[-1].get("role") == "user" \
            and self._max_tokens_ok(body)

    def _mirrors_reply(self, body: dict[str, Any]) -> bool:
        """Mirror the assistant reply for the user's turn *or* an agent turn.

        In agent mode the final answer arrives on a request whose last
        message is a tool result, not the user's message; the presence of
        tool history identifies it as the continuation of a mirrored turn.
        """
        if not self._max_tokens_ok(body):
            return False  # title generation / other internal helper calls
        if self._is_user_turn_start(body):
            return True
        for m in body.get("messages") or []:
            if m.get("role") == "tool" or (
                    m.get("role") == "assistant" and m.get("tool_calls")):
                return True
        return False

    async def _upstream_post(self, request: web.BaseRequest, path: str,
                             raw: bytes) -> aiohttp.ClientResponse:
        assert self.session is not None
        return await self.session.post(
            f"{self.upstream}{path}", data=raw,
            headers=self._forward_headers(request),
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=15))

    # -- handlers ----------------------------------------------------------------

    async def chat(self, request: web.Request) -> web.StreamResponse:
        raw = await request.read()
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        assert self.mirror is not None
        mirror_reply = isinstance(body, dict) and self._mirrors_reply(body)
        if isinstance(body, dict) and self._is_user_turn_start(body):
            user_text = clean_user_text(content_text(body["messages"][-1].get("content")))
            if user_text:
                await self.mirror.notify("💬 VS Code — messaggio tuo", user_text)

        try:
            up = await self._upstream_post(request, "/v1/chat/completions", raw)
        except aiohttp.ClientError as exc:
            log.error("Upstream %s error: %s", self.upstream, exc)
            return web.json_response({"error": f"upstream unreachable: {exc}"}, status=502)

        stream = bool(body.get("stream")) if isinstance(body, dict) else False
        if not stream:
            data = await up.read()
            if mirror_reply and up.status == 200:
                try:
                    result = json.loads(data)
                    message = result["choices"][0]["message"]
                    # Responses with tool calls are intermediate agent steps.
                    if not message.get("tool_calls"):
                        reply = content_text(message.get("content"))
                        await self.mirror.notify("🤖 Qwen (GX10)", reply)
                except (ValueError, KeyError, IndexError):
                    pass
            return web.Response(status=up.status, body=data,
                                headers=self._response_headers(up))

        resp = web.StreamResponse(status=up.status, headers=self._response_headers(up))
        await resp.prepare(request)
        reply_parts: list[str] = []
        saw_tool_call = False
        line_buf = b""
        interrupted = False
        try:
            async for chunk in up.content.iter_any():
                await resp.write(chunk)
                line_buf += chunk
                while b"\n" in line_buf:
                    line, line_buf = line_buf.split(b"\n", 1)
                    if self._sse_tool_call(line):
                        saw_tool_call = True
                    piece = self._sse_delta(line)
                    if piece:
                        reply_parts.append(piece)
        except (ConnectionResetError, aiohttp.ClientError):
            interrupted = True
        finally:
            up.release()
        if mirror_reply and not saw_tool_call:
            reply = "".join(reply_parts).strip()
            if reply:
                if interrupted:
                    reply += "\n\n⏹ [risposta interrotta]"
                await self.mirror.notify("🤖 Qwen (GX10)", reply)
        if not interrupted:
            await resp.write_eof()
        return resp

    @staticmethod
    def _sse_tool_call(line: bytes) -> bool:
        line = line.strip()
        if not line.startswith(b"data:"):
            return False
        payload = line[5:].strip()
        if payload in (b"", b"[DONE]"):
            return False
        try:
            event = json.loads(payload)
            delta = event["choices"][0].get("delta", {})
        except (ValueError, KeyError, IndexError):
            return False
        return bool(delta.get("tool_calls"))

    @staticmethod
    def _sse_delta(line: bytes) -> str:
        line = line.strip()
        if not line.startswith(b"data:"):
            return ""
        payload = line[5:].strip()
        if payload in (b"", b"[DONE]"):
            return ""
        try:
            event = json.loads(payload)
            delta = event["choices"][0].get("delta", {})
            return content_text(delta.get("content"))
        except (ValueError, KeyError, IndexError):
            return ""

    async def passthrough(self, request: web.Request) -> web.StreamResponse:
        """Proxy everything else (e.g. /v1/models) unchanged."""
        assert self.session is not None
        raw = await request.read()
        try:
            method = getattr(self.session, request.method.lower(), None)
            if method is None:
                return web.json_response({"error": "method not allowed"}, status=405)
            up = await method(
                f"{self.upstream}{request.path_qs}", data=raw or None,
                headers=self._forward_headers(request),
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=15))
        except aiohttp.ClientError as exc:
            return web.json_response({"error": f"upstream error: {exc}"}, status=502)
        data = await up.read()
        return web.Response(status=up.status, body=data,
                            headers=self._response_headers(up))


async def run_proxy(config: Config, host: str | None = None,
                    port: int | None = None) -> None:
    proxy = VscodeProxy(config)
    host = host or _env("VSCODE_PROXY_HOST", "127.0.0.1")
    port = port or int(_env("VSCODE_PROXY_PORT", "30001"))
    runner = web.AppRunner(proxy.app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("VS Code mirror proxy on %s:%d → %s (Telegram via gateway API)",
             host, port, proxy.upstream)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
