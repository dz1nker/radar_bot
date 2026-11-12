from aiogram.fsm.state import State, StatesGroup


class Application(StatesGroup):
    niche = State()
    audience = State()
