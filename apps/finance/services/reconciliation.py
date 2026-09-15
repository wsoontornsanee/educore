"""Gateway settlement reconciliation service (spec/06 FIN-024).

Implements the core reconciliation logic:
- Fetch settlement data from a payment gateway for a given date.
- Match each gateway record to an existing Payment by external_id.
- Auto-settle matched payments where amounts agree.
- Create PaymentDiscrepancy records for mismatches and missing entries.
- Update GatewaySettlementBatch counters atomically.

NOTE: All ORM calls use `all_tenants` (not the default `objects` manager)
because this service receives `foundation_id` explicitly and does NOT
rely on thread-local tenant context.  This follows the same pattern as
other platform-level batch services (e.g. reconcile_wallet_balances).
"""
import datetime
import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.finance.models import (
    DiscrepancyResolution,
    DiscrepancyType,
    GatewaySettlementBatch,
    Payment,
    PaymentDiscrepancy,
    PaymentStatus,
    SettlementBatchStatus,
)
from apps.finance.services.payment_providers import (
    PaymentGatewayError,
    get_payment_provider,
)

logger = logging.getLogger(__name__)

# Tolerance for amount comparison (IDR has no sub-rupiah, but allow 1-rupiah rounding)
AMOUNT_TOLERANCE = Decimal('1.00')


def reconcile_gateway_settlement(
    provider_name: str,
    settlement_date: datetime.date,
    foundation_id: int,
    dry_run: bool = False,
) -> dict:
    """Fetch and reconcile gateway settlements for one provider on one date.

    This is the primary entry-point called by the management command.
    It is wrapped in a tenant context by the caller.

    Returns a summary dict:
    {
        'batch_id': int,
        'provider': str,
        'settlement_date': date,
        'total': int,
        'matched': int,
        'missing': int,
        'mismatch': int,
        'dry_run': bool,
    }
    """
    provider = get_payment_provider(provider_name)

    # Get-or-create the batch record (idempotent re-runs overwrite).
    # Use all_tenants to bypass thread-local tenant scoping.
    batch, created = GatewaySettlementBatch.all_tenants.get_or_create(
        foundation_id=foundation_id,
        provider=provider_name.upper(),
        settlement_date=settlement_date,
        defaults={'status': SettlementBatchStatus.PENDING},
    )

    if not created and batch.status == SettlementBatchStatus.COMPLETED and not dry_run:
        logger.info(
            "Batch %s/%s already COMPLETED - skipping re-run.",
            provider_name,
            settlement_date,
        )
        return _batch_summary(batch, dry_run=dry_run)

    batch.status = SettlementBatchStatus.PROCESSING
    batch.error_message = ''
    if not dry_run:
        batch.save(update_fields=['status', 'error_message'])

    try:
        records = provider.fetch_settlement(settlement_date)
    except PaymentGatewayError as exc:
        logger.error("Gateway fetch failed for %s/%s: %s", provider_name, settlement_date, exc)
        if not dry_run:
            batch.status = SettlementBatchStatus.FAILED
            batch.error_message = str(exc)
            batch.save(update_fields=['status', 'error_message'])
        return {
            'batch_id': batch.id,
            'provider': provider_name,
            'settlement_date': settlement_date,
            'total': 0,
            'matched': 0,
            'missing': 0,
            'mismatch': 0,
            'error': str(exc),
            'dry_run': dry_run,
        }

    matched = 0
    missing = 0
    mismatch = 0

    for record in records:
        result = _process_settlement_record(record, batch, foundation_id, dry_run=dry_run)
        if result == 'matched':
            matched += 1
        elif result == 'missing':
            missing += 1
        elif result == 'mismatch':
            mismatch += 1

    total = len(records)
    batch_status = (
        SettlementBatchStatus.COMPLETED
        if missing == 0 and mismatch == 0
        else SettlementBatchStatus.PARTIAL
    )

    if not dry_run:
        batch.status = batch_status
        batch.fetched_at = timezone.now()
        batch.total_records = total
        batch.matched_count = matched
        batch.missing_count = missing
        batch.mismatch_count = mismatch
        batch.save(update_fields=[
            'status', 'fetched_at', 'total_records',
            'matched_count', 'missing_count', 'mismatch_count',
        ])

    logger.info(
        "Reconciliation %s/%s: total=%d matched=%d missing=%d mismatch=%d dry_run=%s",
        provider_name, settlement_date, total, matched, missing, mismatch, dry_run,
    )
    return {
        'batch_id': batch.id,
        'provider': provider_name,
        'settlement_date': settlement_date,
        'total': total,
        'matched': matched,
        'missing': missing,
        'mismatch': mismatch,
        'dry_run': dry_run,
    }


@transaction.atomic
def _process_settlement_record(
    record: dict,
    batch: GatewaySettlementBatch,
    foundation_id: int,
    dry_run: bool,
) -> str:
    """Reconcile a single gateway settlement record.

    Returns one of: 'matched', 'missing', 'mismatch'.
    """
    external_id = record['external_id']
    gateway_amount: Decimal = record['amount']
    gateway_fee: Decimal = record['fee']
    gateway_net: Decimal = record['net']
    channel: str = record.get('channel', '')
    bank: str = record.get('bank') or ''
    settled_at = record.get('settled_at')

    try:
        payment = Payment.all_tenants.get(
            foundation_id=foundation_id,
            external_id=external_id,
        )
    except Payment.DoesNotExist:
        logger.warning("MISSING_IN_SYSTEM: external_id=%s not found in payments", external_id)
        if not dry_run:
            PaymentDiscrepancy.all_tenants.update_or_create(
                foundation_id=foundation_id,
                batch=batch,
                external_id=external_id,
                discrepancy_type=DiscrepancyType.MISSING_IN_SYSTEM,
                defaults={
                    'payment': None,
                    'gateway_amount': gateway_amount,
                    'gateway_fee': gateway_fee,
                    'gateway_net': gateway_net,
                    'channel': channel,
                    'bank': bank,
                    'gateway_settled_at': settled_at,
                    'resolution': DiscrepancyResolution.PENDING,
                    'raw_gateway_record': record.get('raw', {}),
                },
            )
        return 'missing'

    # Payment found - check amount delta
    delta = abs(payment.amount - gateway_amount)
    if delta > AMOUNT_TOLERANCE:
        logger.warning(
            "AMOUNT_MISMATCH: external_id=%s system=%s gateway=%s delta=%s",
            external_id, payment.amount, gateway_amount, delta,
        )
        if not dry_run:
            PaymentDiscrepancy.all_tenants.update_or_create(
                foundation_id=foundation_id,
                batch=batch,
                external_id=external_id,
                discrepancy_type=DiscrepancyType.AMOUNT_MISMATCH,
                defaults={
                    'payment': payment,
                    'gateway_amount': gateway_amount,
                    'gateway_fee': gateway_fee,
                    'gateway_net': gateway_net,
                    'system_amount': payment.amount,
                    'channel': channel,
                    'bank': bank,
                    'gateway_settled_at': settled_at,
                    'resolution': DiscrepancyResolution.PENDING,
                    'raw_gateway_record': record.get('raw', {}),
                },
            )
        return 'mismatch'

    # Auto-settle: update payment if not already settled
    if not dry_run and payment.status != PaymentStatus.SETTLED:
        payment.status = PaymentStatus.SETTLED
        payment.settled_at = settled_at or timezone.now()
        payment.fee = gateway_fee
        payment.net = gateway_net
        payment.save(update_fields=['status', 'settled_at', 'fee', 'net'])

        # Clear any prior PENDING discrepancy for this external_id
        PaymentDiscrepancy.all_tenants.filter(
            foundation_id=foundation_id,
            external_id=external_id,
            resolution=DiscrepancyResolution.PENDING,
        ).update(
            resolution=DiscrepancyResolution.AUTO_SETTLED,
            resolved_at=timezone.now(),
        )

    return 'matched'


def resolve_discrepancy(
    discrepancy_id: int,
    resolution: str,
    resolved_by,
    foundation_id: int,
    notes: str = '',
) -> 'PaymentDiscrepancy':
    """Manually resolve a PaymentDiscrepancy (called by API view).

    Allowed transitions: PENDING -> MANUAL_SETTLED | WAIVED | ESCALATED.
    Raises ValueError on invalid state or resolution value.
    """
    valid_resolutions = {
        DiscrepancyResolution.MANUAL_SETTLED,
        DiscrepancyResolution.WAIVED,
        DiscrepancyResolution.ESCALATED,
    }
    if resolution not in valid_resolutions:
        raise ValueError(
            f"Invalid resolution '{resolution}'. Must be one of {valid_resolutions}."
        )

    discrepancy = PaymentDiscrepancy.all_tenants.get(
        id=discrepancy_id,
        foundation_id=foundation_id,
    )
    if discrepancy.resolution != DiscrepancyResolution.PENDING:
        raise ValueError(
            f"Discrepancy #{discrepancy_id} is already '{discrepancy.resolution}' - cannot resolve again."
        )

    with transaction.atomic():
        discrepancy.resolution = resolution
        discrepancy.resolved_by = resolved_by
        discrepancy.resolved_at = timezone.now()
        discrepancy.resolution_notes = notes
        discrepancy.save(
            update_fields=['resolution', 'resolved_by', 'resolved_at', 'resolution_notes']
        )

        if resolution == DiscrepancyResolution.MANUAL_SETTLED and discrepancy.payment:
            payment = discrepancy.payment
            if payment.status != PaymentStatus.SETTLED:
                payment.status = PaymentStatus.SETTLED
                payment.settled_at = timezone.now()
                payment.save(update_fields=['status', 'settled_at'])

    return discrepancy


def _batch_summary(batch: 'GatewaySettlementBatch', dry_run: bool) -> dict:
    return {
        'batch_id': batch.id,
        'provider': batch.provider,
        'settlement_date': batch.settlement_date,
        'total': batch.total_records,
        'matched': batch.matched_count,
        'missing': batch.missing_count,
        'mismatch': batch.mismatch_count,
        'dry_run': dry_run,
    }
