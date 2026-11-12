import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from aiogram.types import Message, Update

from bot import texts
from bot.config import Config
from bot.database.db import Database
from bot.main import dispatcher
from bot.services.notifications import deliver_pending


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.fail_chat = None

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            if method.chat_id == self.fail_chat:
                raise TelegramForbiddenError(method=method, message='blocked')
            return Message(message_id=len(self.calls), date=datetime.now(timezone.utc),
                           chat={'id': int(method.chat_id), 'type': 'private'}, text=method.text)
        return True

    async def stream_content(self, *args, **kwargs):
        yield b''


class Harness:
    def __init__(self, db):
        self.db = db
        self.config = Config('123456:TEST_FAKE_TOKEN', 99)
        self.session = FakeSession()
        self.bot = Bot(self.config.token, session=self.session, default=DefaultBotProperties(parse_mode='HTML'))
        self.dp = dispatcher(db, self.config)
        self.number = 100

    async def send(self, text=None, user=7, callback=None, number=None, username='ivan_test', **content):
        self.number += 1
        number = number or self.number
        person = {'id': user, 'is_bot': False, 'first_name': 'Иван <И>', 'username': username}
        message = {'message_id': number, 'date': 1700000000, 'chat': {'id': user, 'type': 'private'},
                   'from': person, **content}
        if text is not None:
            message['text'] = text
        if callback:
            raw = {'update_id': number, 'callback_query': {'id': str(number), 'from': person,
                   'chat_instance': 'test', 'data': callback, 'message': message}}
        else:
            raw = {'update_id': number, 'message': message}
        await self.dp.feed_update(self.bot, Update.model_validate(raw))

    def messages(self, user=None):
        return [c for c in self.session.calls if isinstance(c, SendMessage) and (user is None or c.chat_id == user)]

    async def apply(self, user=7, username='ivan_test'):
        await self.send('/start', user=user, username=username)
        await self.send(callback='apply:new', user=user, username=username)
        await self.send('  Консультант <b>  ', user=user, username=username)
        await self.send('Предприниматели & эксперты', user=user, username=username)


@pytest_asyncio.fixture
async def h(tmp_path, monkeypatch):
    monkeypatch.setattr('bot.handlers.start.asyncio.sleep', AsyncMock())
    db = Database(str(tmp_path / 'bot.db'))
    await db.open()
    harness = Harness(db)
    yield harness
    await harness.db.close()


async def test_presentation_and_complete_application(h):
    await h.send('/start')
    assert [m.text for m in h.messages()] == list(texts.INTRO)
    assert h.messages()[-1].reply_markup.inline_keyboard[0][0].callback_data == 'apply:new'
    from bot.handlers.start import asyncio as intro_asyncio
    assert intro_asyncio.sleep.await_count == 2
    assert all(call.args == (0.6,) for call in intro_asyncio.sleep.await_args_list)
    await h.send(callback='apply:new')
    assert h.messages()[-2].text == texts.PRIVACY
    assert h.messages()[-1].text == texts.NICHE
    await h.send('  Маркетолог  ')
    assert h.messages()[-1].text == texts.AUDIENCE
    await h.send('  Эксперты  ')
    row = await h.db.application(7)
    assert (row['business_niche'], row['target_audience'], row['status']) == ('Маркетолог', 'Эксперты', 'NEW')
    assert h.messages()[-1].text == texts.SUCCESS
    assert len(await h.db.fetch('SELECT * FROM outbox')) == 1
    await deliver_pending(h.bot, h.db, 99)
    assert 'Заявка №1' in h.messages(99)[-1].text
    assert '&lt;И&gt;' in h.messages(99)[-1].text
    assert (await h.db.application(7))['admin_notified_at']
    await deliver_pending(h.bot, h.db, 99)
    assert len(h.messages(99)) == 1


async def test_repeated_start_and_explicit_edit(h):
    await h.apply()
    original = await h.db.application(7)
    await h.send('/start')
    assert h.messages()[-1].text == texts.EXISTING
    await h.send(callback='apply:new')
    assert h.messages()[-1].text == texts.EXISTING
    await h.send(callback='apply:edit')
    await h.send(callback='apply:edit')  # repeated callback resumes instead of resetting
    await h.send('Новая ниша')
    await h.send('Новая аудитория')
    row = await h.db.application(7)
    assert row['id'] == original['id'] and row['created_at'] == original['created_at']
    assert row['version'] == 2 and row['status'] == 'NEW'
    assert len(await h.db.fetch('SELECT * FROM history')) == 2
    await deliver_pending(h.bot, h.db, 99)
    assert 'обновил заявку' in h.messages(99)[-1].text


async def test_restart_recovers_state(h):
    await h.send(callback='apply:new')
    await h.send('Психолог')
    path = h.db.path
    await h.db.close()
    h.db = Database(path)
    await h.db.open()
    h.dp = dispatcher(h.db, h.config)
    await h.send('/start')
    assert h.messages()[-1].text == texts.AUDIENCE
    await h.send('Родители')
    assert (await h.db.application(7))['business_niche'] == 'Психолог'


@pytest.mark.parametrize('text,content', [(None, {'sticker': {'file_id':'f','file_unique_id':'u','type':'regular','width':1,'height':1,'is_animated':False,'is_video':False}}),
                                        ('   ', {}), ('x' * 1001, {}), ('/unknown', {})])
async def test_invalid_answers_preserve_first_answer(h, text, content):
    await h.send(callback='apply:new')
    await h.send(text, **content)
    assert h.messages()[-1].text == texts.INVALID
    await h.send('Дизайнер')
    await h.send(text, **content)
    assert h.messages()[-1].text == texts.INVALID
    await h.send('Малый бизнес')
    assert (await h.db.application(7))['business_niche'] == 'Дизайнер'


async def test_cancel(h):
    await h.send(callback='apply:new')
    await h.send('Ниша')
    await h.send('/cancel')
    await h.send('Аудитория')
    assert await h.db.application(7) is None
    await h.send('/start')
    assert h.messages()[-1].text == texts.INTRO[-1]


async def test_no_username_and_retry_after_restart(h):
    await h.apply(username=None)
    h.session.fail_chat = 99
    await deliver_pending(h.bot, h.db, 99)
    event = (await h.db.fetch('SELECT * FROM outbox'))[0]
    assert event['attempts'] == 1 and event['sent_at'] is None
    assert await h.db.application(7)
    h.session.fail_chat = None
    await h.db.execute("UPDATE outbox SET next_attempt='2000'")
    await deliver_pending(h.bot, h.db, 99)
    text = h.messages(99)[-1].text
    assert 'tg://user?id=7' in text and '@None' not in text
    assert (await h.db.fetch('SELECT * FROM outbox'))[0]['sent_at']


@pytest.mark.parametrize('command', ['/leads', '/stats', '/reply 7 Привет'])
async def test_admin_commands_are_protected(h, command):
    await h.send(command)
    assert h.messages()[-1].text == texts.DENIED
    await h.send(callback='contact:1:1')
    assert await h.db.application(7) is None
    assert h.session.calls[-1].text == texts.DENIED
    await h.send(callback='leads:0')
    assert h.session.calls[-1].text == texts.DENIED


async def test_admin_commands_status_and_reply(h):
    await h.apply()
    await h.send('/start', user=99)
    assert h.messages()[-1].text == texts.ADMIN
    await h.send('/stats', user=99)
    assert 'Всего заявок: 1' in h.messages()[-1].text
    await h.send('/leads', user=99)
    assert 'Консультант &lt;b&gt;' in h.messages()[-1].text
    await h.send(callback='contact:1:1', user=99)
    assert (await h.db.application(7))['status'] == 'CONTACTED'
    await h.send('/reply 7 Привет <test>', user=99)
    assert h.messages(7)[-1].text == 'Привет <test>' and h.messages(7)[-1].parse_mode is None
    assert h.messages(99)[-1].text == texts.REPLY_OK
    await h.send('/reply 999 Привет', user=99)
    assert h.messages()[-1].text == texts.UNKNOWN_USER
    h.session.fail_chat = 7
    await h.send('/reply 7 Привет', user=99)
    assert h.messages()[-1].text == texts.REPLY_BLOCKED
    await h.send('/reply неправильный формат', user=99)
    assert h.messages()[-1].text == texts.REPLY_USAGE


async def test_duplicate_answers_and_concurrent_users(h):
    await h.send(callback='apply:new')
    await h.send('Ниша', number=300)
    await h.send('Ниша', number=300)
    assert not await h.db.application(7)
    await h.send('Аудитория', number=301)
    await h.send('Аудитория', number=301)
    assert len(await h.db.fetch('SELECT * FROM applications')) == 1
    assert len(await h.db.fetch('SELECT * FROM outbox')) == 1
    await asyncio.gather(h.apply(user=8), h.apply(user=9))
    assert len(await h.db.fetch('SELECT * FROM applications')) == 3


async def test_id_details_and_stale_contact(h):
    await h.send('/id')
    assert '<code>7</code>' in h.messages()[-1].text
    await h.send(callback='details')
    assert h.messages()[-1].text == texts.DETAILS
    await h.apply()
    await h.send(callback='apply:edit')
    await h.send('Новая ниша')
    await h.send('Новая аудитория')
    await h.send(callback='contact:1:1', user=99)
    assert (await h.db.application(7))['status'] == 'NEW'


def test_config_validation(monkeypatch):
    monkeypatch.setattr('bot.config.load_dotenv', lambda: None)
    monkeypatch.delenv('BOT_TOKEN', raising=False)
    with pytest.raises(ValueError, match='BOT_TOKEN'):
        Config.load()
    monkeypatch.setenv('BOT_TOKEN', '123456:FAKE_TOKEN')
    monkeypatch.delenv('ADMIN_TELEGRAM_ID', raising=False)
    with pytest.raises(ValueError, match='ADMIN_TELEGRAM_ID'):
        Config.load()
    monkeypatch.setenv('ADMIN_TELEGRAM_ID', '99')
    assert Config.load().admin_id == 99


async def test_leads_pagination(h):
    for user in range(10, 22):
        await h.apply(user=user)
    await h.send('/leads', user=99)
    messages = h.messages(99)
    assert len(messages) == 11  # 10 leads, then navigation
    assert messages[-1].reply_markup.inline_keyboard[0][0].callback_data == 'leads:1'
    before = len(messages)
    await h.send(callback='leads:1', user=99)
    assert len(h.messages(99)) == before + 3  # 2 leads + back
    assert h.messages(99)[-1].reply_markup.inline_keyboard[0][0].callback_data == 'leads:0'


async def test_pending_notification_survives_actual_reopen(h):
    await h.apply()
    h.session.fail_chat = 99
    await deliver_pending(h.bot, h.db, 99)
    await h.db.close()
    db = Database(h.db.path)
    await db.open()
    h.db = db
    h.session.fail_chat = None
    await db.execute("UPDATE outbox SET next_attempt='2000'")
    await deliver_pending(h.bot, db, 99)
    assert (await db.fetch('SELECT * FROM outbox'))[0]['sent_at']
    assert len(await db.fetch('SELECT * FROM applications')) == 1


async def test_online_backup(h, tmp_path):
    from deploy.backup import backup
    import sqlite3
    await h.apply()
    target = tmp_path / 'backup.db'
    backup(h.db.path, target)
    with sqlite3.connect(target) as conn:
        assert conn.execute('SELECT COUNT(*) FROM applications').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 1
    with pytest.raises(ValueError):
        backup(h.db.path, target)


async def test_main_startup_and_shutdown(tmp_path, monkeypatch):
    from aiogram.types import User
    from bot import main
    session = FakeSession()
    original_request = session.make_request

    async def request(bot, method, timeout=None):
        if method.__class__.__name__ == 'GetMe':
            session.calls.append(method)
            return User(id=123456, is_bot=True, first_name='Test', username='test_bot')
        return await original_request(bot, method, timeout)

    session.make_request = request
    monkeypatch.setattr(main, 'EnvironmentSession', lambda: session)
    monkeypatch.setattr(main.Dispatcher, 'start_polling', AsyncMock())
    await main.run(Config('123456:FAKE_TOKEN', 99, str(tmp_path / 'startup.db')))
    assert [m.__class__.__name__ for m in session.calls] == ['GetMe', 'DeleteWebhook']
    assert session.calls[1].drop_pending_updates is False
