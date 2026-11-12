import re
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot import texts


def offer(existing=False):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.EDIT_LABEL if existing else texts.TRY_LABEL,
                              callback_data='apply:edit' if existing else 'apply:new')],
        [InlineKeyboardButton(text=texts.DETAILS_LABEL, callback_data='details')],
    ])


def lead_buttons(row):
    username = row['username']
    url = (f'https://t.me/{username}' if username and re.fullmatch(r'[A-Za-z0-9_]{5,32}', username)
           else f"tg://user?id={row['telegram_user_id']}")
    buttons = [[InlineKeyboardButton(text=texts.PROFILE_LABEL, url=url)]]
    if row['status'] != 'CONTACTED':
        buttons.append([InlineKeyboardButton(text=texts.CONTACT_LABEL, callback_data=f"contact:{row['id']}:{row['version']}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def pages(page, has_next):
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton(text=texts.PREVIOUS_LABEL, callback_data=f'leads:{page-1}'))
    if has_next:
        buttons.append(InlineKeyboardButton(text=texts.NEXT_LABEL, callback_data=f'leads:{page+1}'))
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None
