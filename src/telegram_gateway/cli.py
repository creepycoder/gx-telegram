"""CLI: telegram-gateway health|agents|test|send|tasks|notify|run|agent"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import socket
import sys
import urllib.error
import urllib.request

from .config import Config, load_dotenv


def _http(method: str, url: str, token: str | None = None,
          body: dict | None = None, timeout: int = 30) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Admin-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001
            return {"error": f"HTTP {exc.code}"}
    except urllib.error.URLError as exc:
        return {"error": f"connection failed: {exc.reason}"}


def _base(config: Config) -> str:
    return f"http://{config.host if config.host != '0.0.0.0' else '127.0.0.1'}:{config.port}"


def cmd_health(config: Config, _args) -> int:
    res = _http("GET", f"{_base(config)}/health")
    print(json.dumps(res, indent=2))
    return 0 if res.get("status") in ("ok", "degraded") and "error" not in res else 1


def cmd_agents(config: Config, _args) -> int:
    res = _http("GET", f"{_base(config)}/agents", token=config.admin_token)
    if "error" in res:
        print(f"Error: {res['error']}", file=sys.stderr)
        return 1
    for a in res.get("agents", []):
        icon = "🟢" if a["status"] == "online" else "🔴"
        print(f"{icon} {a['agent_id']}  host={a['hostname']}  model={a['model'] or '-'}  "
              f"status={a['status']}  last_seen={a['last_seen']}")
    return 0


def cmd_tasks(config: Config, args) -> int:
    res = _http("GET", f"{_base(config)}/tasks?limit={args.limit}", token=config.admin_token)
    if "error" in res:
        print(f"Error: {res['error']}", file=sys.stderr)
        return 1
    for t in res.get("tasks", []):
        print(f"{t['status']:>10}  {t['task_id']}  [{t['agent_id']}]  {t['prompt'][:60]}")
    return 0


def cmd_send(config: Config, args) -> int:
    body = {"text": args.message}
    if args.chat_id:
        body["chat_id"] = int(args.chat_id)
    res = _http("POST", f"{_base(config)}/telegram/message", token=config.admin_token, body=body)
    if res.get("ok"):
        print("Message sent.")
        return 0
    print(f"Error: {res.get('error')}", file=sys.stderr)
    return 1


def cmd_notify(config: Config, args) -> int:
    body = {"agent_id": args.agent, "type": args.type, "message": args.message,
            "priority": args.priority}
    if args.chat_id:
        body["chat_id"] = int(args.chat_id)
    res = _http("POST", f"{_base(config)}/telegram/notify", token=config.admin_token, body=body)
    if res.get("ok"):
        print(f"Notification {res.get('notification_id')} → {res.get('status')}")
        return 0
    print(f"Error: {res.get('error')}", file=sys.stderr)
    return 1


def cmd_test(config: Config, args) -> int:
    """Send a REAL test message directly through the Telegram Bot API (no gateway needed)."""
    if not config.bot_token:
        print("TELEGRAM_BOT_TOKEN not set.", file=sys.stderr)
        return 1
    chat_id = args.chat_id or config.target_chat_id()
    if not chat_id:
        print("No target chat: set DEFAULT_CHAT_ID or TELEGRAM_ALLOWED_CHAT_IDS.", file=sys.stderr)
        return 1
    host = socket.gethostname()
    text = (
        "🟢 GX Telegram Gateway\n\n"
        "Telegram integration test successful.\n\n"
        "Host:\n"
        f"{host}\n\n"
        "Status:\n"
        "READY"
    )
    from .telegram import TelegramClient
    client = TelegramClient(config)

    async def _send():
        await client.start()
        try:
            me = await client.get_me()
            result = await client.send_message(chat_id, text)
            return me.get("username"), result.get("message_id")
        finally:
            await client.close()

    try:
        username, message_id = asyncio.run(_send())
    except Exception as exc:  # noqa: BLE001
        from .security import redact
        print(f"FAILED: {redact(str(exc), [config.bot_token])}", file=sys.stderr)
        return 1
    print(f"✅ Test message sent via @{username} (message_id={message_id})")
    return 0


def cmd_run(_config: Config, _args) -> int:
    from .main import main as run_main
    run_main()
    return 0


def cmd_agent(config: Config, args) -> int:
    """Run this machine as a remote agent (register + heartbeat + execute via Hermes)."""
    from .agent_runner import run_agent
    gateway_url = args.gateway_url
    agent_id = args.agent_id
    token = os.environ.get(f"AGENT_TOKEN_{agent_id.upper()}") or os.environ.get("AGENT_TOKEN")
    if not token:
        token = os.environ.get("GATEWAY_AGENT_TOKEN") or getpass.getpass("Agent token: ")
    asyncio.run(run_agent(gateway_url, agent_id, token,
                          hermes_bin=config.hermes_bin,
                          interval=config.heartbeat_interval))
    return 0


def cmd_vscode_proxy(config: Config, args) -> int:
    """Run the OpenAI-compatible proxy mirroring VS Code <-> Qwen chats to Telegram."""
    from .main import setup_logging
    from .vscode_proxy import run_proxy
    setup_logging(config.log_level)
    asyncio.run(run_proxy(config, host=args.host, port=args.port))
    return 0


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="telegram-gateway",
                                     description="GX10 Telegram Gateway administration")
    parser.add_argument("--env-file", help="Path to .env file")
    args, rest = parser.parse_known_args(argv)
    if args.env_file:
        os.environ["GATEWAY_ENV_FILE"] = args.env_file
        load_dotenv(args.env_file)
    config = Config.from_env()

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("health", help="Check gateway health")
    sub.add_parser("agents", help="List registered agents")
    p_tasks = sub.add_parser("tasks", help="List recent tasks")
    p_tasks.add_argument("--limit", type=int, default=20)
    p_send = sub.add_parser("send", help="Send a raw Telegram message (via gateway API)")
    p_send.add_argument("message")
    p_send.add_argument("--chat-id")
    p_notify = sub.add_parser("notify", help="Send a formatted notification (via gateway API)")
    p_notify.add_argument("message")
    p_notify.add_argument("--agent", default=config.default_agent)
    p_notify.add_argument("--type", default="info")
    p_notify.add_argument("--priority", default="normal")
    p_notify.add_argument("--chat-id")
    p_test = sub.add_parser("test", help="Send a REAL Telegram test message (direct Bot API)")
    p_test.add_argument("--chat-id")
    sub.add_parser("run", help="Run the gateway in the foreground")
    p_agent = sub.add_parser("agent", help="Run this machine as a remote agent")
    p_agent.add_argument("--gateway-url", required=True,
                         help="Gateway base URL, e.g. http://192.168.1.152:30100")
    p_agent.add_argument("--agent-id", required=True, help="Agent id, e.g. scar16")
    p_proxy = sub.add_parser("vscode-proxy",
                             help="OpenAI proxy that mirrors VS Code <-> Qwen chats to Telegram")
    p_proxy.add_argument("--host", default=None, help="Bind host (default 127.0.0.1)")
    p_proxy.add_argument("--port", type=int, default=None, help="Bind port (default 30001)")

    ns = parser.parse_args(argv)
    if not ns.command:
        parser.print_help()
        sys.exit(2)

    handlers = {
        "health": cmd_health, "agents": cmd_agents, "tasks": cmd_tasks,
        "send": cmd_send, "notify": cmd_notify, "test": cmd_test,
        "run": cmd_run, "agent": cmd_agent, "vscode-proxy": cmd_vscode_proxy,
    }
    sys.exit(handlers[ns.command](config, ns))


if __name__ == "__main__":
    main()
