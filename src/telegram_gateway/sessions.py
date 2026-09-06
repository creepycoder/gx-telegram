"""Conversation/session management: user+chat+agent scoped contexts."""

from __future__ import annotations

from .config import Config
from .database import Database
from .models import Conversation, utcnow


def make_key(user_id: int, chat_id: int, agent_id: str) -> str:
    return f"{user_id}:{chat_id}:{agent_id}"


class SessionManager:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    def touch(self, user_id: int, chat_id: int, agent_id: str) -> Conversation:
        key = make_key(user_id, chat_id, agent_id)
        conv = Conversation(key=key, agent_id=agent_id, telegram_user_id=user_id,
                            telegram_chat_id=chat_id, updated_at=utcnow())
        self.db.upsert_conversation(conv)
        return conv

    def add_user_message(self, key: str, content: str) -> None:
        self.db.add_message(key, "user", content, utcnow())

    def add_assistant_message(self, key: str, content: str) -> None:
        self.db.add_message(key, "assistant", content, utcnow())

    def context_block(self, key: str) -> str:
        """Recent turns formatted as context to prepend to a prompt."""
        rows = self.db.recent_messages(key, self.config.context_turns * 2)
        if not rows:
            return ""
        lines = [f"{r['role'].upper()}: {r['content']}" for r in rows]
        return "Previous conversation context (for continuity):\n" + "\n".join(lines) + "\n\n"
