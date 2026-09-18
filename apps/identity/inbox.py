"""Per-user task inbox ("Kotak tugas") for the web console.

A read-only aggregation of the pending work items a user can actually act
on, drawn from workflows other apps already own (finance approvals and
write-offs, absence requests, report-card review, timetable substitutions).
Nothing here defines a new workflow or state: each source is a filtered
query over an existing model, gated by the SAME predicate that gates the
corresponding decide endpoint:

  - discount/waiver/refund approvals and invoice write-offs: foundation
    admin only (the services' own is_foundation_admin check — a user who
    cannot decide the item is not shown it as a task);
  - absence requests: attendance.write, limited to the schools the user
    holds that permission in;
  - report cards pending review: school_config.write (the approve action's
    permission), limited to the same school scope;
  - timetable substitutions: personal — the ones assigned to the user's own
    Staff row, no permission needed.

Every source returns (total, items): total is a COUNT over the pending
rows and items only the first `limit` of them, so a long queue never loads
every row — and the nav badge (get_inbox_count) reuses the exact same
sources with limit=0, so its number can never drift from the page's.

Absence requests, report cards and substitutions can also be decided in
place (perform_inbox_action). The item being acted on is looked up through
the SAME scoped queryset that lists it, so "can act" and "is shown" cannot
drift apart, and the decision itself always goes through the owning app's
service (state check, side effects, audit stay in one place). Finance
approvals and write-offs stay read-only here: money-moving decisions belong
to the Keuangan console.

Other apps' models are imported lazily inside each source (the same
leaf-to-leaf pattern apps.foundation.approvals uses) so apps.identity keeps
no import-time dependency on them.
"""
from datetime import datetime
from typing import Callable, NamedTuple

from django.core.exceptions import ValidationError
from django.utils import formats, timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _, gettext_lazy as _lazy

from educore.middleware.tenancy import tenant_context

from .models import RoleAssignment, Staff
from .rbac import SCOPE_SCHOOL, get_user_permissions, is_foundation_admin

# Bounds each section's rendered rows; the section's `total` still reports
# the real pending count so a long queue is visible as "N menunggu", not
# silently truncated.
ITEMS_PER_SECTION = 25


class InboxAction(NamedTuple):
    key: str
    label: str


class InboxItem(NamedTuple):
    title: str
    detail: str
    requested_at: datetime
    # Set only on items that can be decided in place (see _ACTIONS).
    kind: str = ''
    pk: int = 0
    actions: tuple = ()
    note_hint: str = ''


def _school_scope(user, foundation_id, permission):
    """None = `permission` held foundation-wide; otherwise the (possibly
    empty) set of school ids the user holds it in."""
    if permission in get_user_permissions(user, foundation_id, school_id=None):
        return None
    assigned = RoleAssignment.all_tenants.filter(
        foundation_id=foundation_id, user=user, scope_type=SCOPE_SCHOOL, deleted_at__isnull=True,
    ).values_list('scope_id', flat=True).distinct()
    return {sid for sid in assigned if permission in get_user_permissions(user, foundation_id, school_id=sid)}


def _in_scope(qs, scope, school_field):
    return qs if scope is None else qs.filter(**{f'{school_field}__in': scope})


def _money(amount, currency):
    return f"{currency} {formats.number_format(amount, decimal_pos=0, force_grouping=True)}"


def _finance_approvals(user, foundation_id, limit):
    if not is_foundation_admin(user, foundation_id):
        return 0, []
    from apps.foundation.approvals import (
        APPROVAL_TYPE_DISCOUNT, APPROVAL_TYPE_REFUND, APPROVAL_TYPE_WAIVER, list_foundation_approvals,
    )

    labels = {
        APPROVAL_TYPE_DISCOUNT: _("Diskon"),
        APPROVAL_TYPE_WAIVER: _("Keringanan"),
        APPROVAL_TYPE_REFUND: _("Pengembalian dana"),
    }
    rows = list_foundation_approvals(foundation_id, status='pending')
    items = []
    for row in rows[:limit]:
        if row['type'] == APPROVAL_TYPE_DISCOUNT:
            amount = f"{row['amount']}%"
        else:
            amount = _money(row['amount'], row['currency'])
        items.append(InboxItem(
            title=f"{labels[row['type']]} — {row['student_name']}",
            detail=f"{amount} · {row['reason']}",
            requested_at=parse_datetime(row['requested_at']),
        ))
    return len(rows), items


def _write_offs(user, foundation_id, limit):
    if not is_foundation_admin(user, foundation_id):
        return 0, []
    from apps.finance.models import InvoiceWriteOffRequest, InvoiceWriteOffStatus

    qs = InvoiceWriteOffRequest.objects.filter(
        foundation_id=foundation_id, status=InvoiceWriteOffStatus.PENDING, deleted_at__isnull=True,
    ).select_related('invoice__student__person').order_by('-created_at')
    return qs.count(), [
        InboxItem(
            title=_("Hapus buku %(number)s — %(student)s") % {
                'number': r.invoice.number, 'student': r.invoice.student.person.full_name,
            },
            detail=f"{_money(r.amount, r.currency)} · {r.reason}",
            requested_at=r.created_at,
        )
        for r in qs[:limit]
    ]


class InboxActionError(Exception):
    """A decision the owning service refused (already decided, missing reason…)."""


def _absence_request_qs(user, foundation_id):
    scope = _school_scope(user, foundation_id, 'attendance.write')
    if scope is not None and not scope:
        return None
    from apps.attendance.models import AbsenceRequest, AbsenceRequestStatus

    qs = AbsenceRequest.objects.filter(
        foundation_id=foundation_id, status=AbsenceRequestStatus.PENDING, deleted_at__isnull=True,
    ).select_related('student__person').order_by('-created_at')
    return _in_scope(qs, scope, 'school_id')


def _report_card_qs(user, foundation_id):
    scope = _school_scope(user, foundation_id, 'school_config.write')
    if scope is not None and not scope:
        return None
    from apps.academic.models import ReportCard, ReportCardStatus

    qs = ReportCard.objects.filter(
        foundation_id=foundation_id, status=ReportCardStatus.PENDING_REVIEW, is_current=True,
        deleted_at__isnull=True,
    ).select_related('student__person', 'class_group', 'term').order_by('-created_at')
    return _in_scope(qs, scope, 'class_group__school_id')


def _substitution_qs(user, foundation_id):
    staff_ids = list(Staff.all_tenants.filter(
        foundation_id=foundation_id, user=user, deleted_at__isnull=True,
    ).values_list('id', flat=True))
    if not staff_ids:
        return None
    from apps.academic.models import SubstitutionStatus, TimetableSubstitution

    return TimetableSubstitution.objects.filter(
        foundation_id=foundation_id, substitute_teacher_id__in=staff_ids, status=SubstitutionStatus.PENDING,
        date__gte=timezone.localdate(), deleted_at__isnull=True,
    ).select_related(
        'slot__class_subject__class_group', 'slot__class_subject__subject', 'original_teacher__person',
    ).order_by('date', 'slot__period_no')


def _decide_absence(decide, obj, user, note):
    try:
        decide(obj, decided_by=user, note=note)
    except ValidationError as exc:
        raise InboxActionError(exc.messages[0])


def _approve_absence(obj, user, note):
    from apps.attendance.services import approve_absence_request
    _decide_absence(approve_absence_request, obj, user, note)


def _reject_absence(obj, user, note):
    from apps.attendance.services import reject_absence_request
    _decide_absence(reject_absence_request, obj, user, note)


def _approve_report_card(obj, user, note):
    from apps.academic.services import ReportCardStateError, approve_report_card
    try:
        approve_report_card(obj, actor=user)
    except ReportCardStateError as exc:
        raise InboxActionError(str(exc))


def _accept_substitution(obj, user, note):
    from apps.academic.services import accept_substitution
    accept_substitution(obj, actor=user)


def _decline_substitution(obj, user, note):
    from apps.academic.services import SubstitutionDeclineReasonRequiredError, decline_substitution
    try:
        decline_substitution(obj, reason=note, actor=user)
    except SubstitutionDeclineReasonRequiredError as exc:
        raise InboxActionError(str(exc))


class _ActionSpec(NamedTuple):
    queryset: Callable  # (user, foundation_id) -> scoped pending queryset, or None
    note_hint: str      # placeholder for the shared note field; '' = no note field
    handlers: dict      # action key -> (button label, handler(obj, user, note))


# Sections that can be decided in place, keyed by the `kind` carried on each
# item and used in the action URL.
_ACTIONS = {
    'absence_request': _ActionSpec(_absence_request_qs, _lazy("Catatan (opsional)"), {
        'approve': (_lazy("Setujui"), _approve_absence),
        'reject': (_lazy("Tolak"), _reject_absence),
    }),
    'report_card': _ActionSpec(_report_card_qs, '', {
        'approve': (_lazy("Setujui"), _approve_report_card),
    }),
    'substitution': _ActionSpec(_substitution_qs, _lazy("Alasan (wajib jika menolak)"), {
        'accept': (_lazy("Terima"), _accept_substitution),
        'decline': (_lazy("Tolak"), _decline_substitution),
    }),
}


def _action_fields(kind, pk):
    spec = _ACTIONS[kind]
    return {
        'kind': kind,
        'pk': pk,
        'actions': tuple(InboxAction(key, label) for key, (label, _handler) in spec.handlers.items()),
        'note_hint': spec.note_hint,
    }


def _absence_requests(user, foundation_id, limit):
    qs = _absence_request_qs(user, foundation_id)
    if qs is None:
        return 0, []
    return qs.count(), [
        InboxItem(
            title=f"{r.student.person.full_name} — {r.get_type_display()}",
            detail=f"{r.date_from:%d/%m/%Y} – {r.date_to:%d/%m/%Y} · {r.reason}",
            requested_at=r.created_at,
            **_action_fields('absence_request', r.pk),
        )
        for r in qs[:limit]
    ]


def _report_cards(user, foundation_id, limit):
    qs = _report_card_qs(user, foundation_id)
    if qs is None:
        return 0, []
    return qs.count(), [
        InboxItem(
            title=_("Rapor %(student)s — %(class_group)s") % {
                'student': r.student.person.full_name, 'class_group': r.class_group.name,
            },
            detail=r.term.name,
            requested_at=r.created_at,
            **_action_fields('report_card', r.pk),
        )
        for r in qs[:limit]
    ]


def _substitutions(user, foundation_id, limit):
    qs = _substitution_qs(user, foundation_id)
    if qs is None:
        return 0, []
    return qs.count(), [
        InboxItem(
            title=_("Pengganti %(subject)s — %(class_group)s") % {
                'subject': s.slot.class_subject.subject.name, 'class_group': s.slot.class_subject.class_group.name,
            },
            detail=_("%(date)s · jam ke-%(period)s · menggantikan %(teacher)s") % {
                'date': f"{s.date:%d/%m/%Y}", 'period': s.slot.period_no,
                'teacher': s.original_teacher.person.full_name,
            },
            requested_at=s.created_at,
            **_action_fields('substitution', s.pk),
        )
        for s in qs[:limit]
    ]


def perform_inbox_action(user, foundation_id, kind, pk, action, note=''):
    """Decide one inbox item in place; returns True if applied.

    Raises KeyError for an unknown kind/action (a tampered URL), and
    InboxActionError when the owning service refuses. Returns False when the
    item is not (or no longer) in the user's own actionable set — already
    decided by someone else, out of their school scope, another tenant's."""
    spec = _ACTIONS[kind]
    _label, handler = spec.handlers[action]
    with tenant_context(foundation_id):
        qs = spec.queryset(user, foundation_id)
        obj = qs.filter(pk=pk).first() if qs is not None else None
        if obj is None:
            return False
        handler(obj, user, (note or '').strip())
    return True


# (section id, label, source). List order is the render order.
_SOURCES = [
    ('finance_approvals', _lazy("Persetujuan keuangan"), _finance_approvals),
    ('write_offs', _lazy("Penghapusbukuan piutang"), _write_offs),
    ('absence_requests', _lazy("Izin & sakit siswa"), _absence_requests),
    ('report_cards', _lazy("Rapor menunggu tinjauan"), _report_cards),
    ('substitutions', _lazy("Permintaan mengganti kelas"), _substitutions),
]


def get_inbox_for_user(user, foundation_id):
    """Non-empty inbox sections for `user`, each {id, label, total, items}.

    `items` holds at most ITEMS_PER_SECTION rows; `total` is the full
    pending count. Empty sections are omitted, so an empty list means the
    user has nothing waiting on them."""
    sections = []
    with tenant_context(foundation_id):
        for section_id, label, source in _SOURCES:
            total, items = source(user, foundation_id, ITEMS_PER_SECTION)
            if total:
                sections.append({'id': section_id, 'label': label, 'total': total, 'items': items})
    return sections


def get_inbox_count(user, foundation_id):
    """Total pending items across every source, for the nav badge."""
    with tenant_context(foundation_id):
        return sum(source(user, foundation_id, 0)[0] for _id, _label, source in _SOURCES)
