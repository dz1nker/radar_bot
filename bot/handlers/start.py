import asyncio
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from bot import texts
from bot.keyboards.inline import offer
from bot.states.application import Application



async def resume(message, state):
    current = await state.get_state()
    await message.answer(texts.AUDIENCE if current == Application.audience.state else texts.NICHE)


async def start(message: Message, state: FSMContext, db, config):
    if message.from_user.id == config.admin_id:
        await message.answer(texts.ADMIN)
    elif await state.get_state():
        await resume(message, state)
    elif await db.application(message.from_user.id):
        await message.answer(texts.EXISTING, reply_markup=offer(True))
    else:
        for i, text in enumerate(texts.INTRO):
            if i:
                await asyncio.sleep(0.6)
            await message.answer(text, reply_markup=offer() if i == 2 else None)


async def user_id(message: Message):
    await message.answer(texts.user_id(message.from_user.id))


async def help_command(message: Message, config):
    await message.answer(texts.ADMIN if message.from_user.id == config.admin_id else texts.USER_HELP)


async def cancel(message: Message, state: FSMContext):
    active = await state.get_state()
    await state.clear()
    await message.answer(texts.CANCELLED if active else texts.NO_FORM)


async def details(callback: CallbackQuery, db):
    await callback.answer()
    existing = await db.application(callback.from_user.id)
    await callback.message.answer(texts.DETAILS, reply_markup=offer(bool(existing)))


def router():
    route = Router()
    route.message(CommandStart())(start)
    route.message(Command('id'))(user_id)
    route.message(Command('help'))(help_command)
    route.message(Command('cancel'))(cancel)
    route.callback_query(F.data == 'details')(details)
    return route
