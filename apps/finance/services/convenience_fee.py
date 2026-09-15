from decimal import Decimal, ROUND_HALF_UP

from apps.core.services import audit
from apps.finance.models import (
    ConvenienceFeeAllocation,
    ConvenienceFeeType,
    SchoolConvenienceFeePolicy,
)

# Platform default when a school has no explicit policy row: PASSED_TO_PARENT per
# the resolved [Open Decision] Payment Gateway Convenience Fee Allocation Policy,
# but with fee_value=0 so no school is ever silently charged a fee amount it never
# configured — the allocation only has an effect once a school sets a real fee.
DEFAULT_ALLOCATION = ConvenienceFeeAllocation.PASSED_TO_PARENT
DEFAULT_FEE_TYPE = ConvenienceFeeType.FIXED
DEFAULT_FEE_VALUE = Decimal('0.00')


def set_school_convenience_fee_policy(
    school,
    allocation: str = DEFAULT_ALLOCATION,
    fee_type: str = DEFAULT_FEE_TYPE,
    fee_value: Decimal = DEFAULT_FEE_VALUE,
    is_active: bool = True,
) -> SchoolConvenienceFeePolicy:
    """FIN-017: configure a school's convenience fee allocation and schedule."""
    config, _created = SchoolConvenienceFeePolicy.objects.update_or_create(
        foundation_id=school.foundation_id,
        school=school,
        defaults={
            'allocation': allocation,
            'fee_type': fee_type,
            'fee_value': fee_value,
            'is_active': is_active,
        },
    )
    audit(
        action='finance.convenience_fee_policy.set',
        entity_type='SchoolConvenienceFeePolicy',
        entity_id=config.id,
        foundation_id=school.foundation_id,
        school_id=school.id,
        diff={'allocation': allocation, 'fee_type': fee_type, 'fee_value': str(fee_value)},
    )
    return config


def get_effective_convenience_fee_policy(school) -> dict:
    """Returns the school's active policy, or the platform default if unconfigured
    or explicitly deactivated (is_active=False behaves the same as unconfigured —
    no fee, allocation irrelevant).
    """
    config = SchoolConvenienceFeePolicy.objects.filter(school=school, is_active=True).first()
    if config is None:
        return {
            'allocation': DEFAULT_ALLOCATION,
            'fee_type': DEFAULT_FEE_TYPE,
            'fee_value': DEFAULT_FEE_VALUE,
        }
    return {
        'allocation': config.allocation,
        'fee_type': config.fee_type,
        'fee_value': config.fee_value,
    }


def calculate_convenience_fee(school, base_amount: Decimal) -> Decimal:
    """FIN-017: the fee to display/charge on top of base_amount when the school's
    policy is PASSED_TO_PARENT. Returns 0 for ABSORBED_BY_SCHOOL or an unconfigured
    school (see get_effective_convenience_fee_policy).
    """
    policy = get_effective_convenience_fee_policy(school)
    if policy['allocation'] != ConvenienceFeeAllocation.PASSED_TO_PARENT:
        return Decimal('0.00')

    if policy['fee_type'] == ConvenienceFeeType.PERCENTAGE:
        fee = (base_amount * policy['fee_value'] / Decimal('100'))
    else:
        fee = policy['fee_value']

    return fee.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
