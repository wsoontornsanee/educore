"""Feature Entitlements Engine (spec/02 §6, IAM-023, IAM-024)."""
import calendar
import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from django.db.models import Q

from .models import EntitlementChange, Foundation, FoundationEntitlement

# Canonical Module Keys (spec/02 §6, IAM-025)
MODULE_ACADEMIC = FoundationEntitlement.MODULE_ACADEMIC
MODULE_ATTENDANCE = FoundationEntitlement.MODULE_ATTENDANCE
MODULE_FINANCE = FoundationEntitlement.MODULE_FINANCE
MODULE_WALLET = FoundationEntitlement.MODULE_WALLET
MODULE_CAMPUS_LIFE = FoundationEntitlement.MODULE_CAMPUS_LIFE
MODULE_PAYROLL = FoundationEntitlement.MODULE_PAYROLL
MODULE_ANALYTICS = FoundationEntitlement.MODULE_ANALYTICS
MODULE_HARDWARE = FoundationEntitlement.MODULE_HARDWARE

ALL_MODULES = [
    MODULE_ACADEMIC,
    MODULE_ATTENDANCE,
    MODULE_FINANCE,
    MODULE_WALLET,
    MODULE_CAMPUS_LIFE,
    MODULE_PAYROLL,
    MODULE_ANALYTICS,
    MODULE_HARDWARE,
]

def is_module_entitled(
    foundation_id: int,
    module_key: str,
    school_id: Optional[int] = None
) -> bool:
    """Evaluate whether a module is enabled for a foundation/school context (IAM-023, IAM-024).
    
    Resolution Order:
    1. Per-school override (foundation_id, school_id, module_key).
    2. Foundation-wide default (foundation_id, school_id=None, module_key).
    3. Fallback: True (standard entitlement enabled).
    """
    if not foundation_id:
        return False

    # 1. Check school-specific override if school_id is given
    if school_id is not None:
        school_entitlement = FoundationEntitlement.all_tenants.filter(
            foundation_id=foundation_id,
            school_id=school_id,
            module_key=module_key,
            deleted_at__isnull=True,
        ).first()
        if school_entitlement is not None:
            return school_entitlement.enabled

    # 2. Check foundation-wide default
    foundation_entitlement = FoundationEntitlement.all_tenants.filter(
        foundation_id=foundation_id,
        school_id__isnull=True,
        module_key=module_key,
        deleted_at__isnull=True,
    ).first()
    if foundation_entitlement is not None:
        return foundation_entitlement.enabled

    # 3. Default to enabled
    return True

def get_active_entitlements(
    foundation_id: int,
    school_id: Optional[int] = None
) -> dict[str, bool]:
    """Return dictionary of module enablement for UI navigation gating (IAM-024)."""
    return {
        module: is_module_entitled(foundation_id, module, school_id)
        for module in ALL_MODULES
    }

def set_module_entitlement(
    foundation_id: int,
    module_key: str,
    enabled: bool,
    school_id: Optional[int] = None,
    limits: Optional[dict] = None
) -> FoundationEntitlement:
    """Set or toggle module entitlement for a foundation or school (FND-012)."""
    entitlement, _ = FoundationEntitlement.all_tenants.update_or_create(
        foundation_id=foundation_id,
        school_id=school_id,
        module_key=module_key,
        defaults={
            'enabled': enabled,
            'limits': limits or {},
            'deleted_at': None,
        }
    )
    return entitlement


def entitled_days_in_month(foundation_id: int, school_id: int, month: datetime.date) -> dict[str, int]:
    """For each module, the number of days of `month` it was entitled for one school (RPT-010).

    Rebuilt from `EntitlementChange`, resolving each day exactly as `is_module_entitled` does now: the
    school's own value, else the foundation-wide one, else enabled. A day counts when the module was
    entitled at the END of that day in the foundation's timezone, so switching a module off part way
    through a day still bills that day, and switching it on part way through does too. Changes are read
    from the whole history up to the end of the month, so a value set months ago carries forward.
    """
    month = month.replace(day=1)
    days_in_month = calendar.monthrange(month.year, month.month)[1]
    tz = ZoneInfo(Foundation.objects.values_list('timezone', flat=True).get(pk=foundation_id))
    last_day = month.replace(day=days_in_month)

    # (scope school id or None, module) -> value in force, None = no own value
    state: dict[tuple[Optional[int], str], Optional[bool]] = {}
    changes = [
        (change.effective_from.astimezone(tz).date(), change)
        for change in EntitlementChange.all_tenants.filter(
            Q(school_id__isnull=True) | Q(school_id=school_id), foundation_id=foundation_id,
        ).order_by('effective_from', 'id')
    ]
    changes = [(day, change) for day, change in changes if day <= last_day]

    days = {module: 0 for module in ALL_MODULES}
    position = 0
    for offset in range(days_in_month):
        day = month + datetime.timedelta(days=offset)
        while position < len(changes) and changes[position][0] <= day:
            change = changes[position][1]
            state[(change.school_id, change.module_key)] = change.enabled
            position += 1
        for module in ALL_MODULES:
            own = state.get((school_id, module))
            value = own if own is not None else state.get((None, module))
            if value is None or value:
                days[module] += 1
    return days
