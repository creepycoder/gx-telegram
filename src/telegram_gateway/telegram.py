"""Telegram Bot API client: outbound sends with retry/backoff, editing, long polling."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from .config import Config

log = logging.getLogger("tg.telegram")

MAX_RETRIES = 5
TEXT_LIMIT = 4096


class TelegramClient:
    def __init__(self, config: Config, session: aiohttp.ClientSession | None = None):
        self.config = config
        self._session = session
        self._own_session = session is None

    async def start(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30))

    async def close(self) -> None:
        if self._own_session and self._session:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        assert self._session is not None, "TelegramClient not started"
        return self._session

    async def _api(self, method: str, payload: dict[str, Any],
                   timeout: aiohttp.ClientTimeout | None = None) -> dict[str, Any]:
        """Call the Bot API with exponential backoff; respect 429 retry_after."""
        url = f"{self.config.telegram_api_base}/bot{self.config.bot_token}/{method}"
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                post_kwargs: dict[str, Any] = {"json": payload}
                if timeout is not None:
                    post_kwargs["timeout"] = timeout
                async with self.session.post(url, **post_kwargs) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status == 429:
                        params = data.get("parameters") or {}
                        wait = float(params.get("retry_after", 2 ** attempt))
                        log.warning("Telegram 429 on %s, retrying in %.1fs", method, wait)
                        await asyncio.sleep(min(wait, 60))
                        continue
                    if data.get("ok"):
                        return data["result"]
                    if resp.status >= 500:
                        wait = min(2 ** attempt, 30)
                        log.warning("Telegram %s on %s: %s, retry in %.1fs",
                                    resp.status, method, data.get("description"), wait)
                        await asyncio.sleep(wait)
                        continue
                    raise TelegramError(data.get("description", "unknown error"), resp.status)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                wait = min(2 ** attempt, 30)
                log.warning("Telegram network error on %s: %s, retry in %.1fs", method, exc, wait)
                await asyncio.sleep(wait)
        raise TelegramError(f"Telegram API {method} failed after {MAX_RETRIES} attempts",
                            last_error=last_error)

    async def send_message(self, chat_id: int, text: str,
                           parse_mode: str | None = None) -> dict[str, Any]:
        text = text[:TEXT_LIMIT]
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text,
                                   "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return await self._api("sendMessage", payload)

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> dict[str, Any]:
        text = text[:TEXT_LIMIT]
        try:
            return await self._api("editMessageText", {
                "chat_id": chat_id, "message_id": message_id, "text": text,
                "disable_web_page_preview": True})
        except TelegramError as exc:
            if "message is not modified" in (exc.description or "").lower():
                return {}  # nothing to update
            raise

    async def get_updates(self, offset: int | None, timeout: int = 50) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message"],
            "drop_pending_updates": False,
        }
        if offset:
            payload["offset"] = offset
        # Long polling holds the connection open server-side for `timeout`
        # seconds; the per-request client timeout must exceed it or every
        # poll is killed locally as asyncio.TimeoutError.
        poll_timeout = aiohttp.ClientTimeout(total=timeout + 15)
        return await self._api("getUpdates", payload, timeout=poll_timeout)

    async def get_me(self) -> dict[str, Any]:
        return await self._api("getMe", {})

    async def delete_webhook(self) -> None:
        await self._api("deleteWebhook", {"drop_pending_updates": False})


class TelegramError(Exception):
    def __init__(self, description: str, status: int | None = None, last_error=None):
        super().__init__(description)
        self.description = description
        self.status = status
        self.last_error = last_error
