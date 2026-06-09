"""
SQLite connection helpers for browser profile databases.
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


class _TempCopyConnection(sqlite3.Connection):
    """Connection that deletes its temporary DB copy when closed."""
    _temp_dir = None

    def close(self):
        super().close()
        if self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def _copy_db_files(full_path: str, dest_dir: str) -> str:
    """Copy a SQLite DB plus any WAL/SHM sidecars into dest_dir; return the copy's path."""
    dst = os.path.join(dest_dir, os.path.basename(full_path))
    for suffix in ('', '-wal', '-shm'):
        src = full_path + suffix
        if os.path.exists(src):
            shutil.copyfile(src, dst + suffix)
    return dst


def open_db(db_path: str, db_name: str, no_copy: bool = False, temp_dir: str = None):
    """
    Open a Chrome SQLite database. Copies the file (plus any WAL/SHM) to a
    temp directory first unless no_copy is True. A temp directory created
    here is deleted when the returned connection is closed; a caller-supplied
    temp_dir is left for the caller to manage.

    Returns an open sqlite3.Connection or None on failure.
    """
    full_path = os.path.join(db_path, db_name)
    if not os.path.exists(full_path):
        log.debug(f'{db_name} not found in {db_path}')
        return None

    owned_temp = None
    if no_copy:
        path_to_open = full_path
    else:
        dest_dir = temp_dir or tempfile.mkdtemp(prefix='chrome_artifacts_')
        if temp_dir is None:
            owned_temp = dest_dir
        try:
            path_to_open = _copy_db_files(full_path, dest_dir)
        except OSError as e:
            log.error(f'Could not copy {db_name}: {e}')
            if owned_temp:
                shutil.rmtree(owned_temp, ignore_errors=True)
            return None

    try:
        conn = sqlite3.connect(path_to_open, factory=_TempCopyConnection)
    except sqlite3.Error as e:
        log.error(f'Could not open {db_name}: {e}')
        if owned_temp:
            shutil.rmtree(owned_temp, ignore_errors=True)
        return None

    conn._temp_dir = owned_temp
    conn.row_factory = _dict_factory
    conn.text_factory = _text_factory
    try:
        conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")
        return conn
    except sqlite3.Error as e:
        log.error(f'Could not open {db_name}: {e}')
        conn.close()  # also removes the temp copy
        return None


def open_db_copy(full_path: str, prefix: str = 'bcat_') -> sqlite3.Connection | None:
    """
    Copy a standalone SQLite DB (plus any WAL/SHM) to a temp directory and
    open it with sqlite3.Row rows. The temp copy is deleted when the
    connection is closed. Used by the Firefox and Safari parsers.

    Returns an open sqlite3.Connection or None on failure.
    """
    if not os.path.exists(full_path):
        log.warning(f'Not found: {full_path}')
        return None
    tmp = tempfile.mkdtemp(prefix=prefix)
    try:
        dst = _copy_db_files(full_path, tmp)
        conn = sqlite3.connect(dst, factory=_TempCopyConnection)
    except Exception as e:
        log.error(f'Could not open {full_path}: {e}')
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    conn._temp_dir = tmp
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return column names for a table, or [] if the table doesn't exist."""
    try:
        cur = conn.execute(f'PRAGMA table_info({table})')
        return [row['name'] for row in cur.fetchall()]
    except sqlite3.Error:
        return []
