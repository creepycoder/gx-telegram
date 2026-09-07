"""Main entrypoint: wire components and run gateway + HTTP API."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from .agents import AgentManager
from .api import ApiServer
from .config import Config, load_dotenv
from .database import Database
from .gateway import Gateway
from .notifications import NotificationManager
from .sessions import SessionManager
from .tasks import TaskManager
from .telegram import TelegramClient, TelegramError

log = logging.getLogger("tg.main")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )


async def run(config: Config) -> None:
    db = Database(config.db_path)
    telegram = TelegramClient(config)
    agents = AgentManager(config, db)
    sessions = SessionManager(config, db)
    notifier = NotificationManager(config, db, telegram)
    tasks = TaskManager(config, db, agents, sessions, notifier, telegram)
    gateway = Gateway(config, db, telegram, agents, sessions, notifier, tasks)
    api = ApiServer(config, db, agents, tasks, notifier, telegram)

    await api.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    poll_task = asyncio.create_task(gateway.run_forever())
    stop_task = asyncio.create_task(stop.wait())
    done, _pending = await asyncio.wait({poll_task, stop_task},
                                        return_when=asyncio.FIRST_COMPLETED)

    log.info("Shutting down…")
    poll_failed = poll_task in done
    stop_task.cancel()
    await gateway.stop()
    await api.stop()
    await telegram.close()
    db.close()
    if poll_failed:
        # surface startup/poll failures (bad token, network, …) to the caller
        poll_task.result()


def main() -> None:
    load_dotenv()
    config = Config.from_env()
    setup_logging(config.log_level)
    if not config.bot_token:
        log.error("TELEGRAM_BOT_TOKEN is not set — see .env.example")
        sys.exit(1)
    if not config.allowed_user_ids and not config.allowed_chat_ids:
        log.error("No TELEGRAM_ALLOWED_USER_IDS / TELEGRAM_ALLOWED_CHAT_IDS configured — refusing to run open to the world")
        sys.exit(1)
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        pass
    except TelegramError as exc:
        log.error("Telegram error: %s (check TELEGRAM_BOT_TOKEN / connectivity)", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
