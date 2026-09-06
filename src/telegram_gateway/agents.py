"""Agent registry: registration, heartbeat, status tracking (generic, no hardcoded hosts)."""

from __future__ import annotations

import logging
import socket
from datetime import datetime, timedelta, timezone

from .config import Config
from .database import Database
from .models import Agent, utcnow

log = logging.getLogger("tg.agents")


def detect_hostname() -> str:
    return socket.gethostname() or "unknown"


class AgentManager:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    def register(self, agent_id: str, *, hostname: str = "", os_name: str = "",
                 model: str = "", capabilities: list[str] | None = None,
                 version: str = "", local: bool = False) -> Agent:
        existing = self.db.get_agent(agent_id)
        agent = Agent(
            agent_id=agent_id,
            hostname=hostname or detect_hostname(),
            os_name=os_name,
            model=model,
            capabilities=capabilities or [],
            version=version,
            status="online",
            last_seen=utcnow(),
            registered_at=existing.registered_at if existing else utcnow(),
            local=local or agent_id in self.config.local_agents,
        )
        self.db.upsert_agent(agent)
        log.info("Agent registered: %s (host=%s model=%s)", agent_id, agent.hostname, agent.model)
        return agent

    def heartbeat(self, agent_id: str) -> Agent | None:
        agent = self.db.get_agent(agent_id)
        if not agent:
            return None
        self.db.update_agent_status(agent_id, "online", utcnow())
        agent.status = "online"
        agent.last_seen = utcnow()
        return agent

    def mark_offline(self, agent_id: str) -> None:
        self.db.update_agent_status(agent_id, "offline", utcnow())

    def get(self, agent_id: str) -> Agent | None:
        return self.db.get_agent(agent_id)

    def list_agents(self) -> list[Agent]:
        return self.db.list_agents()

    def is_online(self, agent_id: str) -> bool:
        agent = self.db.get_agent(agent_id)
        return bool(agent and agent.status == "online")

    def check_timeouts(self) -> list[str]:
        """Mark agents offline when heartbeats stop. Returns newly-offline ids."""
        went_offline: list[str] = []
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.config.agent_offline_after)
        for agent in self.db.list_agents():
            if agent.status != "online" or not agent.last_seen:
                continue
            try:
                last = datetime.fromisoformat(agent.last_seen)
            except ValueError:
                continue
            if last < cutoff:
                self.db.update_agent_status(agent.agent_id, "offline", agent.last_seen)
                went_offline.append(agent.agent_id)
                log.warning("Agent %s marked offline (last seen %s)", agent.agent_id, agent.last_seen)
        return went_offline

    def available(self) -> list[str]:
        """Agent ids that can receive work: online, or registered local agents."""
        out = []
        for agent in self.db.list_agents():
            if agent.status == "online":
                out.append(agent.agent_id)
        return out

    def resolve(self, requested: str | None) -> str | None:
        """Resolve an agent id (case-insensitive); fall back to configured default."""
        agents = {a.agent_id.lower(): a.agent_id for a in self.db.list_agents()}
        if requested:
            return agents.get(requested.lower())
        default = self.config.default_agent.lower()
        return agents.get(default, self.config.default_agent)
