import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from bot import texts
from bot.keyboards.inline import lead_buttons, pages

logger = logging.getLogger(__name__)


async def show_leads(message, db, page=0):
    rows = await db.fetch('SELECT * FROM applications ORDER BY id DESC LIMIT 11 OFFSET ?', (page * 10,))
    if not rows:
        await message.answer(texts.NO_LEADS)
        return
    for row in rows[:10]:
        await message.answer(texts.lead(row, updated=row['version'] > 1), reply_markup=lead_buttons(row))
    markup = pages(page, len(rows) > 10)
    if markup:
        await message.answer(texts.page_number(page + 1), reply_markup=markup)


async def admin_command(message: Message, config, db, bot):
    if message.from_user.id != config.admin_id:
        await message.answer(texts.DENIED)
        return
    command = message.text.split()[0].split('@')[0]
    if command == '/leads':
        await show_leads(message, db)
    elif command == '/stats':
        await message.answer(texts.stats(await db.statistics()))
    else:
        parts = message.text.split(maxsplit=2)
        if len(parts) != 3 or not parts[1].isascii() or not parts[1].isdigit() or not 1 <= len(parts[2].strip()) <= 4000:
            await message.answer(texts.REPLY_USAGE)
            return
        # Reject oversized IDs without overflowing SQLite's INTEGER.
        recipient = int(parts[1]) if len(parts[1]) <= 18 else 0
        rows = await db.fetch('SELECT chat_id FROM users WHERE id=?', (recipient,))
        if not rows:
            await message.answer(texts.UNKNOWN_USER)
            return
        try:
            await bot.send_message(rows[0]['chat_id'], parts[2].strip(), parse_mode=None)
        except TelegramForbiddenError:
            await message.answer(texts.REPLY_BLOCKED)
        except TelegramAPIError:
            await message.answer(texts.REPLY_FAILED)
        else:
            await message.answer(texts.REPLY_OK)


async def contact(callback: CallbackQuery, config, db):
    if callback.from_user.id != config.admin_id:
        await callback.answer(texts.DENIED, show_alert=True)
        return
    parts = callback.data.split(':')
    if len(parts) != 3 or not all(p.isdigit() and len(p) <= 18 for p in parts[1:]):
        await callback.answer(texts.ERROR, show_alert=True)
        return
    row = await db.contacted(int(parts[1]), int(parts[2]))
    if row is None:
        await callback.answer(texts.STALE_LEAD, show_alert=True)
        return
    try:
        await callback.message.edit_text(
            texts.lead(row, updated=row['version'] > 1), reply_markup=lead_buttons(row),
        )
    except TelegramAPIError as exc:
        # Re-delivered callbacks can edit an already updated card; that is OK.
        if not (isinstance(exc, TelegramBadRequest) and 'message is not modified' in exc.message.lower()):
            logger.warning('Lead %s card edit failed: %s', row['id'], type(exc).__name__)
            await callback.answer(texts.CONTACTED_REFRESH_FAILED, show_alert=True)
            return
    await callback.answer(texts.CONTACTED)


async def pagination(callback: CallbackQuery, config, db):
    if callback.from_user.id != config.admin_id:
        await callback.answer(texts.DENIED, show_alert=True)
        return
    page = callback.data.removeprefix('leads:')
    if not page.isdigit() or len(page) > 6:
        await callback.answer(texts.ERROR)
        return
    await callback.answer()
    await show_leads(callback.message, db, int(page))


def router():
    route = Router()
    route.message(Command('leads', 'stats', 'reply'))(admin_command)
    route.callback_query(F.data.startswith('contact:'))(contact)
    route.callback_query(F.data.startswith('leads:'))(pagination)
    return route
