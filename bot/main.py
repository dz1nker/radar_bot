import asyncio
import logging
import sys
from contextlib import suppress

from aiogram import BaseMiddleware, Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import ErrorEvent, Message

from bot import texts
from bot.config import Config, RedactToken
from bot.database.db import Database, SQLiteStorage
from bot.handlers import admin, application, start
from bot.services.notifications import notification_worker
from bot.services.telegram import EnvironmentSession

logger = logging.getLogger(__name__)


class PrivateUsers(BaseMiddleware):
    async def __call__(self, handler, event, data):
        message = event if isinstance(event, Message) else event.message
        if message is None or message.chat.type != 'private':
            if hasattr(event, 'answer'):
                await event.answer(texts.PRIVATE_ONLY)
            return
        await data['db'].remember(event.from_user.id, message.chat.id)
        return await handler(event, data)


def dispatcher(db, config):
    dp = Dispatcher(storage=SQLiteStorage(db), events_isolation=SimpleEventIsolation(), db=db, config=config)
    dp.message.outer_middleware(PrivateUsers())
    dp.callback_query.outer_middleware(PrivateUsers())
    # Router instances are built per dispatcher (also allows restart tests).
    for module in (start, admin, application):
        dp.include_router(module.router())
    fallback = Router()

    @fallback.message()
    async def unknown(message: Message):
        await message.answer(texts.UNKNOWN)

    dp.include_router(fallback)

    @dp.errors()
    async def errors(event: ErrorEvent, bot):
        logger.error('Update %s failed: %s', event.update.update_id, type(event.exception).__name__)
        message = event.update.message
        if message and message.chat.type == 'private':
            with suppress(Exception):
                await bot.send_message(message.chat.id, texts.ERROR)
        return True

    return dp


async def run(config):
    db = Database(config.database_path)
    try:
        await db.open()
    except Exception as exc:
        with suppress(Exception):
            await db.close()
        raise RuntimeError(f'Не удалось открыть SQLite: {type(exc).__name__}. Проверь DATABASE_PATH и права записи.') from None
    bot = Bot(config.token, session=EnvironmentSession(), default=DefaultBotProperties(parse_mode='HTML'))
    worker = None
    try:
        identity = await bot.get_me()
        logger.info('Bot @%s authenticated; SQLite ready', identity.username)
        # Preserve queued updates, including unfinished applications.
        await bot.delete_webhook(drop_pending_updates=False)
        worker = asyncio.create_task(notification_worker(bot, db, config.admin_id))
        await dispatcher(db, config).start_polling(bot, close_bot_session=False, handle_as_tasks=False)
    finally:
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        await bot.session.close()
        await db.close()


def main():
    try:
        config = Config.load()
    except ValueError as exc:
        print(f'Ошибка настройки: {exc}', file=sys.stderr)
        return 1
    logging.basicConfig(level=config.log_level, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactToken(config.token))
    try:
        asyncio.run(run(config))
    except TelegramUnauthorizedError:
        logger.error('Telegram отклонил BOT_TOKEN. Проверь токен в BotFather.')
        return 1
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logger.error('Запуск остановлен: %s. Проверь SQLite, сеть и настройки.', type(exc).__name__)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
