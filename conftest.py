"""Repo-wide pytest fixtures.

Clears Django's cache (DatabaseCache on MySQL — django_cache table) before
every test. Without this, state that lives in the cache — most notably
apps.core.throttling's rate-limit counters — accumulates across the whole
test run instead of resetting per test, since Django's TestCase only rolls
back the database, never the cache. A test file that legitimately exercises
a throttle (e.g. hitting /api/v1/auth/otp/request/ several times) would
otherwise silently poison unrelated later tests hitting the same endpoint.
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_cache_between_tests():
    from django.core.cache import cache
    try:
        cache.clear()
    except Exception:
        # DatabaseCache needs a DB connection — SimpleTestCase-based tests
        # forbid one entirely (by design, they don't touch the DB at all),
        # so there's nothing to clear for them anyway. Best-effort, not a
        # hard dependency: don't force DB setup onto tests that don't use it.
        pass
    yield
