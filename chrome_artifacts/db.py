"""
SQLite connection helpers for Chrome profile databases.
"""
import logging
import os
import shutil
import sqlite3
import tempfile

log = logging.getLogger(__name__)


def _dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def _text_factory(data):
    try:
        return data.decode('utf-8')
    except (UnicodeDecodeError, AttributeError):
        return data


def open_db(db_path: str, db_name: str, no_copy: bool = False, temp_dir: str = None):
    """
    Open a Chrome SQLite database. Copies the file (plus any WAL/SHM) to a
    temp directory first unless no_copy is True.

    Returns an open sqlite3.Connection or None on failure.
    """
    full_path = os.path.join(db_path, db_name)
    if not os.path.exists(full_path):
        log.debug(f'{db_name} not found in {db_path}')
        return None

    if no_copy:
        path_to_open = full_path
    else:
        dest_dir = temp_dir or tempfile.mkdtemp(prefix='chrome_artifacts_')
        path_to_open = os.path.join(dest_dir, db_name)
        try:
            for suffix in ('', '-wal', '-shm'):
                src = full_path + suffix
                if os.path.exists(src):
                    shutil.copyfile(src, path_to_open + suffix)
        except OSError as e:
            log.error(f'Could not copy {db_name}: {e}')
            return None

    try:
        conn = sqlite3.connect(path_to_open)
        conn.row_factory = _dict_factory
        conn.text_factory = _text_factory
        conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")
        return conn
    except sqlite3.Error as e:
        log.error(f'Could not open {db_name}: {e}')
        return None


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return column names for a table, or [] if the table doesn't exist."""
    try:
        cur = conn.execute(f'PRAGMA table_info({table})')
        return [row['name'] for row in cur.fetchall()]
    except sqlite3.Error:
        return []
