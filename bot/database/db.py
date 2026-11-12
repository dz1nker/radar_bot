import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiosqlite
from aiogram.fsm.storage.base import BaseStorage, StorageKey


def now():
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path):
        self.path = path
        self.lock = asyncio.Lock()

    async def open(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL UNIQUE,
                telegram_chat_id INTEGER NOT NULL,
                username TEXT, first_name TEXT, last_name TEXT,
                business_niche TEXT NOT NULL, target_audience TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('NEW','CONTACTED','CLOSED')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                admin_notified_at TEXT, version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS history (
                application_id INTEGER NOT NULL, version INTEGER NOT NULL,
                snapshot TEXT NOT NULL, PRIMARY KEY(application_id, version)
            );
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL,
                version INTEGER NOT NULL, payload TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, next_attempt TEXT NOT NULL,
                sent_at TEXT, last_error TEXT,
                UNIQUE(application_id, version)
            );
            CREATE TABLE IF NOT EXISTS fsm (
                key TEXT PRIMARY KEY, state TEXT, data TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS submissions (
                chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
                PRIMARY KEY(chat_id, message_id)
            );
        ''')
        # Retry notifications that failed on Windows before the timezone fix;
        # do not make users wait for their previous (up to one hour) backoff.
        await self.conn.execute(
            "UPDATE outbox SET next_attempt=? WHERE sent_at IS NULL AND last_error='ZoneInfoNotFoundError'",
            (now(),),
        )
        await self.conn.commit()

    async def close(self):
        await self.conn.close()

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            await self.conn.execute('BEGIN IMMEDIATE')
            try:
                yield self.conn
                await self.conn.commit()
            except BaseException:
                await self.conn.rollback()
                raise

    async def fetch(self, sql, params=()):
        async with self.lock:
            async with self.conn.execute(sql, params) as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    async def execute(self, sql, params=()):
        async with self.transaction() as conn:
            await conn.execute(sql, params)

    async def remember(self, user_id, chat_id):
        await self.execute('INSERT INTO users VALUES (?,?) ON CONFLICT(id) DO UPDATE SET chat_id=excluded.chat_id',
                           (user_id, chat_id))

    async def application(self, user_id):
        rows = await self.fetch('SELECT * FROM applications WHERE telegram_user_id=?', (user_id,))
        return rows[0] if rows else None

    async def submit(self, user, chat_id, message_id, niche, audience, fsm_key):
        # Application, history, notification and cleared form commit together.
        async with self.transaction() as conn:
            cursor = await conn.execute('INSERT OR IGNORE INTO submissions VALUES (?,?)', (chat_id, message_id))
            if not cursor.rowcount:
                return False
            timestamp = now()
            await conn.execute('''INSERT INTO applications
                (telegram_user_id,telegram_chat_id,username,first_name,last_name,
                 business_niche,target_audience,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,'NEW',?,?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                  telegram_chat_id=excluded.telegram_chat_id, username=excluded.username,
                  first_name=excluded.first_name,last_name=excluded.last_name,
                  business_niche=excluded.business_niche,target_audience=excluded.target_audience,
                  status='NEW',updated_at=excluded.updated_at,admin_notified_at=NULL,
                  version=applications.version+1''',
                (user.id, chat_id, user.username, user.first_name, user.last_name, niche, audience, timestamp, timestamp))
            async with conn.execute('SELECT * FROM applications WHERE telegram_user_id=?', (user.id,)) as cursor:
                row = dict(await cursor.fetchone())
            payload = json.dumps(row, ensure_ascii=False)
            await conn.execute('INSERT INTO history VALUES (?,?,?)', (row['id'], row['version'], payload))
            await conn.execute('INSERT INTO outbox(application_id,version,payload,next_attempt) VALUES (?,?,?,?)',
                               (row['id'], row['version'], payload, timestamp))
            await conn.execute('DELETE FROM fsm WHERE key=?', (fsm_key,))
            return True

    async def contacted(self, application_id, version):
        # An old notification must not mark a newly edited application reviewed.
        async with self.transaction() as conn:
            await conn.execute("UPDATE applications SET status='CONTACTED',updated_at=? WHERE id=? AND version=? AND status!='CONTACTED'",
                               (now(), application_id, version))
            async with conn.execute('SELECT * FROM applications WHERE id=? AND version=?',
                                    (application_id, version)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def statistics(self):
        rows = await self.fetch('''SELECT COUNT(*) total,
            COALESCE(SUM(status='NEW'),0) NEW, COALESCE(SUM(status='CONTACTED'),0) CONTACTED,
            COALESCE(SUM(status='CLOSED'),0) CLOSED, COALESCE(SUM(created_at>=?),0) week FROM applications''',
            ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),))
        return rows[0]


class SQLiteStorage(BaseStorage):
    def __init__(self, db):
        self.db = db

    @staticmethod
    def key(key: StorageKey):
        return json.dumps([key.bot_id, key.chat_id, key.user_id, key.thread_id, key.business_connection_id, key.destiny])

    async def set_state(self, key, state=None):
        value = state.state if hasattr(state, 'state') else state
        await self.db.execute('INSERT INTO fsm(key,state) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET state=excluded.state',
                              (self.key(key), value))

    async def get_state(self, key):
        rows = await self.db.fetch('SELECT state FROM fsm WHERE key=?', (self.key(key),))
        return rows[0]['state'] if rows else None

    async def set_data(self, key, data):
        await self.db.execute('INSERT INTO fsm(key,data) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data',
                              (self.key(key), json.dumps(data)))

    async def get_data(self, key):
        rows = await self.db.fetch('SELECT data FROM fsm WHERE key=?', (self.key(key),))
        return json.loads(rows[0]['data']) if rows else {}

    async def close(self):
        pass  # Connection owned by main(), after dispatcher shutdown.
