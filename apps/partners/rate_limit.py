"""Per-key rate limiting for the partner API (spec/18 §8).

600 requests/minute/key via the DatabaseCache (MySQL-only constraint — no
Redis). A cache outage fails open: MySQL remains the durable store; rate
limiting is a guardrail, not a correctness dependency.
"""
import time

from django.core.cache import cache

from apps.partners.errors import PartnerAPIError

REQUESTS_PER_MINUTE = 600
WINDOW_SECONDS = 60


def check_rate_limit(key_id: str) -> None:
    """Raise 429 RATE_LIMITED (carrying Retry-After) when the key exceeds quota."""
    now = int(time.time())
    bucket = now // WINDOW_SECONDS
    cache_key = f"partner_rl:{key_id}:{bucket}"
    try:
        count = cache.get(cache_key)
        if count is None:
            cache.set(cache_key, 1, WINDOW_SECONDS)
            return
        if count >= REQUESTS_PER_MINUTE:
            err = PartnerAPIError(429, 'RATE_LIMITED', 'Rate limit exceeded (600 requests/minute).')
            err.retry_after = WINDOW_SECONDS - (now % WINDOW_SECONDS)
            raise err
        cache.set(cache_key, count + 1, WINDOW_SECONDS)
    except PartnerAPIError:
        raise
    except Exception:
        return
