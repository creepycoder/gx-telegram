"""Security helpers: allowlists, agent auth, rate limiting, secret redaction."""

from __future__ import annotations

import hashlib
import hmac
import time as _time
from collections import defaultdict, deque

from .config import Config


def verify_agent_token(config: Config, agent_id: str, token: str | None) -> bool:
    """Constant-time check of the per-agent shared secret."""
    expected = config.agent_token(agent_id)
    if not expected:
        # No token configured for this agent: only local agents may connect
        # without auth (loopback is trusted on the LAN boundary by config choice).
        return False
    if not token:
        return False
    return hmac.compare_digest(expected.encode(), token.encode())


class RateLimiter:
    """Sliding-window rate limiter, configurable per minute."""

    def __init__(self, max_per_minute: int):
        self.max_per_minute = max_per_minute
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        if self.max_per_minute <= 0:
            return True
        now = _time.monotonic()
        q = self._events[key]
        while q and now - q[0] > 60.0:
            q.popleft()
        if len(q) >= self.max_per_minute:
            return False
        q.append(now)
        return True


def redact(text: str, secrets: list[str]) -> str:
    """Remove secret values from text (logs, errors, Telegram messages)."""
    if not text:
        return text
    out = text
    for s in secrets:
        if s and len(s) >= 6:
            out = out.replace(s, "***REDACTED***")
    return out


def secrets_list(config: Config) -> list[str]:
    import os
    items = [config.bot_token, config.admin_token]
    for agent in config.local_agents:
        items.append(config.agent_token(agent))
    for key, value in os.environ.items():
        if key.startswith(("AGENT_TOKEN_", "TELEGRAM_", "GATEWAY_ADMIN_")):
            items.append(value)
    return [s for s in items if s]


def token_fingerprint(token: str) -> str:
    """Non-reversible fingerprint for logging token usage without exposing it."""
    return hashlib.sha256(token.encode()).hexdigest()[:8] if token else "-"
