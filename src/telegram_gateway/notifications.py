"""Notification formatting and dispatch with priority filtering + idempotency."""

from __future__ import annotations

import logging
import uuid

from .config import Config
from .database import Database
from .models import Notification, utcnow
from .telegram import TelegramClient, TelegramError

log = logging.getLogger("tg.notifications")

ICONS = {
    "success": "✅",
    "info": "ℹ️",
    "warning": "⚠️",
    "error": "❌",
    "progress": "⏳",
    "task_started": "🚀",
    "task_completed": "✅",
    "task_failed": "❌",
    "scheduled_task": "⏰",
    "startup": "🟢",
    "shutdown": "🔴",
}

LABELS = {
    "success": "SUCCESS",
    "info": "INFO",
    "warning": "WARNING",
    "error": "ERROR",
    "progress": "PROGRESS",
    "task_started": "TASK STARTED",
    "task_completed": "TASK COMPLETED",
    "task_failed": "TASK FAILED",
    "scheduled_task": "SCHEDULED TASK",
    "startup": "ONLINE",
    "shutdown": "OFFLINE",
}


def format_notification(n: Notification) -> str:
    icon = ICONS.get(n.type, "📣")
    label = LABELS.get(n.type, n.type.upper())
    agent = (n.agent_id or "agent").upper()
    return f"[{agent}] {icon} {label}\n\n{n.message}"


class NotificationManager:
    def __init__(self, config: Config, db: Database, telegram: TelegramClient):
        self.config = config
        self.db = db
        self.tg = telegram

    async def notify(
        self,
        agent_id: str,
        type: str,
        message: str,
        priority: str = "normal",
        chat_id: int | None = None,
        notification_id: str | None = None,
    ) -> Notification | None:
        """Persist (idempotent) and deliver a notification. Returns None if duplicate."""
        notification_id = notification_id or str(uuid.uuid4())
        chat_id = chat_id or self.config.target_chat_id()

        # Priority filtering
        if priority == "low" and self.config.low_priority_mode == "ignore":
            status = "ignored"
        else:
            status = "pending"

        n = Notification(
            notification_id=notification_id, agent_id=agent_id, type=type,
            priority=priority, message=message, chat_id=chat_id,
            status=status, created_at=utcnow(),
        )
        if not self.db.insert_notification(n):
            log.info("Duplicate notification %s ignored", notification_id)
            return None

        if status == "ignored":
            return n
        if chat_id is None:
            self.db.update_notification(notification_id, status="failed")
            log.error("No target chat for notification %s (set DEFAULT_CHAT_ID or allowlist)",
                      notification_id)
            n.status = "failed"
            return n

        await self._deliver(n)
        return n

    async def _deliver(self, n: Notification) -> None:
        try:
            result = await self.tg.send_message(n.chat_id, format_notification(n))
            self.db.update_notification(n.notification_id, status="sent",
                                        telegram_message_id=result.get("message_id"))
            n.status = "sent"
        except (TelegramError, Exception) as exc:  # noqa: BLE001
            log.exception("Failed to deliver notification %s", n.notification_id)
            self.db.update_notification(n.notification_id, status="failed")
            n.status = "failed"
