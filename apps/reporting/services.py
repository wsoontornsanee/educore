from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from apps.identity.models import School
from apps.reporting.models import RptWalletActivity

DASHBOARD_LOOKBACK_DAYS = 2


def refresh_wallet_activity(scope: str, since=None) -> dict:
    """spec/15 §2: rebuild rpt_wallet_activity, one row per school per day.

    scope='dashboard': only the last DASHBOARD_LOOKBACK_DAYS days (incremental,
    every 5 minutes per spec/15 §2). scope='full': every day that has any wallet
    activity at all (nightly rebuild). `since` overrides the computed start date
    for either scope, mainly for tests.
    """
    from apps.wallet.models import POSTransaction, POSTransactionStatus, WalletTransaction, WalletTransactionStatus, WalletTransactionType

    now = timezone.now()
    today = now.date()

    if since is not None:
        start_date = since
    elif scope == 'dashboard':
        start_date = today - timedelta(days=DASHBOARD_LOOKBACK_DAYS)
    else:
        earliest_topup = WalletTransaction.all_tenants.filter(
            type=WalletTransactionType.TOPUP, deleted_at__isnull=True,
        ).order_by('occurred_at').values_list('occurred_at', flat=True).first()
        start_date = earliest_topup.date() if earliest_topup else today

    rows_written = 0
    for school in School.all_tenants.filter(deleted_at__isnull=True):
        topups_by_day = dict(
            WalletTransaction.all_tenants.filter(
                foundation_id=school.foundation_id,
                wallet__student__school=school,
                type=WalletTransactionType.TOPUP,
                status__in=[WalletTransactionStatus.COMPLETED, WalletTransactionStatus.RECONCILE_REQUIRED],
                occurred_at__date__gte=start_date,
                deleted_at__isnull=True,
            ).values_list('occurred_at__date').annotate(total=Sum('amount')).values_list('occurred_at__date', 'total')
        )
        purchases_by_day = dict(
            POSTransaction.all_tenants.filter(
                foundation_id=school.foundation_id,
                merchant__school=school,
                status=POSTransactionStatus.COMPLETED,
                occurred_at__date__gte=start_date,
                deleted_at__isnull=True,
            ).values_list('occurred_at__date').annotate(total=Sum('subtotal')).values_list('occurred_at__date', 'total')
        )
        commission_by_day = dict(
            POSTransaction.all_tenants.filter(
                foundation_id=school.foundation_id,
                merchant__school=school,
                status=POSTransactionStatus.COMPLETED,
                occurred_at__date__gte=start_date,
                deleted_at__isnull=True,
            ).values_list('occurred_at__date').annotate(total=Sum('commission')).values_list('occurred_at__date', 'total')
        )
        active_wallets_by_day = dict(
            WalletTransaction.all_tenants.filter(
                foundation_id=school.foundation_id,
                wallet__student__school=school,
                status__in=[WalletTransactionStatus.COMPLETED, WalletTransactionStatus.RECONCILE_REQUIRED],
                occurred_at__date__gte=start_date,
                deleted_at__isnull=True,
            ).values_list('occurred_at__date').annotate(count=Count('wallet_id', distinct=True)).values_list('occurred_at__date', 'count')
        )

        days = set(topups_by_day) | set(purchases_by_day) | set(commission_by_day) | set(active_wallets_by_day)
        for day in days:
            RptWalletActivity.all_tenants.update_or_create(
                foundation_id=school.foundation_id,
                school=school,
                date=day,
                currency='IDR',
                defaults={
                    'topups': topups_by_day.get(day, Decimal('0.00')),
                    'purchases': purchases_by_day.get(day, Decimal('0.00')),
                    'commission': commission_by_day.get(day, Decimal('0.00')),
                    'active_wallets': active_wallets_by_day.get(day, 0),
                    'computed_at': now,
                },
            )
            rows_written += 1

    return {'rows_written': rows_written, 'start_date': str(start_date), 'scope': scope}
