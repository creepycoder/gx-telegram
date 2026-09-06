"""Configuration loading from environment variables / .env file."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: str | os.PathLike | None = None) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (existing env wins)."""
    if path is None:
        path = os.environ.get("GATEWAY_ENV_FILE", Path.cwd() / ".env")
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key) and key not in os.environ:
            os.environ[key] = value


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


@dataclass
class Config:
    # Telegram
    bot_token: str = ""
    allowed_user_ids: list[int] = field(default_factory=list)
    allowed_chat_ids: list[int] = field(default_factory=list)
    default_chat_id: int | None = None
    telegram_api_base: str = "https://api.telegram.org"

    # Gateway HTTP
    host: str = "127.0.0.1"
    port: int = 30100
    admin_token: str = ""

    # Agents
    local_agents: list[str] = field(default_factory=lambda: ["gx10"])
    default_agent: str = "gx10"
    heartbeat_interval: int = 30
    agent_offline_after: int = 90

    # Hermes
    hermes_bin: str = "hermes"
    task_timeout: int = 1800
    max_concurrent_tasks: int = 4
    context_turns: int = 6

    # Notifications
    low_priority_mode: str = "ignore"  # ignore | telegram
    notify_min_interval: float = 1.1   # seconds between edits of the same message

    # Rate limiting
    rate_limit_per_minute: int = 20

    # Storage / logging
    db_path: str = "data/gateway.db"
    log_level: str = "INFO"

    @staticmethod
    def from_env() -> "Config":
        env = os.environ
        user_ids = [int(v) for v in _split_csv(env.get("TELEGRAM_ALLOWED_USER_IDS", "")) if v.lstrip("-").isdigit()]
        chat_ids = [int(v) for v in _split_csv(env.get("TELEGRAM_ALLOWED_CHAT_IDS", "")) if v.lstrip("-").isdigit()]
        default_chat = env.get("DEFAULT_CHAT_ID", "")
        return Config(
            bot_token=env.get("TELEGRAM_BOT_TOKEN", ""),
            allowed_user_ids=user_ids,
            allowed_chat_ids=chat_ids,
            default_chat_id=int(default_chat) if default_chat.lstrip("-").isdigit() else None,
            telegram_api_base=env.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/"),
            host=env.get("GATEWAY_HOST", "127.0.0.1"),
            port=_int(env.get("GATEWAY_PORT"), 30100),
            admin_token=env.get("GATEWAY_ADMIN_TOKEN", ""),
            local_agents=_split_csv(env.get("LOCAL_AGENTS", "gx10")) or ["gx10"],
            default_agent=env.get("DEFAULT_AGENT", "gx10"),
            heartbeat_interval=_int(env.get("HEARTBEAT_INTERVAL"), 30),
            agent_offline_after=_int(env.get("AGENT_OFFLINE_AFTER"), 90),
            hermes_bin=env.get("HERMES_BIN", "hermes"),
            task_timeout=_int(env.get("TASK_TIMEOUT"), 1800),
            max_concurrent_tasks=_int(env.get("MAX_CONCURRENT_TASKS"), 4),
            context_turns=_int(env.get("CONTEXT_TURNS"), 6),
            low_priority_mode=env.get("LOW_PRIORITY_MODE", "ignore"),
            rate_limit_per_minute=_int(env.get("RATE_LIMIT_PER_MINUTE"), 20),
            db_path=env.get("DB_PATH", "data/gateway.db"),
            log_level=env.get("LOG_LEVEL", "INFO"),
        )

    def agent_token(self, agent_id: str) -> str | None:
        """Per-agent shared secret, e.g. AGENT_TOKEN_SCAR16=..."""
        return os.environ.get(f"AGENT_TOKEN_{agent_id.upper()}") or None

    def target_chat_id(self) -> int | None:
        """Chat used for notifications without an explicit chat."""
        if self.default_chat_id:
            return self.default_chat_id
        if self.allowed_chat_ids:
            return self.allowed_chat_ids[0]
        if self.allowed_user_ids:
            return self.allowed_user_ids[0]
        return None

    def is_allowed(self, user_id: int | None, chat_id: int | None) -> bool:
        """Allowlist check: user must be allowed; if a chat allowlist is set, chat too."""
        if self.allowed_user_ids and user_id in self.allowed_user_ids:
            if not self.allowed_chat_ids or chat_id in self.allowed_chat_ids:
                return True
        if not self.allowed_user_ids and self.allowed_chat_ids and chat_id in self.allowed_chat_ids:
            return True
        return False
