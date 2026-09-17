"""Partner-facing read endpoints (spec/18 §7).

All views inherit PartnerAPIView (HMAC auth -> rate limit -> problem+json ->
idempotent mutations). Tenancy: the key's foundation is the request's tenant
context; a `school_ids`-scoped key additionally filters every school-bound
query to its allow-list (PVA-011).
"""
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response

from apps.attendance.models import AttendanceDay, AttendanceStatus
from apps.finance.models import Invoice, Payment, PaymentStatus
from apps.identity.models import School, Staff, Student
from apps.partners.errors import PartnerAPIError
from apps.partners.models import PartnerEvent, PayrollRun
from apps.partners.pagination import PartnerCursorPagination
from apps.partners.views import PartnerAPIView

# Endpoints a read-only-rotated key may still call (PVA-012: day-23 freeze
# applies to writes only — spec §9 AC#5).
READ_METHODS = ('GET', 'HEAD', 'OPTIONS')


class ScopeRequiredMixin:
    """Enforce the key's scope and (for writes) its read-only rotation state."""

    required_scope = None  # set per view

    def initial(self, request, *args, **kwargs):
        key = getattr(request, 'partner_key', None)
        if key is None:
            raise PartnerAPIError(401, 'SIGNATURE_INVALID', 'Not authenticated.')
        if self.required_scope and not key.has_scope(self.required_scope):
            raise PartnerAPIError(
                403, 'SCOPE_DENIED', f"Key lacks {self.required_scope}.")
        if request.method not in READ_METHODS and key.is_read_only:
            raise PartnerAPIError(
                403, 'KEY_READ_ONLY',
                'Key is in its 7-day pre-expiry read-only window (PVA-012).')
        super().initial(request, *args, **kwargs)

    def paginate(self, request, queryset, serialize):
        """serialize(rows) -> list of dicts. Returns the {results, next_cursor}
        envelope (spec/18 §4)."""
        paginator = PartnerCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(serialize(page))


def _school_filter(key, queryset, field='school_id'):
    """PVA-011: restrict to the key's per-school scope (empty = all schools)."""
    ids = key.school_ids or []
    if ids:
        return queryset.filter(**{f'{field}__in': ids})
    return queryset


class PartnerFoundationListView(ScopeRequiredMixin, PartnerAPIView):
    """GET /api/v1/partner/foundations (roster.read): the key's own foundation."""

    required_scope = 'roster.read'

    def get(self, request):
        from apps.identity.models import Foundation
        foundation = Foundation.objects.filter(pk=request.partner_key.foundation_id).first()
        if foundation is None:
            raise PartnerAPIError(404, 'NOT_FOUND', 'Foundation not found.')
        return Response({'results': [{
            'id': foundation.pk,
            'legal_name': foundation.legal_name,
            'brand_name': foundation.brand_name,
        }]})


class PartnerSchoolListView(ScopeRequiredMixin, PartnerAPIView):
    """GET /api/v1/partner/schools (roster.read)."""

    required_scope = 'roster.read'

    def get(self, request):
        qs = School.objects.filter(foundation_id=request.partner_key.foundation_id)
        qs = _school_filter(request.partner_key, qs).order_by('id')
        return self.paginate(request, qs, lambda rows, many=True: [
            {'id': s.pk, 'name': s.name, 'foundation_id': s.foundation_id}
            for s in rows
        ])


class PartnerStaffListView(ScopeRequiredMixin, PartnerAPIView):
    """GET /api/v1/partner/staff?school_id (roster.read).

    PVA-030: NIK fields are only returned when the key additionally holds
    roster.pii — never implied by plain roster.read.
    """

    required_scope = 'roster.read'

    def get(self, request):
        qs = Staff.objects.all().order_by('id')
        school_id = request.query_params.get('school_id')
        if school_id:
            if not school_id.isdigit():
                raise PartnerAPIError(400, 'VALIDATION_ERROR', 'school_id must be an integer.')
            sid = int(school_id)
            allowed = request.partner_key.school_ids or []
            if allowed and sid not in allowed:
                raise PartnerAPIError(403, 'SCOPE_DENIED', 'school_id is outside this key scope.')
            qs = qs.filter(school_id=sid)
        else:
            qs = _school_filter(request.partner_key, qs)
        qs = qs.select_related('person')

        include_pii = request.partner_key.has_scope('roster.pii')

        def serialize(rows):
            out = []
            for st in rows:
                item = {
                    'id': st.pk,
                    'school_id': st.school_id,
                    'full_name': st.person.full_name if st.person else '',
                    'nip': st.nip or None,
                    'status': st.status,
                }
                if include_pii and st.person:
                    item['nik'] = st.person.nik or None
                out.append(item)
            return out

        return self.paginate(request, qs, serialize)


class PartnerPayrollRunListView(ScopeRequiredMixin, PartnerAPIView):
    """GET /api/v1/partner/payroll/runs?period=YYYY-MM (payroll.read)."""

    required_scope = 'payroll.read'

    def get(self, request):
        qs = PayrollRun.objects.all().order_by('-id')
        qs = _school_filter(request.partner_key, qs)
        period = request.query_params.get('period')
        if period:
            qs = qs.filter(period=period)

        def serialize(rows):
            return [{
                'id': r.pk,
                'school_id': r.school_id,
                'period': r.period,
                'status': r.status,
                'gross': {'amount': str(r.gross_amount), 'currency': r.currency},
                'deduction': {'amount': str(r.deduction_amount), 'currency': r.currency},
                'net': {'amount': str(r.net_amount), 'currency': r.currency},
                'approved_at': r.approved_at.isoformat() if r.approved_at else None,
                'acknowledged_at': r.acknowledged_at.isoformat() if r.acknowledged_at else None,
                'acknowledgement_locked': r.acknowledgement_locked,
            } for r in rows]

        return self.paginate(request, qs, serialize)


class PartnerPayrollRunLinesView(ScopeRequiredMixin, PartnerAPIView):
    """GET /api/v1/partner/payroll/runs/:id/lines (payroll.read)."""

    required_scope = 'payroll.read'

    def get(self, request, run_id):
        key = request.partner_key
        qs = _school_filter(key, PayrollRun.objects.all())
        run = get_object_or_404(qs, pk=run_id)
        lines = run.lines.all().order_by('id')
        return Response({'results': [{
            'id': ln.pk,
            'staff_id': ln.staff_id,
            'staff_name': ln.staff_name,
            'gross': {'amount': str(ln.gross_amount), 'currency': run.currency},
            'deduction': {'amount': str(ln.deduction_amount), 'currency': run.currency},
            'net': {'amount': str(ln.net_amount), 'currency': run.currency},
        } for ln in lines]})


class PartnerPayrollAcknowledgeView(ScopeRequiredMixin, PartnerAPIView):
    """POST /api/v1/partner/payroll/runs/:id/acknowledge (payroll.write, PVA-032).

    409 PAYROLL_RUN_LOCKED once the run has moved past the acknowledgement
    window (already acknowledged, or past acknowledge_deadline).
    """

    required_scope = 'payroll.write'

    def post(self, request, run_id):
        from django.utils import timezone

        key = request.partner_key
        qs = _school_filter(key, PayrollRun.objects.all())
        run = get_object_or_404(qs, pk=run_id)

        if run.status == PayrollRun.STATUS_ACKNOWLEDGED:
            raise PartnerAPIError(409, 'PAYROLL_RUN_LOCKED', 'Run has already been acknowledged.')
        if run.acknowledgement_locked:
            raise PartnerAPIError(409, 'PAYROLL_RUN_LOCKED',
                                  'Run has moved past the acknowledgement window.')

        run.status = PayrollRun.STATUS_ACKNOWLEDGED
        run.acknowledged_at = timezone.now()
        run.acknowledged_by = key.key_id
        run.save(update_fields=['status', 'acknowledged_at', 'acknowledged_by',
                                'updated_at', 'updated_by'])

        from apps.core.services import audit
        audit(
            action='payroll.run.acknowledged',
            entity_type='PayrollRun',
            entity_id=run.pk,
            foundation_id=run.foundation_id,
            school_id=run.school_id,
            actor_id=key.key_id,
            role='PARTNER_API',
        )
        return Response({
            'id': run.pk,
            'status': run.status,
            'acknowledged_at': run.acknowledged_at.isoformat(),
            'acknowledged_by': run.acknowledged_by,
        })


class PartnerInvoiceListView(ScopeRequiredMixin, PartnerAPIView):
    """GET /api/v1/partner/invoices?status=&school_id= (finance.read)."""

    required_scope = 'finance.read'

    def get(self, request):
        qs = Invoice.objects.all().order_by('-id')
        qs = _school_filter(request.partner_key, qs)
        status_param = request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param.upper())

        def serialize(rows):
            return [{
                'id': inv.pk,
                'number': inv.number,
                'school_id': inv.school_id,
                'student_id': inv.student_id,
                'period': inv.period,
                'status': inv.status,
                'issue_date': inv.issue_date.isoformat(),
                'due_date': inv.due_date.isoformat(),
                'total': {'amount': str(inv.total), 'currency': inv.currency},
                'paid': {'amount': str(inv.paid), 'currency': inv.currency},
                'balance_due': {'amount': str(inv.balance_due), 'currency': inv.currency},
            } for inv in rows]

        return self.paginate(request, qs, serialize)


class PartnerAttendanceDailyView(ScopeRequiredMixin, PartnerAPIView):
    """GET /api/v1/partner/attendance/daily?date=YYYY-MM-DD (attendance.read).

    PVA-031: returns only the six AttendanceStatus values; gate photos are
    never exposed through this API.
    """

    required_scope = 'attendance.read'

    def get(self, request):
        from datetime import date as date_cls
        date_param = request.query_params.get('date')
        try:
            day = date_cls.fromisoformat(date_param) if date_param else None
        except ValueError:
            raise PartnerAPIError(400, 'VALIDATION_ERROR', 'date must be ISO YYYY-MM-DD.')
        if day is None:
            raise PartnerAPIError(400, 'VALIDATION_ERROR', 'date query parameter is required.')

        qs = AttendanceDay.objects.filter(date=day).order_by('id')
        qs = _school_filter(request.partner_key, qs)
        qs = qs.select_related('student', 'student__person')

        def serialize(rows):
            return [{
                'student_id': rec.student_id,
                'school_id': rec.school_id,
                'date': rec.date.isoformat(),
                'status': rec.status,
                'first_in_at': rec.first_in_at.isoformat() if rec.first_in_at else None,
                'last_out_at': rec.last_out_at.isoformat() if rec.last_out_at else None,
            } for rec in rows]

        return self.paginate(request, qs, serialize)


class PartnerEventListView(ScopeRequiredMixin, PartnerAPIView):
    """GET /api/v1/partner/events?since=<event-id> — webhook fallback (§6).

    Returns events NEWER than the `since` cursor (events the partner may have
    missed while deliveries failed), newest-first within the page. Any scope:
    events are foundation-wide for the key. Minimum polling interval is 30
    seconds per key (§8).
    """

    def initial(self, request, *args, **kwargs):
        # Any scope — key existence and signature validity are the only gates
        # (§7: "any scope").
        key = getattr(request, 'partner_key', None)
        if key is None:
            raise PartnerAPIError(401, 'SIGNATURE_INVALID', 'Not authenticated.')

        from django.core.cache import cache
        cache_key = f"partner_poll:{key.key_id}"
        last = cache.get(cache_key)
        if last is not None:
            elapsed = _now_ts() - last
            if elapsed < 30:
                raise PartnerAPIError(
                    429, 'RATE_LIMITED',
                    f'Polling interval must be at least 30s (retry in {int(30 - elapsed)}s).')
        cache.set(cache_key, _now_ts(), 60)
        super().initial(request, *args, **kwargs)

    def get(self, request):
        qs = PartnerEvent.objects.filter(
            foundation_id=request.partner_key.foundation_id,
        ).order_by('-id')

        since = request.query_params.get('since')
        if since and since.isdigit():
            # Only events strictly newer than what the partner already has.
            qs = qs.filter(id__gt=int(since))
        event_type = request.query_params.get('event_type')
        if event_type:
            qs = qs.filter(event_type=event_type)

        paginator = PartnerCursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response([{
            'id': ev.pk,
            'event_type': ev.event_type,
            'occurred_at': ev.created_at.isoformat(),
            'status': ev.status,
            'payload': ev.payload,
        } for ev in page])


class PartnerWebhookRegisterView(ScopeRequiredMixin, PartnerAPIView):
    """POST /api/v1/partner/webhooks — register the receiving endpoint (§7,
    any scope). Exactly one active endpoint per environment; the signing
    secret is returned ONCE in this response and never again."""

    def post(self, request):
        body = self.get_json_body(request)
        url = (body.get('url') or '').strip()
        environment = (body.get('environment') or 'LIVE').upper()
        if not url:
            raise PartnerAPIError(400, 'VALIDATION_ERROR', 'url is required.')
        if environment not in ('LIVE', 'TEST'):
            raise PartnerAPIError(400, 'VALIDATION_ERROR', 'environment must be LIVE or TEST.')

        from apps.partners.services import register_webhook_endpoint
        try:
            endpoint, secret = register_webhook_endpoint(
                foundation_id=request.partner_key.foundation_id,
                url=url,
                environment=environment,
                created_by=request.partner_key.key_id,
            )
        except ValueError as exc:
            raise PartnerAPIError(400, 'VALIDATION_ERROR', str(exc))

        from apps.core.services import audit
        audit(
            action='integration.webhook.registered',
            entity_type='PartnerWebhookEndpoint',
            entity_id=endpoint.pk,
            foundation_id=endpoint.foundation_id,
            actor_id=request.partner_key.key_id,
            role='PARTNER_API',
            diff={'url': endpoint.url, 'environment': endpoint.environment},
        )
        return Response({
            'id': endpoint.pk,
            'url': endpoint.url,
            'environment': endpoint.environment,
            'signing_secret': secret,
            'note': 'Store this signing secret now; it is never shown again.',
        }, status=status.HTTP_201_CREATED)


def _now_ts():
    import time
    return int(time.time())
