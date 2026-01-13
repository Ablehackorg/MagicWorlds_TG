# db_notify.py — версия с логами и автопереподключением
import os
import asyncio
import asyncpg
import logging

log = logging.getLogger(__name__)

def _dsn_from_env() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    user = os.getenv("DB_USER", "botuser")
    pwd  = os.getenv("DB_PASS", "botpass")
    host = os.getenv("DB_HOST", "postgres")
    port = os.getenv("DB_PORT", "5432")
    db   = os.getenv("DB_NAME", "bot_manager")
    return f"postgres://{user}:{pwd}@{host}:{port}/{db}"

async def listen_tasks_changed(event: asyncio.Event, channel: str = "tasks_changed"):
    """
    Слушает pg_notify(channel) и ставит event при каждом уведомлении.
    Логирует payload и автоматически переподключается при обрыве.
    """
    dsn = _dsn_from_env()
    while True:
        conn = None
        try:
            conn = await asyncpg.connect(dsn)
            log.info(f"LISTEN {channel} connected")

            async def _on_notify(*args):
                # args = (connection, pid, channel, payload)
                payload = args[3] if len(args) > 3 else None
                log.info(f"🔔 NOTIFY {channel}: {payload}")
                event.set()

            await conn.add_listener(channel, _on_notify)

            # держим соединение живым
            while True:
                await asyncio.sleep(3600)

        except Exception as e:
            log.warning(f"LISTEN {channel} failed: {e}; reconnect in 2s")
            await asyncio.sleep(2)
        finally:
            if conn:
                try:
                    await conn.close()
                except Exception:
                    pass
