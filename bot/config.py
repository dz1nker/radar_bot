import logging
import os
from dataclasses import dataclass
from pathlib import Path

from aiogram.utils.token import TokenValidationError, validate_token
from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    token: str
    admin_id: int
    database_path: str = './data/bot.db'
    log_level: str = 'INFO'

    @classmethod
    def load(cls):
        load_dotenv()
        token = os.getenv('BOT_TOKEN', '').strip()
        try:
            validate_token(token)
        except TokenValidationError:
            raise ValueError('BOT_TOKEN отсутствует или имеет неверный формат') from None
        try:
            admin_id = int(os.getenv('ADMIN_TELEGRAM_ID', ''))
            if admin_id <= 0:
                raise ValueError
        except ValueError:
            raise ValueError('ADMIN_TELEGRAM_ID должен быть положительным числовым User ID') from None
        path = os.getenv('DATABASE_PATH', './data/bot.db').strip()
        if not path:
            raise ValueError('DATABASE_PATH не может быть пустым')
        level = os.getenv('LOG_LEVEL', 'INFO').upper()
        if level not in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
            raise ValueError('Неверный LOG_LEVEL')
        return cls(token, admin_id, str(Path(path)), level)


class RedactToken(logging.Filter):
    def __init__(self, token):
        super().__init__()
        self.token = token

    def filter(self, record):
        record.msg = record.getMessage().replace(self.token, '[REDACTED]')
        record.args = ()
        # Never format tracebacks from HTTP clients: request URLs can include tokens.
        record.exc_info = None
        record.exc_text = None
        return True
