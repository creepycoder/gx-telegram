"""Security tests: allowlists, agent token auth, rate limiting, redaction."""

from __future__ import annotations

from telegram_gateway.config import Config
from telegram_gateway.security import (
    RateLimiter,
    redact,
    secrets_list,
    token_fingerprint,
    verify_agent_token,
)


# --- allowlists -------------------------------------------------------------

def test_allowed_user_and_chat(config):
    assert config.is_allowed(111, 222) is True


def test_unknown_user_denied(config):
    assert config.is_allowed(999, 222) is False


def test_known_user_wrong_chat_denied(config):
    assert config.is_allowed(111, 333) is False


def test_none_denied(config):
    assert config.is_allowed(None, None) is False


def test_chat_only_allowlist():
    cfg = Config(allowed_user_ids=[], allowed_chat_ids=[222])
    assert cfg.is_allowed(999, 222) is True
    assert cfg.is_allowed(999, 333) is False


def test_empty_allowlists_deny_everything():
    cfg = Config()
    assert cfg.is_allowed(111, 222) is False


# --- agent token auth -------------------------------------------------------

def test_verify_agent_token_ok(config, monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN_SCAR16", "scar16-super-secret")
    assert verify_agent_token(config, "scar16", "scar16-super-secret") is True


def test_verify_agent_token_case_insensitive_id(config, monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN_SCAR16", "scar16-super-secret")
    assert verify_agent_token(config, "SCAR16", "scar16-super-secret") is True


def test_verify_agent_token_wrong(config, monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN_SCAR16", "scar16-super-secret")
    assert verify_agent_token(config, "scar16", "wrong") is False


def test_verify_agent_token_missing(config, monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN_SCAR16", "scar16-super-secret")
    assert verify_agent_token(config, "scar16", None) is False


def test_verify_agent_token_unconfigured_agent(config, monkeypatch):
    monkeypatch.delenv("AGENT_TOKEN_NAS01", raising=False)
    assert verify_agent_token(config, "nas01", "anything") is False


# --- rate limiter ------------------------------------------------------------

def test_rate_limiter_window():
    rl = RateLimiter(max_per_minute=3)
    assert [rl.allow("u1") for _ in range(5)] == [True, True, True, False, False]


def test_rate_limiter_isolated_per_key():
    rl = RateLimiter(max_per_minute=1)
    assert rl.allow("a") is True
    assert rl.allow("b") is True
    assert rl.allow("a") is False


def test_rate_limiter_window_expiry(monkeypatch):
    import time as time_mod
    real = time_mod.monotonic()
    rl = RateLimiter(max_per_minute=1)
    assert rl.allow("u") is True
    monkeypatch.setattr(time_mod, "monotonic", lambda: real + 61.0)
    assert rl.allow("u") is True


def test_rate_limiter_disabled():
    rl = RateLimiter(max_per_minute=0)
    assert all(rl.allow("u") for _ in range(100))


# --- redaction ----------------------------------------------------------------

def test_redact_removes_secrets(config):
    text = "failed with token 123456:TEST-token-secret while calling"
    out = redact(text, secrets_list(config))
    assert "TEST-token-secret" not in out
    assert "***REDACTED***" in out


def test_redact_env_agent_tokens(monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN_SCAR16", "scar16-super-secret")
    cfg = Config(bot_token="x")
    out = redact("leak: scar16-super-secret detected", secrets_list(cfg))
    assert "scar16-super-secret" not in out


def test_redact_keeps_clean_text(config):
    assert redact("all good", secrets_list(config)) == "all good"


def test_short_secrets_not_matched():
    # strings shorter than 6 chars are not redacted (avoid false positives)
    assert redact("abc def", ["abc"]) == "abc def"


def test_token_fingerprint_stable_and_short():
    fp = token_fingerprint("secret-token-value")
    assert len(fp) == 8
    assert fp == token_fingerprint("secret-token-value")
    assert "secret" not in fp
    assert token_fingerprint("") == "-"
