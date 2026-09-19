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
from django.utils.translation import gettext as _

from apps.core.services import audit
from apps.finance.models import (
    DiscrepancyResolution,
    DiscrepancyType,
    GatewaySettlementBatch,
    Payment,
    PaymentDiscrepancy,
    PaymentStatus,
    SettlementBatchStatus,
)
from apps.finance.services.bank_statement_parser import (
    BankStatementParseError,
    parse_bank_statement,
)
from apps.finance.services.payment_providers import (
    PaymentGatewayError,
    get_payment_provider,
)

logger = logging.getLogger(__name__)

# Tolerance for amount comparison (IDR has no sub-rupiah, but allow 1-rupiah rounding)
AMOUNT_TOLERANCE = Decimal('1.00')


def _get_or_create_batch(provider_name: str, settlement_date: datetime.date, foundation_id: int):
    """Get-or-create the batch record (idempotent re-runs overwrite).
    Use all_tenants to bypass thread-local tenant scoping."""
    return GatewaySettlementBatch.all_tenants.get_or_create(
        foundation_id=foundation_id,
        provider=provider_name.upper(),
        settlement_date=settlement_date,
        defaults={'status': SettlementBatchStatus.PENDING},
    )


def _fail_batch(batch, error: str, provider_name: str, settlement_date, dry_run: bool) -> dict:
    if not dry_run:
        batch.status = SettlementBatchStatus.FAILED
        batch.error_message = error
        batch.save(update_fields=['status', 'error_message'])
    return {
        'batch_id': batch.id,
        'provider': provider_name,
        'settlement_date': settlement_date,
        'total': 0,
        'matched': 0,
        'missing': 0,
        'mismatch': 0,
        'error': error,
        'dry_run': dry_run,
    }


def _finalize_batch_with_records(
    batch,
    records: list,
    provider_name: str,
    settlement_date: datetime.date,
    foundation_id: int,
    dry_run: bool,
) -> dict:
    matched = 0
    missing = 0
    mismatch = 0
    errors = 0

    for record in records:
        result = _process_settlement_record(record, batch, foundation_id, dry_run=dry_run)
        if result == 'matched':
            matched += 1
        elif result == 'missing':
            missing += 1
        elif result == 'mismatch':
            mismatch += 1
        elif result == 'error':
            errors += 1

    total = len(records)
    batch_status = (
        SettlementBatchStatus.COMPLETED
        if missing == 0 and mismatch == 0 and errors == 0
        else SettlementBatchStatus.PARTIAL
    )

    if not dry_run:
        batch.status = batch_status
        batch.fetched_at = timezone.now()
        batch.total_records = total
        batch.matched_count = matched
        batch.missing_count = missing
        batch.mismatch_count = mismatch
        if errors:
            batch.error_message = f"{errors} record(s) matched but could not be settled; will retry on the next run."
        batch.save(update_fields=[
            'status', 'fetched_at', 'total_records',
            'matched_count', 'missing_count', 'mismatch_count', 'error_message',
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
        'errors': errors,
        'dry_run': dry_run,
    }


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
        'errors': int,   # matched payments whose settlement failed (retried next run)
        'dry_run': bool,
    }
    """
    provider = get_payment_provider(provider_name)

    batch, created = _get_or_create_batch(provider_name, settlement_date, foundation_id)

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
        return _fail_batch(batch, str(exc), provider_name, settlement_date, dry_run)

    return _finalize_batch_with_records(batch, records, provider_name, settlement_date, foundation_id, dry_run)


def reconcile_bank_statement_file(
    file_content,
    file_format: str,
    bank_code: str,
    settlement_date: datetime.date,
    foundation_id: int,
    dry_run: bool = False,
) -> dict:
    """Parse and reconcile a bank-provided MT940/CAMT.053 statement file
    (spec/14 CMP-026) — the file-based counterpart to reconcile_gateway_settlement,
    used for direct bank VA settlement that doesn't come through a gateway's API.

    The batch's 'provider' is recorded as 'BANK_<bank_code>' (e.g. 'BANK_BCA') so
    it appears in the same GatewaySettlementBatch/PaymentDiscrepancy listing as
    gateway-sourced batches, distinguishable by that prefix.
    """
    provider_name = f'BANK_{bank_code.upper()}'

    batch, created = _get_or_create_batch(provider_name, settlement_date, foundation_id)

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
        records = parse_bank_statement(file_content, file_format, bank_code=bank_code)
    except BankStatementParseError as exc:
        logger.error("Bank statement parse failed for %s/%s: %s", provider_name, settlement_date, exc)
        return _fail_batch(batch, str(exc), provider_name, settlement_date, dry_run)

    return _finalize_batch_with_records(batch, records, provider_name, settlement_date, foundation_id, dry_run)


def record_bank_sftp_pull_failure(
    bank_code: str,
    settlement_date: datetime.date,
    foundation_id: int,
    error: str,
) -> dict:
    """Record a failed automated SFTP pull attempt (CMP-024, CMP-026) as a FAILED
    GatewaySettlementBatch, so it's visible in the same batch listing gateway and
    manually-uploaded bank statement reconciliations use — an operator sees "this
    bank's daily pull didn't happen" without a separate tracking mechanism.
    """
    provider_name = f'BANK_SFTP_{bank_code.upper()}'
    batch, _created = _get_or_create_batch(provider_name, settlement_date, foundation_id)
    logger.error("Bank SFTP pull failed for %s/%s: %s", provider_name, settlement_date, error)
    return _fail_batch(batch, error, provider_name, settlement_date, dry_run=False)


@transaction.atomic
def _process_settlement_record(
    record: dict,
    batch: GatewaySettlementBatch,
    foundation_id: int,
    dry_run: bool,
) -> str:
    """Reconcile a single gateway settlement record.

    Returns one of: 'matched', 'missing', 'mismatch', 'error' (a matched
    payment whose settlement failed; left PENDING for the next run).
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

    # Auto-settle: run the full settlement path if the payment is not already
    # settled. A failure here is confined to this one record (own savepoint)
    # so a single bad payment cannot abort the rest of the batch; it stays
    # PENDING and is retried on the next run because the batch is not
    # COMPLETED.
    if not dry_run and payment.status != PaymentStatus.SETTLED:
        try:
            with transaction.atomic():
                _settle_payment(
                    payment, gateway_fee, foundation_id, actor_role='RECONCILIATION_AUTO', settled_at=settled_at,
                )
                # Clear any prior PENDING discrepancy for this external_id
                PaymentDiscrepancy.all_tenants.filter(
                    foundation_id=foundation_id,
                    external_id=external_id,
                    resolution=DiscrepancyResolution.PENDING,
                ).update(
                    resolution=DiscrepancyResolution.AUTO_SETTLED,
                    resolved_at=timezone.now(),
                )
        except Exception:
            logger.exception("AUTO_SETTLE_FAILED: external_id=%s payment_id=%s", external_id, payment.id)
            return 'error'

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
        raise ValueError(_('Pilihan penyelesaian tidak valid.'))

    discrepancy = PaymentDiscrepancy.all_tenants.get(
        id=discrepancy_id,
        foundation_id=foundation_id,
    )
    if discrepancy.resolution != DiscrepancyResolution.PENDING:
        raise ValueError(
            _("Selisih #%(id)s sudah berstatus %(resolution)s dan tidak dapat diselesaikan ulang.") % {
                'id': discrepancy_id, 'resolution': discrepancy.get_resolution_display(),
            }
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
            _settle_payment_manually(discrepancy, resolved_by)

        audit(
            action='finance.reconciliation.discrepancy_resolved',
            entity_type='PaymentDiscrepancy',
            entity_id=discrepancy.id,
            actor_id=str(resolved_by.id) if resolved_by else None,
            foundation_id=foundation_id,
            school_id=discrepancy.payment.school_id if discrepancy.payment else None,
            diff={'resolution': str(resolution), 'notes': notes},
        )

    return discrepancy


def _settle_payment(
    payment: Payment, gateway_fee, foundation_id: int, *, actor_role: str, actor_id: str = None, settled_at=None,
) -> None:
    """Settle `payment` through the SAME path a gateway webhook uses (receipt
    number, invoice allocation, balanced ledger journal, settlement events),
    instead of only flipping its status — which would mark money settled
    without it ever reaching AR or the double-entry ledger.

    The Payment's own recorded amount is what gets allocated; the gateway's
    reported fee is kept when there is one and net is derived, so
    `net + fee == amount` and the journal balances even when the gateway
    amount differs from the recorded one by up to AMOUNT_TOLERANCE.
    """
    from apps.finance.services.payments import _finalize_payment_settlement
    from educore.middleware.tenancy import tenant_context

    fee = gateway_fee or payment.fee or Decimal('0.00')
    with tenant_context(foundation_id):
        _finalize_payment_settlement(
            payment,
            fee=fee,
            net=payment.amount - fee,
            actor_role=actor_role,
            actor_id=actor_id,
            settled_at=settled_at,
        )


def _settle_payment_manually(discrepancy: 'PaymentDiscrepancy', resolved_by) -> None:
    """A finance user's MANUAL_SETTLED resolution: settle the discrepancy's
    linked Payment (an AMOUNT_MISMATCH therefore settles at the amount EduCore
    holds, not the gateway's)."""
    payment = discrepancy.payment
    if payment.status == PaymentStatus.SETTLED:
        return
    _settle_payment(
        payment, discrepancy.gateway_fee, discrepancy.foundation_id,
        actor_role='MANUAL_RECONCILIATION', actor_id=str(resolved_by.id) if resolved_by is not None else None,
    )


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
