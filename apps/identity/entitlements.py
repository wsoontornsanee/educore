"""Feature Entitlements Engine (spec/02 §6, IAM-023, IAM-024)."""
from typing import Optional
from .models import FoundationEntitlement

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
