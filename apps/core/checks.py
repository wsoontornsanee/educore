"""Environment preconditions that must fail loud, not silently (deploy/environment
trust boundary — see AGENTS.md red lines on never letting financial/attendance
data go silently wrong).
"""
from django.conf import settings
from django.core.checks import Error, Tags, register
from django.db import connections


@register(Tags.database)
def check_mysql_named_timezones_loaded(app_configs, **kwargs):
    """MySQL's named-timezone tables (mysql.time_zone_name) are NOT loaded by
    default on a fresh install or the official mysql:8.0 image. Without them,
    CONVERT_TZ('UTC', settings.TIME_ZONE) silently returns NULL for every row —
    no exception. Django compiles every `__date` lookup on a DateTimeField
    through exactly that call whenever TIME_ZONE isn't UTC, so the day-bucketed
    reporting rollups in apps/reporting/services.py silently write zero rows
    instead of raising. See README.md "Local Development Setup" for the fix.
    """
    if not settings.USE_TZ or settings.TIME_ZONE == 'UTC':
        return []

    connection = connections['default']
    if connection.vendor != 'mysql':
        return []

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT CONVERT_TZ(NOW(), 'UTC', %s)", [settings.TIME_ZONE])
            result = cursor.fetchone()
    except Exception:
        # No live DB connection available (e.g. collectstatic) — nothing to check.
        return []

    if result is None or result[0] is None:
        return [
            Error(
                f"MySQL's named-timezone tables are not loaded, so CONVERT_TZ('UTC', "
                f"'{settings.TIME_ZONE}') returns NULL. Every `__date` lookup on a "
                f"DateTimeField silently matches nothing under this condition — the "
                f"reporting rollups (apps/reporting/services.py) will write zero rows "
                f"with no error. Fix: run `mysql_tzinfo_to_sql /usr/share/zoneinfo | "
                f"mysql -u root -p mysql` against this database. See README.md "
                f"'Local Development Setup'.",
                id='core.E001',
            )
        ]
    return []
