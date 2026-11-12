import json

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from bot import texts
from bot.database.db import SQLiteStorage
from bot.handlers.start import resume
from bot.keyboards.inline import offer
from bot.states.application import Application



async def begin(callback: CallbackQuery, state: FSMContext, db):
    await callback.answer()
    if await state.get_state():
        await resume(callback.message, state)
        return
    existing = await db.application(callback.from_user.id)
    if existing and callback.data != 'apply:edit':
        await callback.message.answer(texts.EXISTING, reply_markup=offer(True))
        return
    # One durable write for the initial FSM state and data.
    await db.execute("INSERT INTO fsm(key,state,data) VALUES (?,?,'{}') ON CONFLICT(key) DO UPDATE SET state=excluded.state,data='{}'",
                     (SQLiteStorage.key(state.key), Application.niche.state))
    await callback.message.answer(texts.PRIVACY)
    await callback.message.answer(texts.NICHE)


def answer_text(message):
    if message.text is None or message.text.startswith('/'):
        return None
    value = message.text.strip()
    return value if 1 <= len(value) <= 1000 else None


async def niche(message: Message, state: FSMContext, db):
    value = answer_text(message)
    if value is None:
        await message.answer(texts.INVALID)
        return
    data = json.dumps({'business_niche': value, 'niche_message_id': message.message_id})
    await db.execute('UPDATE fsm SET state=?,data=? WHERE key=?',
                     (Application.audience.state, data, SQLiteStorage.key(state.key)))
    await message.answer(texts.AUDIENCE)


async def audience(message: Message, state: FSMContext, db):
    data = await state.get_data()
    # Re-delivery of the first answer cannot become the second answer.
    if message.message_id <= data.get('niche_message_id', -1):
        return
    value = answer_text(message)
    if value is None:
        await message.answer(texts.INVALID)
        return
    await db.submit(message.from_user, message.chat.id, message.message_id,
                    data['business_niche'], value, SQLiteStorage.key(state.key))
    await message.answer(texts.SUCCESS, reply_markup=offer(True))


def router():
    route = Router()
    route.callback_query(F.data.in_({'apply:new', 'apply:edit'}))(begin)
    route.message(Application.niche)(niche)
    route.message(Application.audience)(audience)
    return route
