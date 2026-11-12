"""Online SQLite backup: python deploy/backup.py data/bot.db /safe/path/backup.db"""
import sqlite3
import sys
from pathlib import Path


def backup(source, destination):
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if not source.is_file() or destination.exists() or source == destination:
        raise ValueError('Источник должен существовать, файл назначения — отсутствовать')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the destination securely; never replace an existing backup.
    with destination.open('xb'):
        pass
    destination.chmod(0o600)
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Проверка резервной копии не пройдена')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    backup(*sys.argv[1:])
