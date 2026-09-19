"""Records that a user was active today (RPT-012 input).

Called from `EduCoreJWTAuthentication.authenticate`, which has already loaded the
user, so the steady-state cost of a request is one date comparison. The first
request of a day claims the day with an atomic UPDATE; only the request whose
UPDATE matched a row inserts the `UserActivityDay`, so racing first requests
insert exactly once.
"""
import logging

from django.db import transaction
from django.utils import timezone

from apps.identity.models import User, UserActivityDay

logger = logging.getLogger(__name__)


def record_activity(user) -> None:
    foundation_id = getattr(user, 'foundation_id', None)
    if not foundation_id:
        return
    today = timezone.localdate()
    if user.last_activity_date == today:
        return
    try:
        with transaction.atomic():
            claimed = (
                User.all_tenants.filter(pk=user.pk)
                .exclude(last_activity_date=today)
                .update(last_activity_date=today)
            )
            if claimed:
                UserActivityDay.all_tenants.create(foundation_id=foundation_id, user_id=user.pk, date=today)
        user.last_activity_date = today
    except Exception:
        logger.exception("Could not record user activity")
