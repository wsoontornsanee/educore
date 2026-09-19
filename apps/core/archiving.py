"""Hot-set archiving for the high-write tables (NFR-009, spec/01 §8).

NFR-009 allows "partitioned by month OR archived by a cron job". Partitioning is not
available here: InnoDB partitioned tables cannot carry foreign keys, and
wallet_transactions / pos_transactions / gate_events all have them (pos_transactions is
also referenced by disputes); audit_events / domain_events would need the partition key
in their primary key, which Django's single-column PK cannot express. So every table is
archived instead: old rows move to a sibling `<table>_archive` table, never dropped
(AGENTS red line 2 — academic and financial data are never physically deleted).

Archive models are cloned from their source model, so a column added to the hot table
produces an archive migration on the next `makemigrations` instead of silently losing
data; `archive_batch` refuses to run if the two ever disagree.

Only the write path is covered. Code that reads history older than the retention window
(reporting aggregates, the audit viewer, DSAR erasure) still sees hot rows only.
"""
from dataclasses import dataclass
from datetime import timedelta

from django.apps import apps
from django.db import connection, models, transaction
from django.utils import timezone

ARCHIVE_SUFFIX = '_archive'


def build_archive_model(source):
    """Clone `source` into an archive model: same columns, no FKs, no uniques, no indexes.

    Foreign keys become plain BigIntegerFields (a partial archive must not constrain the
    hot tables), unique/index flags are stripped so archived rows can never collide with
    live ones, and generated columns are skipped (MySQL recomputes them, and INSERT
    cannot name them). Only `foundation_id` stays indexed, for tenant-scoped reads.
    """
    opts = source._meta
    attrs = {'__module__': source.__module__}
    for field in opts.concrete_fields:
        if getattr(field, 'generated', False):
            continue
        if field.primary_key:
            clone = models.BigIntegerField(primary_key=True, db_column=field.column)
        elif field.is_relation:
            clone = models.BigIntegerField(null=field.null, db_column=field.column)
        else:
            _, _, args, kwargs = field.deconstruct()
            for key in ('unique', 'db_index', 'auto_now', 'auto_now_add'):
                kwargs.pop(key, None)
            clone = field.__class__(*args, **kwargs)
        if field.attname == 'foundation_id':
            clone.db_index = True
        attrs[field.attname] = clone
    attrs['Meta'] = type('Meta', (), {
        'app_label': opts.app_label,
        'db_table': opts.db_table + ARCHIVE_SUFFIX,
        'default_permissions': (),
    })
    return type(f'{source.__name__}Archive', (models.Model,), attrs)


@dataclass(frozen=True)
class ArchivePolicy:
    model_label: str
    time_field: str
    retention_days: int
    extra_filter: models.Q | None = None

    @property
    def source(self):
        return apps.get_model(self.model_label)

    @property
    def archive(self):
        return apps.get_model(self.source._meta.app_label, f'{self.source.__name__}Archive')


# Retention is bounded below by what still reads the table live:
#   gate_events        — ATT-007 debounce and the gate feed look back hours, not months.
#   wallet/pos         — balances live in wallets.balance, not summed from history; the
#                        binding limits are the dispute window and offline replay dedup
#                        (idempotency_key / client_transaction_id leave the hot unique
#                        index with the row).
#   audit_events       — spec/01 retains audit for 7 years: the archive is that retention.
#   domain_events      — the admissions funnel report reads them by arbitrary date range.
POLICIES = (
    ArchivePolicy('attendance.GateEvent', 'occurred_at', 90),
    # POS first: a wallet transaction referenced by a hot POS row is not archivable yet.
    ArchivePolicy('wallet.POSTransaction', 'occurred_at', 400),
    ArchivePolicy(
        'wallet.WalletTransaction', 'occurred_at', 400,
        # Open reconciliation items are still worked from the hot table.
        extra_filter=~models.Q(status='RECONCILE_REQUIRED'),
    ),
    ArchivePolicy('core.AuditEvent', 'timestamp', 400),
    ArchivePolicy('core.DomainEvent', 'occurred_at', 400),
)


def eligible_rows(policy, retention_days=None, now=None):
    """Hot rows past retention that nothing still points at.

    Rows referenced by any foreign key (e.g. a POS sale under dispute) stay hot: the
    database would reject their deletion, and the referrer still needs them. Soft-deleted
    referrers count — the FK constraint does not know about `deleted_at`.
    """
    source = policy.source
    days = policy.retention_days if retention_days is None else retention_days
    cutoff = (now or timezone.now()) - timedelta(days=days)
    rows = source._base_manager.filter(**{f'{policy.time_field}__lt': cutoff})
    if policy.extra_filter is not None:
        rows = rows.filter(policy.extra_filter)
    # include_hidden: many referrers use related_name='+', which related_objects omits.
    for rel in source._meta.get_fields(include_hidden=True):
        if rel.auto_created and not rel.concrete and (rel.one_to_many or rel.one_to_one):
            referrers = rel.related_model._base_manager.filter(**{rel.field.name: models.OuterRef('pk')})
            rows = rows.exclude(models.Exists(referrers))
    return rows


def assert_archive_matches_source(policy):
    source_cols = {f.column for f in policy.source._meta.concrete_fields if not getattr(f, 'generated', False)}
    archive_cols = {f.column for f in policy.archive._meta.concrete_fields}
    if source_cols != archive_cols:
        raise RuntimeError(
            f"{policy.archive._meta.db_table} columns differ from {policy.source._meta.db_table} "
            f"(missing: {sorted(source_cols - archive_cols)}, extra: {sorted(archive_cols - source_cols)}); "
            "run makemigrations before archiving so no column is dropped."
        )


def archive_batch(policy, batch_size, retention_days=None, now=None):
    """Move up to `batch_size` eligible rows to the archive atomically; return how many.

    Progress is the shrinking eligible set itself: a crashed or `--limit`-capped run
    resumes exactly where it stopped, and a row is in exactly one of the two tables.
    """
    assert_archive_matches_source(policy)
    source, archive = policy.source, policy.archive
    qn = connection.ops.quote_name
    pk = qn(source._meta.pk.column)
    columns = ', '.join(qn(f.column) for f in archive._meta.concrete_fields)
    with transaction.atomic():
        ids = list(
            eligible_rows(policy, retention_days, now)
            .select_for_update(skip_locked=True)
            .order_by('pk')
            .values_list('pk', flat=True)[:batch_size]
        )
        if not ids:
            return 0
        marks = ', '.join(['%s'] * len(ids))
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {qn(archive._meta.db_table)} ({columns}) "
                f"SELECT {columns} FROM {qn(source._meta.db_table)} WHERE {pk} IN ({marks})",
                ids,
            )
            copied = cursor.rowcount
            cursor.execute(f"DELETE FROM {qn(source._meta.db_table)} WHERE {pk} IN ({marks})", ids)
            removed = cursor.rowcount
        if copied != len(ids) or removed != len(ids):
            raise RuntimeError(
                f"archive of {source._meta.db_table} moved {copied}/{removed} of {len(ids)} rows; rolled back"
            )
    return len(ids)
