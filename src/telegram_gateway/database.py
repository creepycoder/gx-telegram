"""SQLite persistence layer (single connection, thread-safe via lock)."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import Agent, Conversation, Notification, Task

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    hostname TEXT DEFAULT '',
    os_name TEXT DEFAULT '',
    model TEXT DEFAULT '',
    capabilities TEXT DEFAULT '[]',
    version TEXT DEFAULT '',
    status TEXT DEFAULT 'unknown',
    last_seen TEXT DEFAULT '',
    registered_at TEXT DEFAULT '',
    local INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    telegram_user_id INTEGER,
    telegram_chat_id INTEGER,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    result TEXT,
    error TEXT,
    conversation_key TEXT DEFAULT '',
    progress_message_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
CREATE TABLE IF NOT EXISTS conversations (
    key TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    telegram_user_id INTEGER NOT NULL,
    telegram_chat_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_key TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_key, id);
CREATE TABLE IF NOT EXISTS notifications (
    notification_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    type TEXT NOT NULL,
    priority TEXT NOT NULL,
    message TEXT NOT NULL,
    chat_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    telegram_message_id INTEGER
);
CREATE TABLE IF NOT EXISTS telegram_users (
    user_id INTEGER PRIMARY KEY,
    username TEXT DEFAULT '',
    first_name TEXT DEFAULT '',
    last_seen TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- low level ---------------------------------------------------------

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # -- agents ------------------------------------------------------------

    def upsert_agent(self, agent: Agent) -> None:
        self.execute(
            """INSERT INTO agents (agent_id, hostname, os_name, model, capabilities,
                                   version, status, last_seen, registered_at, local)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(agent_id) DO UPDATE SET
                 hostname=excluded.hostname, os_name=excluded.os_name,
                 model=excluded.model, capabilities=excluded.capabilities,
                 version=excluded.version, status=excluded.status,
                 last_seen=excluded.last_seen, local=excluded.local""",
            (agent.agent_id, agent.hostname, agent.os_name, agent.model,
             json.dumps(agent.capabilities), agent.version, agent.status,
             agent.last_seen, agent.registered_at, int(agent.local)),
        )

    def get_agent(self, agent_id: str) -> Agent | None:
        row = self.query_one("SELECT * FROM agents WHERE agent_id=?", (agent_id,))
        return _row_to_agent(row) if row else None

    def list_agents(self) -> list[Agent]:
        return [_row_to_agent(r) for r in self.query("SELECT * FROM agents ORDER BY agent_id")]

    def update_agent_status(self, agent_id: str, status: str, last_seen: str) -> None:
        self.execute(
            "UPDATE agents SET status=?, last_seen=? WHERE agent_id=?",
            (status, last_seen, agent_id),
        )

    # -- tasks ---------------------------------------------------------------

    def insert_task(self, task: Task) -> None:
        self.execute(
            """INSERT INTO tasks (task_id, agent_id, telegram_user_id, telegram_chat_id,
                                  prompt, status, created_at, started_at, completed_at,
                                  result, error, conversation_key, progress_message_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task.task_id, task.agent_id, task.telegram_user_id, task.telegram_chat_id,
             task.prompt, task.status, task.created_at, task.started_at, task.completed_at,
             task.result, task.error, task.conversation_key, task.progress_message_id),
        )

    def update_task(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE tasks SET {cols} WHERE task_id=?", (*fields.values(), task_id))

    def get_task(self, task_id: str) -> Task | None:
        row = self.query_one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        return _row_to_task(row) if row else None

    def list_tasks(self, limit: int = 20, statuses: tuple[str, ...] | None = None) -> list[Task]:
        if statuses:
            ph = ",".join("?" for _ in statuses)
            rows = self.query(
                f"SELECT * FROM tasks WHERE status IN ({ph}) ORDER BY created_at DESC LIMIT ?",
                (*statuses, limit),
            )
        else:
            rows = self.query("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))
        return [_row_to_task(r) for r in rows]

    def fail_stale_running_tasks(self) -> int:
        """On startup, tasks that were 'running' belong to a dead process."""
        cur = self.execute(
            "UPDATE tasks SET status='failed', error='Gateway restarted while task was running', "
            "completed_at=datetime('now') WHERE status IN ('running','queued')"
        )
        return cur.rowcount

    # -- conversations -------------------------------------------------------

    def get_conversation(self, key: str) -> Conversation | None:
        row = self.query_one("SELECT * FROM conversations WHERE key=?", (key,))
        if not row:
            return None
        return Conversation(key=row["key"], agent_id=row["agent_id"],
                            telegram_user_id=row["telegram_user_id"],
                            telegram_chat_id=row["telegram_chat_id"],
                            updated_at=row["updated_at"])

    def upsert_conversation(self, conv: Conversation) -> None:
        self.execute(
            """INSERT INTO conversations (key, agent_id, telegram_user_id, telegram_chat_id, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET updated_at=excluded.updated_at""",
            (conv.key, conv.agent_id, conv.telegram_user_id, conv.telegram_chat_id, conv.updated_at),
        )

    def add_message(self, conversation_key: str, role: str, content: str, created_at: str) -> None:
        self.execute(
            "INSERT INTO messages (conversation_key, role, content, created_at) VALUES (?,?,?,?)",
            (conversation_key, role, content, created_at),
        )

    def recent_messages(self, conversation_key: str, limit: int) -> list[sqlite3.Row]:
        rows = self.query(
            "SELECT role, content FROM messages WHERE conversation_key=? ORDER BY id DESC LIMIT ?",
            (conversation_key, limit),
        )
        return list(reversed(rows))

    # -- notifications ---------------------------------------------------------

    def insert_notification(self, n: Notification) -> bool:
        """Insert with idempotency; returns False if notification_id already exists."""
        existing = self.query_one("SELECT 1 FROM notifications WHERE notification_id=?",
                                  (n.notification_id,))
        if existing:
            return False
        self.execute(
            """INSERT INTO notifications (notification_id, agent_id, type, priority, message,
                                          chat_id, status, created_at, telegram_message_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (n.notification_id, n.agent_id, n.type, n.priority, n.message,
             n.chat_id, n.status, n.created_at, n.telegram_message_id),
        )
        return True

    def update_notification(self, notification_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE notifications SET {cols} WHERE notification_id=?",
                     (*fields.values(), notification_id))

    def list_notifications(self, limit: int = 20) -> list[Notification]:
        rows = self.query("SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,))
        return [Notification(
            notification_id=r["notification_id"], agent_id=r["agent_id"], type=r["type"],
            priority=r["priority"], message=r["message"], chat_id=r["chat_id"],
            status=r["status"], created_at=r["created_at"],
            telegram_message_id=r["telegram_message_id"],
        ) for r in rows]

    # -- telegram users ----------------------------------------------------------

    def upsert_telegram_user(self, user_id: int, username: str, first_name: str, last_seen: str) -> None:
        self.execute(
            """INSERT INTO telegram_users (user_id, username, first_name, last_seen)
               VALUES (?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
                 first_name=excluded.first_name, last_seen=excluded.last_seen""",
            (user_id, username, first_name, last_seen),
        )

    # -- settings ------------------------------------------------------------------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.query_one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def _row_to_agent(r: sqlite3.Row) -> Agent:
    return Agent(
        agent_id=r["agent_id"], hostname=r["hostname"], os_name=r["os_name"],
        model=r["model"], capabilities=json.loads(r["capabilities"] or "[]"),
        version=r["version"], status=r["status"], last_seen=r["last_seen"],
        registered_at=r["registered_at"], local=bool(r["local"]),
    )


def _row_to_task(r: sqlite3.Row) -> Task:
    return Task(
        task_id=r["task_id"], agent_id=r["agent_id"], prompt=r["prompt"],
        telegram_user_id=r["telegram_user_id"], telegram_chat_id=r["telegram_chat_id"],
        status=r["status"], created_at=r["created_at"], started_at=r["started_at"],
        completed_at=r["completed_at"], result=r["result"], error=r["error"],
        conversation_key=r["conversation_key"] or "",
        progress_message_id=r["progress_message_id"],
    )
