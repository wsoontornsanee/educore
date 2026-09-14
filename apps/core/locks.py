"""MySQL Named Advisory Locks utility (ARC-007).

Every scheduled command must acquire a named advisory lock (`GET_LOCK('educore:<job>', 0)`)
for its run and exit cleanly if the lock is already held.
"""
import logging
import threading
from contextlib import contextmanager
from django.db import connection

logger = logging.getLogger(__name__)

# Fallback lock store for non-MySQL test environments (e.g., SQLite in CI/local unit tests)
_in_memory_locks = set()
_in_memory_mutex = threading.Lock()

def _format_lock_name(name: str) -> str:
    if not name.startswith("educore:"):
        return f"educore:{name}"
    return name

def acquire_advisory_lock(name: str, timeout: int = 0) -> bool:
    """Acquire a named advisory lock.
    
    Returns True if successfully acquired, False otherwise.
    """
    full_name = _format_lock_name(name)

    if connection.vendor == 'mysql':
        with connection.cursor() as cursor:
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

def release_advisory_lock(name: str) -> bool:
    """Release a previously acquired advisory lock."""
    full_name = _format_lock_name(name)

    if connection.vendor == 'mysql':
        with connection.cursor() as cursor:
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
def advisory_lock(name: str, timeout: int = 0):
    """Context manager for acquiring and safely releasing an advisory lock.
    
    Yields True if lock was acquired, False if lock was already held.
    """
    acquired = acquire_advisory_lock(name, timeout)
    try:
        yield acquired
    finally:
        if acquired:
            release_advisory_lock(name)
