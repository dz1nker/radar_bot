import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from aiogram.exceptions import TelegramRetryAfter
from bot import texts
from bot.database.db import now
from bot.keyboards.inline import lead_buttons

logger = logging.getLogger(__name__)


async def deliver_pending(bot, db, admin_id):
    rows = await db.fetch('SELECT * FROM outbox WHERE sent_at IS NULL AND next_attempt<=? ORDER BY id LIMIT 20', (now(),))
    for event in rows:
        row = json.loads(event['payload'])
        try:
            await bot.send_message(admin_id, texts.lead(row, updated=row['version'] > 1),
                                   reply_markup=lead_buttons(row))
        except Exception as exc:
            attempts = event['attempts'] + 1
            delay = min(3600, 5 * 2 ** min(attempts - 1, 10))
            if isinstance(exc, TelegramRetryAfter):
                delay = max(delay, exc.retry_after)
            retry = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
            # Store exception class only: remote error strings can contain secrets.
            await db.execute('UPDATE outbox SET attempts=?,next_attempt=?,last_error=? WHERE id=?',
                             (attempts, retry, type(exc).__name__, event['id']))
            logger.warning('Notification %s failed (%s); retry in %ss', event['id'], type(exc).__name__, delay)
        else:
            async with db.transaction() as conn:
                timestamp = now()
                await conn.execute('UPDATE outbox SET sent_at=?,last_error=NULL WHERE id=?', (timestamp, event['id']))
                await conn.execute('UPDATE applications SET admin_notified_at=? WHERE id=? AND version=?',
                                   (timestamp, row['id'], row['version']))


async def notification_worker(bot, db, admin_id):
    while True:
        try:
            await deliver_pending(bot, db, admin_id)
        except Exception as exc:
            logger.error('Notification worker error: %s', type(exc).__name__)
        await asyncio.sleep(2)
