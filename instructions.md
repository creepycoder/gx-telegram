# GX Telegram Gateway — Project Specification

## OBJECTIVE

Create a production-ready, bidirectional Telegram gateway that allows the existing Telegram bot `inferont` to act as a remote interface for Hermes Agent installations running on:

- ASUS Ascent GX10
- ASUS ROG Strix SCAR 16

Hermes is already installed on both machines.

The system must support both directions:

Telegram → Hermes
Telegram ← Hermes

Therefore:

1. I must be able to send natural-language prompts from Telegram to Hermes.
2. Hermes must be able to send asynchronous notifications to Telegram.
3. The system must support both the GX10 and SCAR16.
4. The GX10 must act as the central gateway.
5. The SCAR16 must connect to the GX10 gateway as a remote agent.

---

# CRITICAL PROJECT LOCATION

You are running directly on the ASUS Ascent GX10.

The project MUST be created on this machine.

The projects directory is:

/home/ale/projects

The project MUST be created EXACTLY here:

/home/ale/projects/gx-telegram

The final project root MUST be:

/home/ale/projects/gx-telegram

Before starting, verify:

```bash
pwd
ls -la /home/ale/projects

Then create and enter:

mkdir -p /home/ale/projects/gx-telegram
cd /home/ale/projects/gx-telegram

All source code, configuration templates, tests, scripts and documentation must remain inside this project directory.

Do NOT create the project in:

/tmp
/opt
/root
/home/ale
another directory
another machine

Do NOT modify unrelated projects under /home/ale/projects.

Do NOT modify Hermes Agent during the first implementation phase.

The GX10 is the development host and central gateway host.

ARCHITECTURE

The intended architecture is:

                     TELEGRAM
                        │
                        ▼
                 ┌─────────────┐
                 │   inferont   │
                 │ Telegram Bot │
                 └──────┬──────┘
                        │
                     HTTPS
                        │
                        ▼
          ┌──────────────────────────┐
          │       GX10 Gateway       │
          │                          │
          │ Telegram integration     │
          │ Authentication           │
          │ Agent routing            │
          │ Sessions                 │
          │ Task management          │
          │ Notifications            │
          │ Agent management         │
          └────────────┬─────────────┘
                       │
                LAN / authenticated
                       │
          ┌────────────┴────────────┐
          │                         │
          ▼                         ▼
   ┌─────────────┐           ┌─────────────┐
   │    GX10     │           │   SCAR 16   │
   │             │           │             │
   │   Hermes    │           │   Hermes    │
   │     │       │           │     │       │
   │  Qwen3.8   │           │  Local LLM  │
   │     │       │           │     │       │
   │   tools     │           │   tools     │
   └─────────────┘           └─────────────┘

The GX10 is the central gateway.

The SCAR16 is a remote Hermes agent.

TELEGRAM BOT

Use the existing Telegram bot:

inferont

Do NOT create another Telegram bot.

Do NOT require creation of a new bot.

Use the Telegram Bot API over HTTPS.

GATEWAY RESPONSIBILITIES

The gateway must provide:

Telegram inbound message handling
Telegram outbound notifications
Telegram authentication
Telegram user/chat allowlist
agent registration
agent heartbeat
agent status
agent routing
conversation/session management
task management
asynchronous task handling
notification management
retries
rate limiting
idempotency
logging
health checks
persistent task/session state

The gateway must be independent from Hermes.

Do NOT modify Hermes Agent in the first phase.

MULTI-AGENT ARCHITECTURE

The gateway must be designed around generic agents.

Today the agents are:

gx10
scar16

Future agents could be:

server
NAS
Raspberry Pi
cloud VM
other machines

Adding an agent should require configuration rather than modifying the gateway core.

Do NOT hardcode GX10 and SCAR16 into the core routing logic.

GX10 AGENT

Register the GX10 as:

agent_id: gx10

Detect the hostname automatically where possible.

The GX10 agent should expose information such as:

{
  "agent_id": "gx10",
  "hostname": "gx10-8532",
  "status": "online",
  "model": "Qwen3.8",
  "capabilities": [
    "shell",
    "filesystem",
    "llm",
    "gpu"
  ]
}

Do not hardcode the hostname if it can be detected automatically.

SCAR16 AGENT

The SCAR16 must eventually register as:

agent_id: scar16

The SCAR16 communicates with the gateway running on the GX10.

Telegram must NOT communicate directly with the SCAR16.

The GX10 gateway remains the central point.

TELEGRAM → HERMES

The user must be able to send natural-language prompts through Telegram.

Example:

Controlla se ci sono aggiornamenti di llama.cpp e dimmi se conviene aggiornare il GX10.

The gateway routes the prompt to the appropriate Hermes agent.

EXPLICIT AGENT SELECTION

Support:

/gx10 <prompt>

/scar16 <prompt>

Examples:

/gx10 controlla lo stato di Qwen3.8

/scar16 controlla la GPU

Also support:

@gx10 <prompt>

@scar16 <prompt>

DEFAULT AGENT

Support a configurable default agent.

Example:

DEFAULT_AGENT=gx10

Then:

Controlla Qwen3.8

is automatically routed to GX10.

FUTURE INTELLIGENT ROUTING

Design the architecture so intelligent routing can be added later.

Examples:

"Controlla Qwen3.8"
→ GX10

"Controlla la GPU del portatile"
→ SCAR16

Do not introduce unnecessary AI routing complexity in the first version.

The first implementation can use explicit routing and a configurable default agent.

TELEGRAM COMMANDS

Implement at least:

/help
/status
/agents
/tasks
/cancel
/gx10
/scar16

/status

Example:

GX10: 🟢 online
SCAR16: 🟢 online

GX10 model: Qwen3.8

/agents

Example:

Available agents:

🟢 gx10
Qwen3.8

🟢 scar16
Local model

/tasks

Display active/recent tasks.

/cancel

Allow cancellation of a running task when technically possible.

PERSISTENT CONVERSATIONS

Support conversation/session context.

Example:

USER:
Controlla Qwen3.8.

inferont:
Cosa vuoi controllare?

USER:
Le performance.

inferont:
Ho analizzato...

The gateway must be able to associate:

Telegram user
Telegram chat
agent
conversation
task

GX10 and SCAR16 conversations should be logically separated.

OUTBOUND NOTIFICATIONS

Outbound notifications are a first-class feature.

Hermes and the agents must be able to send notifications to Telegram even when the user has not previously sent a Telegram message.

Example:

GX10
↓
Hermes
↓
Gateway
↓
Telegram Bot API
↓
inferont
↓
Telegram

And:

SCAR16
↓
Hermes
↓
Gateway
↓
Telegram Bot API
↓
inferont

NOTIFICATION TYPES

Support at least:

success
info
warning
error
progress
task_started
task_completed
task_failed
scheduled_task
startup
shutdown
NOTIFICATION EXAMPLES
Task completed

🤖 inferont

[GX10] ✅ TASK COMPLETED

Task:
llama.cpp update check

Result:
No relevant updates found.

Duration:
3m 42s

Task failed

🤖 inferont

[SCAR16] ❌ TASK FAILED

Task:
Model benchmark

Error:
Connection timeout

Scheduled task

🤖 inferont

[GX10] ⏰ SCHEDULED TASK

Qwen3.8 update check completed.

Result:
New relevant changes detected.

Warning

🤖 inferont

[GX10] ⚠️ WARNING

GPU memory usage:
118 GB / 128 GB

Startup

🤖 inferont

[GX10] 🟢 ONLINE

Hermes Telegram agent started.

Model:
Qwen3.8

NOTIFICATION PRIORITY

Support:

low
normal
high
critical

Make notification behavior configurable.

Future configuration should allow:

low → ignore
normal → Telegram
high → Telegram
critical → Telegram immediately

PROGRESS NOTIFICATIONS

Long-running tasks should support progress updates.

Example:

🚀 [GX10] Task started

Downloading model...

Progress:
42%

Prefer editing/updating the existing Telegram message where practical instead of generating hundreds of separate messages.

ASYNCHRONOUS TASKS

A Telegram prompt must be able to start a long-running task without blocking Telegram interaction.

Example:

USER:

/gx10 esegui il benchmark di Qwen3.8

Immediate response:

🚀 Task started

Agent: GX10
Task ID: gx10-20260906-00142

Later:

⏳ Task running...

Finally:

✅ Task completed

TASK MODEL

Every task must have a unique ID.

At minimum:

task_id
agent_id
telegram_user_id
telegram_chat_id
prompt
status
created_at
started_at
completed_at
result
error

Possible statuses:

queued
running
completed
failed
cancelled
AGENT API

Implement authenticated agent communication.

At minimum:

POST /agent/register
POST /agent/heartbeat
POST /agent/task/result
POST /agent/notification
GET /agent/tasks

The exact API may be improved if there is a technically superior design.

GATEWAY API

Implement at least:

GET /health

GET /agents

POST /telegram/notify
POST /telegram/message

POST /task
GET /tasks
GET /task/{id}
POST /task/{id}/cancel

Document the API.

HEALTH ENDPOINT

Implement:

GET /health

Example response:

{
  "status": "ok",
  "telegram": "ok",
  "agents": {
    "gx10": "online",
    "scar16": "online"
  }
}

Never expose secrets.

SECURITY

This is critical.

The Telegram bot must NOT accept commands from arbitrary Telegram users.

Use an allowlist.

Configuration:

TELEGRAM_ALLOWED_USER_IDS=
TELEGRAM_ALLOWED_CHAT_IDS=

Unauthorized users must not be able to:

execute prompts
start tasks
inspect agents
inspect system information
access task results

Do not expose system details to unauthorized users.

SECRETS

Never hardcode:

TELEGRAM_BOT_TOKEN
API keys
agent credentials
passwords
authentication tokens

Secrets must be loaded from environment variables or protected configuration files.

Never expose secrets in:

source code
Git
logs
Telegram messages
API responses
exception messages
NETWORK SECURITY

Do NOT expose Hermes or llama-server directly to the Internet.

The Telegram gateway communicates with Telegram over HTTPS.

The SCAR16 communicates with the GX10 gateway over the LAN.

The gateway may listen on the GX10 LAN interface so that the SCAR16 can reach it.

Protect agent-to-gateway communication with authentication.

Do NOT configure Internet port forwarding.

TELEGRAM TRANSPORT

Prefer Telegram long polling for the first implementation because it does not require a public inbound port.

Use webhooks only if there is a strong technical reason.

The architecture must not require a public IP address.

PERSISTENCE

Use SQLite for the first version.

Do NOT introduce PostgreSQL or Redis unless there is a strong technical reason.

Persist at least:

agents
conversations
tasks
notifications
telegram_users
IDEMPOTENCY

Prevent duplicate Telegram messages when retrying requests.

Support:

notification_id

or an equivalent idempotency mechanism.

RETRY HANDLING

Handle transient failures such as:

network failure
timeout
Telegram HTTP 429
Telegram HTTP 5xx

Use exponential backoff.

Do not retry forever.

Respect Telegram rate limits.

RATE LIMITING

Protect against:

Telegram message floods
duplicate tasks
repeated requests
notification loops

Rate limits must be configurable.

AGENT HEARTBEAT

Agents must periodically send heartbeats.

The gateway should track:

last_seen
status

Possible states:

online
offline
unknown

An agent that stops sending heartbeats should eventually be marked offline.

AGENT REGISTRATION

When Hermes/agent starts:

SCAR16 → Gateway
GX10 → Gateway

Each agent registers itself.

The gateway should know:

agent_id
hostname
OS
model
capabilities
version
last_seen
status

Do not require manual editing of gateway source code to add agents.

CLI

Provide a CLI for administration and diagnostics.

Examples:

telegram-gateway health
telegram-gateway agents
telegram-gateway test
telegram-gateway send "Test message"
telegram-gateway tasks

TEST TELEGRAM MESSAGE

Provide:

telegram-gateway test

This must send a REAL Telegram message through:

GX10 → Telegram API → inferont → Telegram client

Example:

🟢 GX Telegram Gateway

Telegram integration test successful.

Host:
GX10

Status:
READY

SYSTEMD

Create:

systemd/telegram-gateway.service

The service must:

start automatically
restart after failure
use a non-root user where possible
have appropriate environment/configuration handling
produce useful logs

Document installation and activation.

INSTALLATION SCRIPTS

Create:

scripts/install.sh
scripts/test.sh
scripts/send_test.sh

The scripts should be safe to run multiple times where practical.

CONFIGURATION

Create:

.env.example

At minimum:

TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_IDS=
TELEGRAM_ALLOWED_CHAT_IDS=

DEFAULT_AGENT=gx10

GATEWAY_HOST=0.0.0.0
GATEWAY_PORT=30100

LOG_LEVEL=INFO

Use the actual appropriate bind address after considering the GX10 ↔ SCAR16 LAN requirement.

Do not blindly bind to 0.0.0.0 if a more secure interface-specific configuration is possible.

PROJECT STRUCTURE

The project MUST be located at:

/home/ale/projects/gx-telegram

A suitable structure is:

gx-telegram/
├── README.md
├── .env.example
├── .gitignore
├── pyproject.toml
│
├── src/
│ └── telegram_gateway/
│ ├── init.py
│ ├── config.py
│ ├── database.py
│ ├── models.py
│ ├── telegram.py
│ ├── gateway.py
│ ├── agents.py
│ ├── tasks.py
│ ├── notifications.py
│ ├── sessions.py
│ ├── security.py
│ ├── api.py
│ └── cli.py
│
├── scripts/
│ ├── install.sh
│ ├── test.sh
│ └── send_test.sh
│
├── systemd/
│ └── telegram-gateway.service
│
└── tests/
├── test_telegram.py
├── test_agents.py
├── test_tasks.py
├── test_notifications.py
└── test_security.py

The exact structure may be changed if there is a technically superior design.

Keep the implementation simple.

TECHNOLOGY

Prefer:

Python 3.11+

Use the minimum number of dependencies required.

Do not use Docker unless there is a strong technical reason.

Do not introduce unnecessary microservices.

Prefer one gateway process.

HERMES INTEGRATION

Do NOT modify Hermes Agent in the first implementation phase.

The gateway must provide a clean integration interface for Hermes.

The integration must support:

send notification
receive task
report task started
report task progress
report task completed
report task failed

After the gateway itself is working, the Hermes installations can be connected:

GX10 Hermes → GX10 Gateway
SCAR16 Hermes → GX10 Gateway

INBOUND FLOW

Example:

Telegram
│
│ "Controlla lo stato di Qwen3.8"
▼
inferont
│
▼
GX10 Telegram Gateway
│
├── authenticate
├── identify user
├── identify conversation
├── identify agent
└── create task
│
▼
GX10
│
▼
Hermes
│
▼
Qwen3.8
│
▼
result
│
▼
Gateway
│
▼
inferont
│
▼
Telegram

OUTBOUND FLOW

Example:

GX10
│
▼
Hermes
│
│ scheduled task completed
▼
Telegram Gateway
│
▼
Telegram Bot API
│
▼
inferont
│
▼
Telegram
│
▼
USER

This must work even if the user has never sent a Telegram message beforehand.

MULTI-AGENT NOTIFICATION FORMAT

Every notification must identify its source agent.

Examples:

[GX10] ✅

[SCAR16] ⚠️

The user must immediately know which machine generated the notification.

FINAL USER EXPERIENCE

Telegram should behave like a remote console for the two Hermes installations.

Examples:

/gx10 controlla Qwen3.8

/scar16 controlla la GPU

Controlla se ci sono aggiornamenti interessanti per llama.cpp.

And automatically:

[GX10] ✅ Task completed

[GX10] ⏰ Scheduled task completed

[GX10] ⚠️ Warning

[SCAR16] ❌ Task failed

[SCAR16] 🟢 Online

FUTURE EXTENSIBILITY

Design the architecture so future support can be added for:

additional computers
additional Hermes agents
multiple models per agent
task scheduling
richer Telegram commands
file transfer
logs
benchmark results
screenshots
voice messages
Telegram buttons
approval workflows
human confirmation before dangerous operations

Do not implement these unless necessary for the initial version.

Design interfaces so they can be added later.

COMPLETION CRITERIA

The implementation is complete only when all of the following have been demonstrated.

Test 1 — Telegram outbound

GX10 → Telegram

A real Telegram test message is received through inferont.

Test 2 — Telegram inbound to GX10

Telegram → GX10 → Hermes

A Telegram prompt reaches the GX10 Hermes installation.

Test 3 — Telegram inbound to SCAR16

Telegram → GX10 Gateway → SCAR16 Hermes

A Telegram prompt reaches the SCAR16 Hermes installation.

Test 4 — Asynchronous task

A long-running task produces:

started
progress
completed

notifications.

Test 5 — Error

A failed task produces:

❌ TASK FAILED

Test 6 — Scheduled task

A scheduled task can produce a Telegram notification without any preceding Telegram interaction.

Test 7 — Authentication

An unauthorized Telegram user cannot execute commands.

Test 8 — Agent heartbeat

GX10 and SCAR16 appear correctly in:

/agents

and:

/status

Test 9 — Gateway restart

After restarting the gateway:

gateway starts
agents reconnect
Telegram integration works

Test 10 — No Internet exposure

Verify that Hermes and llama-server are not directly exposed to the Internet.

FINAL IMPLEMENTATION INSTRUCTIONS

Do not merely describe the implementation.

Actually create the complete project on the GX10 filesystem.

The project MUST be located at:

/home/ale/projects/gx-telegram

At the end:

Show the complete project tree.
Create all source files.
Create all configuration templates.
Create all tests.
Create the systemd service.
Create the installation scripts.
Install required dependencies.
Run the complete test suite.
Run the health check.
Run a real Telegram outbound test.
Verify the Telegram inbound architecture.
Document the SCAR16 agent integration.
Document the Hermes integration.
Document exactly which configuration values must be supplied manually.
Do not expose or print any secret values.
Do not modify Hermes Agent itself during this initial implementation.
Do not modify unrelated projects.
Do not create files outside /home/ale/projects/gx-telegram unless strictly required by the operating-system service installation process.

The final result must be a working, maintainable and secure multi-agent Telegram Gateway with:

                inferont
                   ↕
           Telegram Gateway
                   ↕
         ┌─────────┴─────────┐
         │                   │
      GX10 Hermes       SCAR16 Hermes
         │
      Qwen3.8

The GX10 is the central gateway host.

IMPORTANT:
Do not stop after generating the code.
Actually implement, test and validate the project on this GX10.