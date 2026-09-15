"""MySQL Named Advisory Locks utility (ARC-007).

Every scheduled command must acquire a named advisory lock (`GET_LOCK('educore:<job>', 0)`)
for its run and exit cleanly if the lock is already held.
"""
import logging
import threading
from contextlib import contextmanager
from django.db import connection as default_connection

logger = logging.getLogger(__name__)

# Fallback lock store for non-MySQL test environments (e.g., SQLite in CI/local unit tests)
_in_memory_locks = set()
_in_memory_mutex = threading.Lock()

def _format_lock_name(name: str) -> str:
    if not name.startswith("educore:"):
        return f"educore:{name}"
    return name

def acquire_advisory_lock(name: str, timeout: int = 0, db_connection=None) -> bool:
    """Acquire a named advisory lock.

    db_connection defaults to Django's thread-local default connection — every
    real caller (management commands) leaves this unset. It exists so tests can
    pass a genuinely separate connection to simulate a second process contending
    for the same lock: MySQL's GET_LOCK lets the SAME session re-acquire a name
    it already holds (non-blocking, since MySQL 5.7.5), so exercising real
    cross-session rejection needs an actually different session, not just a
    second Python-level call on the one connection Django's TestCase reuses.

    Returns True if successfully acquired, False otherwise.
    """
    db_connection = db_connection or default_connection
    full_name = _format_lock_name(name)

    if db_connection.vendor == 'mysql':
        with db_connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, %s)", [full_name, timeout])
            row = cursor.fetchone()
            return bool(row and row[0] == 1)
    else:
        # Emulation for local testing on SQLite
        with _in_memory_mutex:
            if full_name in _in_memory_locks:
                return False
            _in_memory_locks.add(full_name)
            return True

def release_advisory_lock(name: str, db_connection=None) -> bool:
    """Release a previously acquired advisory lock. See acquire_advisory_lock
    for db_connection's purpose (test-only; real callers leave it unset)."""
    db_connection = db_connection or default_connection
    full_name = _format_lock_name(name)

    if db_connection.vendor == 'mysql':
        with db_connection.cursor() as cursor:
            cursor.execute("SELECT RELEASE_LOCK(%s)", [full_name])
            row = cursor.fetchone()
            return bool(row and row[0] == 1)
    else:
        with _in_memory_mutex:
            if full_name in _in_memory_locks:
                _in_memory_locks.remove(full_name)
                return True
            return False

@contextmanager
def advisory_lock(name: str, timeout: int = 0, db_connection=None):
    """Context manager for acquiring and safely releasing an advisory lock.
    See acquire_advisory_lock for db_connection's purpose (test-only; real
    callers leave it unset).

    Yields True if lock was acquired, False if lock was already held.
    """
    acquired = acquire_advisory_lock(name, timeout, db_connection=db_connection)
    try:
        yield acquired
    finally:
        if acquired:
            release_advisory_lock(name, db_connection=db_connection)
