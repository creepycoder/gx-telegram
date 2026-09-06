"""Data model dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


AGENT_STATUSES = ("online", "offline", "unknown")

TASK_STATUSES = ("queued", "running", "completed", "failed", "cancelled")

NOTIFICATION_TYPES = (
    "success", "info", "warning", "error", "progress",
    "task_started", "task_completed", "task_failed",
    "scheduled_task", "startup", "shutdown",
)

PRIORITIES = ("low", "normal", "high", "critical")


@dataclass
class Agent:
    agent_id: str
    hostname: str = ""
    os_name: str = ""
    model: str = ""
    capabilities: list[str] = field(default_factory=list)
    version: str = ""
    status: str = "unknown"
    last_seen: str = ""
    registered_at: str = ""
    local: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "hostname": self.hostname,
            "os": self.os_name,
            "model": self.model,
            "capabilities": self.capabilities,
            "version": self.version,
            "status": self.status,
            "last_seen": self.last_seen,
            "registered_at": self.registered_at,
            "local": self.local,
        }


@dataclass
class Task:
    task_id: str
    agent_id: str
    prompt: str
    telegram_user_id: int | None = None
    telegram_chat_id: int | None = None
    status: str = "queued"
    created_at: str = field(default_factory=utcnow)
    started_at: str | None = None
    completed_at: str | None = None
    result: str | None = None
    error: str | None = None
    conversation_key: str = ""
    progress_message_id: int | None = None

    def to_dict(self, include_prompt: bool = True) -> dict[str, Any]:
        d = {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
        }
        if include_prompt:
            d["prompt"] = self.prompt
        return d


@dataclass
class Conversation:
    key: str
    agent_id: str
    telegram_user_id: int
    telegram_chat_id: int
    updated_at: str = field(default_factory=utcnow)


@dataclass
class Notification:
    notification_id: str
    agent_id: str
    type: str
    priority: str
    message: str
    chat_id: int | None = None
    status: str = "pending"  # pending | sent | failed | skipped | ignored
    created_at: str = field(default_factory=utcnow)
    telegram_message_id: int | None = None
