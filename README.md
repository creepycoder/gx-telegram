# GX Telegram Gateway

Bidirectional Telegram gateway for the ASUS Ascent **GX10**: connects the Telegram
bot [`@inferont`](https://t.me/inferont) to [Hermes Agent](#hermes-integration)
installations on the GX10 (local) and remote machines such as the SCAR16 laptop —
a generic, extensible agent registry with no hardcoded hosts.

```
                        ┌────────────────────────── GX10 (192.168.1.152) ─────────────────────────┐
 Telegram ◄──────────►  │  telegram-gateway (single process)                                      │
  (long polling)        │  ├── Telegram poller  ──► router ──► TaskManager ──► Hermes (subprocess) │
                        │  ├── HTTP API :30100 ◄────────────── agent REST (register/heartbeat/poll)│
                        │  └── SQLite (tasks, agents, conversations, notifications)               │
                        └──────────────────────────────────────┬──────────────────────────────────┘
                                                               │ LAN HTTP + shared token
                                                              ▼
                                          SCAR16: telegram-gateway agent --agent-id scar16
                                          (registers, heartbeats, polls tasks, runs local Hermes)
```

## Features

- **Telegram bridge** — long polling (works behind NAT/firewall, no webhook/TLS),
  strict **user + chat allowlists**, per-user rate limiting.
- **Generic agent registry** — any machine can register as an agent
  (`gx10`, `scar16`, `nas-01`, …). No hostnames hardcoded.
- **Local execution** — tasks on the GX10 run `hermes -z "<prompt>" --yolo`
  as a subprocess with timeout + concurrency limit.
- **Remote execution** — remote agents run a lightweight runner that registers,
  heartbeats, polls for tasks, executes Hermes locally and reports back.
- **Async task system** — `queued → running → completed | failed | cancelled`,
  progress message edited in-place, cancellation support.
- **Notification system** — types (`success|info|warning|error|progress|
  task_started|task_completed|task_failed|scheduled_task|startup|shutdown`) ×
  priorities (`low|normal|high|critical`) with idempotency (duplicate
  suppression) and low-priority filtering.
- **Heartbeats** — agents marked `offline` when silent > `AGENT_OFFLINE_AFTER`s.
- **Conversation context** — last N exchanges per (user, chat, agent) are
  prepended to subsequent prompts.
- **Safety** — secrets redacted from all errors/logs/Telegram output, agent
  endpoints token-authenticated, admin endpoints token-authenticated,
  SQLite persistence with WAL.

## Requirements

- Python **3.11+** (GX10 ships 3.12)
- A Hermes Agent installation on each machine that should *execute* tasks
- Telegram bot token (from [@BotFather](https://t.me/BotFather)) — the
  `inferont` bot is already configured for this project

## Setup (GX10)

```bash
cd ~/projects/gx-telegram
./scripts/install.sh          # creates .venv, installs package, copies .env.example → .env
$EDITOR .env                  # set TELEGRAM_BOT_TOKEN + your Telegram numeric IDs
./.venv/bin/telegram-gateway test     # sends a real test message to your chat
systemctl --user daemon-load 2>/dev/null || true
```

### How to find your Telegram numeric IDs

Send any message to any chat, then:

```bash
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates" \
  | python3 -c 'import json,sys; [print(u["message"]["from"]["id"], u["message"]["chat"]["id"]) for u in json.load(sys.stdin)["result"]]'
```

Put the user id into `TELEGRAM_ALLOWED_USER_IDS` and the chat id into
`TELEGRAM_ALLOWED_CHAT_IDS` / `DEFAULT_CHAT_ID` in `.env`.

### Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Bot token (required) |
| `TELEGRAM_ALLOWED_USER_IDS` | — | Comma-separated numeric user IDs allowed to talk to the bot (required) |
| `TELEGRAM_ALLOWED_CHAT_IDS` | — | Comma-separated chat IDs (optional second gate + notification target) |
| `DEFAULT_CHAT_ID` | first allowed chat | Where notifications are sent |
| `DEFAULT_AGENT` | `gx10` | Agent used when the message doesn't name one |
| `LOCAL_AGENTS` | `gx10` | Comma-separated agents that execute on this machine |
| `LOCAL_AGENT_MODEL` | — | Model name advertised for local agents |
| `GATEWAY_HOST` | `192.168.1.152` | Bind address. Use the LAN IP so remote agents can reach it (never `0.0.0.0` unless you know why) |
| `GATEWAY_PORT` | `30100` | HTTP API port |
| `GATEWAY_ADMIN_TOKEN` | — | Token for admin REST endpoints. **Unset = localhost-only admin access** |
| `AGENT_TOKEN_SCAR16` | — | Shared secret for agent `scar16` (any `AGENT_TOKEN_<ID>`) |
| `HEARTBEAT_INTERVAL` | `30` | Remote-agent heartbeat seconds |
| `AGENT_OFFLINE_AFTER` | `90` | Silence before an agent is `offline` |
| `TASK_TIMEOUT` | `1800` | Per-task Hermes timeout (s) |
| `MAX_CONCURRENT_TASKS` | `4` | Local Hermes parallel executions |
| `CONTEXT_TURNS` | `6` | Conversation turns remembered per (user, chat, agent) |
| `HERMES_BIN` | `hermes` | Hermes binary (path or name on PATH) |
| `LOW_PRIORITY_MODE` | `ignore` | `ignore` or `telegram` for low-priority notifications |
| `RATE_LIMIT_PER_MINUTE` | `20` | Per-user Telegram rate limit |
| `DB_PATH` | `data/gateway.db` | SQLite file |
| `LOG_LEVEL` | `INFO` | Python log level |

## Using Telegram

| Message | Effect |
|---|---|
| `Check GPU temperature` | routed to `DEFAULT_AGENT` (gx10) |
| `/gx10 summarize logs` or `@gx10 summarize logs` | routed to agent `gx10` |
| `/scar16 download X` / `@scar16 download X` | queued for remote agent `scar16` |
| `/status` | gateway + agent status |
| `/agents` | registered agents and online state |
| `/tasks` | recent tasks |
| `/cancel [id]` | cancel the running task (or the given id) |
| `/help` | command help |

Long tasks show a `⏳ Task running…` progress message which is edited in-place
to `✅ Task finished.` / `❌ Task failed.` and a `TASK COMPLETED` notification
with the result is sent to the chat.

## CLI

```bash
telegram-gateway health                 # GET /health
telegram-gateway agents                 # registered agents
telegram-gateway tasks [--limit N]      # recent tasks
telegram-gateway send "message" [--chat-id ID]   # raw message to your chat
telegram-gateway notify "text" [--agent gx10 --type warning --priority high]
telegram-gateway test                   # REAL end-to-end Telegram test message
telegram-gateway run                    # run the gateway (foreground)
telegram-gateway agent --gateway-url http://192.168.1.152:30100 --agent-id scar16
                                        # run the remote-agent runner
telegram-gateway vscode-proxy [--host H --port P]
                                        # OpenAI proxy mirroring VS Code <-> Qwen chats to Telegram
```

## VS Code ↔ Qwen Telegram mirror

Mirror your **VS Code Copilot Chat** conversations with the local Qwen model to
Telegram. An OpenAI-compatible passthrough proxy sits between VS Code and
`llama-server` and pushes each turn (your message + the model reply) to your
Telegram chat through the gateway admin API.

```
VS Code ─POST /v1/chat/completions─► proxy :30001 ─► llama-server :30000
                                       │
                                       └─ notify ─► gateway API ─► Telegram
```

```bash
# 1. start the proxy (systemd user service, enabled at boot)
systemctl --user enable --now vscode-telegram-mirror.service

# 2. point VS Code at the proxy instead of llama-server, in
#    ~/.config/Code/User/chatLanguageModels.json:
#      "url": "http://localhost:30001/v1/chat/completions"
#    then Reload Window.
```

Behaviour:
- **Fail-open**: if the gateway is down, chat keeps working; mirror errors are
  only logged (`journalctl --user -u vscode-telegram-mirror`).
- Only turns whose last message is from the user are mirrored, so agent-mode
  intermediate calls don't spam the chat.
- Internal helper calls (tiny `max_tokens`, e.g. title generation) and
  tool-call-only responses are skipped.
- Both streaming and non-streaming responses are captured; long replies are
  truncated to 3200 chars.

| Env var | Default | Purpose |
|---|---|---|
| `VSCODE_PROXY_UPSTREAM` | `http://localhost:30000` | llama-server (OpenAI) base URL |
| `VSCODE_PROXY_HOST` | `127.0.0.1` | proxy bind host |
| `VSCODE_PROXY_PORT` | `30001` | proxy bind port |
| `VSCODE_PROXY_SKIP_MAX_TOKENS` | `128` | skip requests with `max_tokens ≤` this (0 disables) |
| `VSCODE_PROXY_NOTIFY_PRIORITY` | `normal` | notification priority |

## Remote agents (SCAR16)

The gateway machine needs no per-remote config besides a token. On **SCAR16**:

```bash
# 1. copy the project (git clone / rsync), then
./scripts/install.sh
# 2. in SCAR16 .env:  AGENT_TOKEN_SCAR16=<same secret as on the GX10>
# 3. start the runner (Hermes must be installed on SCAR16 too)
telegram-gateway agent --gateway-url http://192.168.1.152:30100 --agent-id scar16
```

The runner: registers (hostname/OS/model/capabilities) → heartbeats every
`HEARTBEAT_INTERVAL`s → polls `GET /agent/tasks` → executes `hermes -z … --yolo`
locally → reports results and progress notifications to the gateway. Cancellations
propagate: heartbeat responses include cancelled ids and the runner kills the
local Hermes process.

## HTTP API

Public:

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Gateway status, Telegram connection state, per-agent online map |

Agent endpoints — require headers `X-Agent-Id: <id>` and `X-Agent-Token: <secret>`:

| Method | Path | Description |
|---|---|---|
| `POST` | `/agent/register` | `{hostname, os, model, capabilities, version}` |
| `POST` | `/agent/heartbeat` | liveness; returns `{cancelled_tasks: [...]}` |
| `GET` | `  /agent/tasks` | claims queued tasks for this agent |
| `POST` | `/agent/task/result` | `{task_id, status, result, error}` |
| `POST` | `/agent/notification` | `{type, message, priority}` — typed agent→owner notifications |

Admin endpoints — require `X-Admin-Token: $GATEWAY_ADMIN_TOKEN` (or localhost when
no token is configured):

| Method | Path | Description |
|---|---|---|
| `GET` | `/agents` | list agents with status |
| `POST` | `/telegram/notify` | `{agent_id, type, message, priority, chat_id}` |
| `POST` | `/telegram/message` | `{chat_id, text}` raw message |
| `POST` | `/task` | `{agent_id, prompt, chat_id, user_id}` create async task |
| `GET` | `/tasks` | recent tasks (`?agent=&status=&limit=`) |
| `GET` | `/task/{id}` | task details + result |
| `POST` | `/task/{id}/cancel` | cancel queued/running task |

## Hermes integration

The gateway shells out to the Hermes CLI in one-shot mode:

```bash
hermes -z "<prompt>" --yolo
```

`-z/--oneshot` prints **only** the final assistant response to stdout — verified
against Hermes v0.20.6 on the GX10. No Hermes source or configuration is modified
by this project; the only requirement is that `HERMES_BIN` is executable by the
service user and already configured (model, API endpoint, keys) on its machine.

## systemd (GX10, user service)

```bash
mkdir -p ~/.config/systemd/user
cp systemd/telegram-gateway.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now telegram-gateway
journalctl --user -u telegram-gateway -f
```

## Development

```bash
./scripts/install.sh
./scripts/test.sh              # pytest -v
```

Layout: `src/telegram_gateway/{config,database,models,security,telegram,
notifications,agents,sessions,tasks,api,gateway,cli,agent_runner}.py`,
tests in `tests/`.

## Security notes

- The bot token and agent secrets are **never** logged or echoed; all error text
  passing through the gateway is redacted against known secrets.
- Inbound Telegram messages from users outside `TELEGRAM_ALLOWED_USER_IDS`
  (and chats outside `TELEGRAM_ALLOWED_CHAT_IDS`) are rejected with a warning log.
- Agent REST endpoints require the per-agent shared token; the gateway refuses
  to start without a bot token **and** an allowlist.
- Bind `GATEWAY_HOST` to the LAN interface only; combine with the agent token
  if remote machines must reach the API. `GATEWAY_ADMIN_TOKEN` is mandatory
  before exposing admin endpoints beyond localhost.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `telegram-gateway test` → `getMe failed` | wrong/revoked `TELEGRAM_BOT_TOKEN` |
| No reply in Telegram | your user/chat ID missing from the allowlists (check `journalctl`) |
| Agent always offline | heartbeat not reaching gateway — check `GATEWAY_HOST` firewall/port |
| Task stuck `running` after reboot | gateway marks stale tasks `failed` at startup automatically |
| `409 Conflict` | another poller/webhook is active — `telegram-gateway` deletes the webhook on start; ensure only one instance runs |
