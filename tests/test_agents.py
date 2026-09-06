"""Agent registry tests: registration, heartbeat, offline timeouts, generic agents."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from telegram_gateway.agents import AgentManager
from telegram_gateway.models import utcnow


def _with_last_seen(db, agent_id, seconds_ago: int):
    ts = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat(timespec="seconds")
    db.update_agent_status(agent_id, "online", ts)


def test_register_detects_hostname(config, db):
    mgr = AgentManager(config, db)
    agent = mgr.register("scar16", model="Local model")
    assert agent.agent_id == "scar16"
    assert agent.hostname  # auto-detected, not empty
    assert agent.status == "online"


def test_register_generic_agent_no_hardcoding(config, db):
    mgr = AgentManager(config, db)
    mgr.register("nas-01", hostname="nas", model="tiny", capabilities=["fs"])
    mgr.register("cloud-vm", hostname="vm", model="gpt", capabilities=["api"])
    ids = {a.agent_id for a in mgr.list_agents()}
    assert {"nas-01", "cloud-vm"} <= ids


def test_heartbeat_marks_online(config, db):
    mgr = AgentManager(config, db)
    mgr.register("scar16")
    _with_last_seen(db, "scar16", 3600)
    db.update_agent_status("scar16", "offline", utcnow())
    agent = mgr.heartbeat("scar16")
    assert agent.status == "online"


def test_heartbeat_unknown_agent(config, db):
    mgr = AgentManager(config, db)
    assert mgr.heartbeat("ghost") is None


def test_check_timeouts_marks_offline(config, db):
    config.agent_offline_after = 90
    mgr = AgentManager(config, db)
    mgr.register("scar16")
    _with_last_seen(db, "scar16", 120)
    went = mgr.check_timeouts()
    assert went == ["scar16"]
    assert mgr.get("scar16").status == "offline"


def test_check_timeouts_keeps_fresh_agent(config, db):
    mgr = AgentManager(config, db)
    mgr.register("scar16")
    assert mgr.check_timeouts() == []
    assert mgr.get("scar16").status == "online"


def test_resolve_case_insensitive_and_default(config, db):
    mgr = AgentManager(config, db)
    mgr.register("gx10")
    mgr.register("scar16")
    assert mgr.resolve("SCAR16") == "scar16"
    assert mgr.resolve(None) == "gx10"
    assert mgr.resolve("unknown") is None


def test_local_flag_from_config(config, db):
    mgr = AgentManager(config, db)
    gx = mgr.register("gx10")
    remote = mgr.register("scar16")
    assert gx.local is True
    assert remote.local is False
