"""Foundation-admin management surface for the partner API (spec/18).

Mounted at /api/v1/partner-admin/. Session/JWT authenticated inside the
normal DRF pipeline (NOT partner HMAC), gated by the existing
is_foundation_admin authority — no new RBAC keys minted, consistent with
this codebase's pattern of reusing established permission gates for
configuration surfaces.

This is the surface that actually operates the partner API: without it
neither keys nor payroll runs could ever exist.
"""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.services import audit
from apps.identity.models import School
from apps.identity.rbac import is_foundation_admin
from apps.partners.errors import PartnerAPIError, problem_from_exception, problem_response
from apps.partners.models import PartnerApiKey, PayrollRun, PayrollRunLine
from apps.partners import services
from educore.middleware.tenancy import get_current_foundation_id

from decimal import Decimal

ADMIN_KEY_FIELDS = ('id', 'key_id', 'label', 'scopes', 'school_ids', 'ip_allowlist',
                    'status', 'rotated_from_id', 'read_only_at', 'expires_at',
                    'created_at', 'last_used_at')


def _key_json(key: PartnerApiKey) -> dict:
    data = {}
    for f in ADMIN_KEY_FIELDS:
        value = getattr(key, f)
        if hasattr(value, 'isoformat'):
            data[f] = value.isoformat()
        else:
            data[f] = value
    data['is_read_only'] = key.is_read_only
    data['is_active'] = key.is_active
    return data


class PartnerAdminMixin(APIView):
    """Shared fail-closed foundation-admin gate + problem+json rendering.

    The gate runs in initial() — AFTER DRF has resolved request.user (its
    lazy authentication runs on first request.user access inside
    perform_authentication, which initial() triggers).
    """

    permission_classes = [permissions.IsAuthenticated]

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except PartnerAPIError as exc:
            return problem_response(request, exc)
        except Exception as exc:
            return problem_from_exception(request, exc)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        foundation_id = get_current_foundation_id() or getattr(
            request.user, 'foundation_id', None)
        if not foundation_id or not is_foundation_admin(request.user, foundation_id):
            raise PartnerAPIError(403, 'FORBIDDEN', 'Foundation admin authority required.')
        request.foundation_id = foundation_id


class PartnerAdminKeyListView(PartnerAdminMixin, APIView):
    """GET/POST /api/v1/partner-admin/keys."""

    def get(self, request):
        keys = PartnerApiKey.objects.filter(
            foundation_id=request.foundation_id).order_by('-id')
        return Response({'results': [_key_json(k) for k in keys]})

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        label = (body.get('label') or '').strip()
        scopes = body.get('scopes') or []
        if not label:
            raise PartnerAPIError(400, 'VALIDATION_ERROR', 'label is required.')
        try:
            key, secret = services.issue_api_key(
                foundation_id=request.foundation_id,
                label=label,
                scopes=scopes,
                school_ids=body.get('school_ids') or [],
                ip_allowlist=body.get('ip_allowlist') or [],
                created_by=str(request.user.pk),
            )
        except services.KeyLimitExceeded as exc:
            raise PartnerAPIError(409, 'KEY_LIMIT_EXCEEDED', str(exc))
        except ValueError as exc:
            raise PartnerAPIError(400, 'VALIDATION_ERROR', str(exc))

        audit(
            action='integration.key.issued',
            entity_type='PartnerApiKey',
            entity_id=key.key_id,
            foundation_id=request.foundation_id,
            actor_id=str(request.user.pk),
            diff={'label': label, 'scopes': scopes},
        )
        data = _key_json(key)
        data['secret'] = secret
        data['note'] = 'Store this secret now; it is never shown again.'
        return Response(data, status=status.HTTP_201_CREATED)


class PartnerAdminKeyDetailView(PartnerAdminMixin, APIView):
    """/api/v1/partner-admin/keys/:key_id — GET / POST rotate / DELETE revoke."""

    def _get_key(self, request, key_id):
        return get_object_or_404(
            PartnerApiKey, key_id=key_id, foundation_id=request.foundation_id)

    def get(self, request, key_id):
        return Response(_key_json(self._get_key(request, key_id)))

    def post(self, request, key_id):
        """Rotate: issue successor, start the 23d/30d countdown on this key."""
        old_key = self._get_key(request, key_id)
        try:
            new_key, secret = services.rotate_api_key(
                old_key, created_by=str(request.user.pk))
        except services.KeyLimitExceeded as exc:
            raise PartnerAPIError(409, 'KEY_LIMIT_EXCEEDED', str(exc))
        data = _key_json(new_key)
        data['secret'] = secret
        data['note'] = 'Store this secret now; it is never shown again.'
        return Response(data, status=status.HTTP_201_CREATED)

    def delete(self, request, key_id):
        """Revoke (soft): key stops working immediately; row is never deleted."""
        key = self._get_key(request, key_id)
        services.revoke_api_key(key, revoked_by=str(request.user.pk))
        return Response(_key_json(key))


class PartnerAdminPayrollRunListCreateView(PartnerAdminMixin, APIView):
    """GET/POST /api/v1/partner-admin/payroll/runs.

    POST creates a DRAFT run with line items. Money arrives as
    {"amount": "1500000.00", "currency": "IDR"} strings (CUR-026).
    """

    def get(self, request):
        runs = PayrollRun.objects.filter(
            foundation_id=request.foundation_id).order_by('-id')
        period = request.query_params.get('period')
        if period:
            runs = runs.filter(period=period)
        return Response({'results': [_run_json(r) for r in runs]})

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        school_id = body.get('school_id')
        period = body.get('period') or ''
        lines = body.get('lines') or []
        if not school_id or not period:
            raise PartnerAPIError(400, 'VALIDATION_ERROR', 'school_id and period are required.')
        if len(period) != 7 or period[4] != '-':
            raise PartnerAPIError(400, 'VALIDATION_ERROR', 'period must be YYYY-MM.')
        if not lines:
            raise PartnerAPIError(400, 'VALIDATION_ERROR', 'At least one line is required.')

        school = get_object_or_404(
            School, pk=school_id, foundation_id=request.foundation_id)
        currency = body.get('currency') or 'IDR'

        try:
            parsed_lines, gross, deduction = _parse_lines(lines, currency)
        except (KeyError, ValueError, TypeError, ArithmeticError):
            raise PartnerAPIError(
                400, 'VALIDATION_ERROR',
                'Each line needs staff_id, staff_name, gross.amount, deduction.amount '
                'as decimal strings; all amounts same currency.')

        from django.db import transaction
        with transaction.atomic():
            run = PayrollRun.objects.create(
                foundation_id=request.foundation_id,
                school=school,
                period=period,
                currency=currency,
                gross_amount=gross,
                deduction_amount=deduction,
                net_amount=gross - deduction,
                created_by=str(request.user.pk),
            )
            PayrollRunLine.objects.bulk_create([
                PayrollRunLine(
                    foundation_id=request.foundation_id,
                    run=run,
                    staff_id=ln['staff_id'],
                    staff_name=ln['staff_name'],
                    gross_amount=ln['gross'],
                    deduction_amount=ln['deduction'],
                    net_amount=ln['gross'] - ln['deduction'],
                    created_by=str(request.user.pk),
                ) for ln in parsed_lines
            ])

        audit(
            action='payroll.run.created',
            entity_type='PayrollRun',
            entity_id=run.pk,
            foundation_id=request.foundation_id,
            school_id=school.pk,
            actor_id=str(request.user.pk),
        )
        return Response(_run_json(run), status=status.HTTP_201_CREATED)


def _parse_lines(lines, currency):
    parsed = []
    gross_total = Decimal('0.00')
    deduction_total = Decimal('0.00')
    for ln in lines:
        staff_id = int(ln['staff_id'])
        staff_name = str(ln['staff_name'])[:128]
        gross_ln = ln.get('gross') or {}
        ded_ln = ln.get('deduction') or {}
        line_currency = ded_ln.get('currency', gross_ln.get('currency', currency))
        if line_currency != currency:
            raise PartnerAPIError(
                422, 'CURRENCY_MISMATCH',
                f'Line currency {line_currency} does not match run currency {currency}.')
        gross_val = Decimal(str(gross_ln.get('amount', '0')))
        ded_val = Decimal(str(ded_ln.get('amount', '0')))
        gross_total += gross_val
        deduction_total += ded_val
        parsed.append({'staff_id': staff_id, 'staff_name': staff_name,
                       'gross': gross_val, 'deduction': ded_val})
    return parsed, gross_total, deduction_total


def _run_json(run: PayrollRun) -> dict:
    return {
        'id': run.pk,
        'school_id': run.school_id,
        'period': run.period,
        'status': run.status,
        'currency': run.currency,
        'gross': {'amount': str(run.gross_amount), 'currency': run.currency},
        'deduction': {'amount': str(run.deduction_amount), 'currency': run.currency},
        'net': {'amount': str(run.net_amount), 'currency': run.currency},
        'approved_at': run.approved_at.isoformat() if run.approved_at else None,
        'acknowledge_deadline': run.acknowledge_deadline.isoformat() if run.acknowledge_deadline else None,
        'acknowledged_at': run.acknowledged_at.isoformat() if run.acknowledged_at else None,
        'acknowledged_by': run.acknowledged_by,
        'acknowledgement_locked': run.acknowledgement_locked,
    }


class PartnerAdminPayrollRunApproveView(PartnerAdminMixin, APIView):
    """POST /api/v1/partner-admin/payroll/runs/:id/approve.

    DRAFT -> APPROVED, opens the acknowledgement window, and emits
    `payroll.run.approved` to partner webhooks (§6: "line-item detail ready
    to be pulled").
    """

    def post(self, request, run_id):
        from datetime import timedelta
        from django.conf import settings

        run = get_object_or_404(
            PayrollRun, pk=run_id, foundation_id=request.foundation_id)
        if run.status != PayrollRun.STATUS_DRAFT:
            raise PartnerAPIError(
                409, 'PAYROLL_RUN_LOCKED',
                f'Only DRAFT runs can be approved; run is {run.status}.')

        ack_days = int(getattr(settings, 'EDUCORE_PARTNER_PAYROLL_ACK_DAYS', 14))
        now = timezone.now()
        run.status = PayrollRun.STATUS_APPROVED
        run.approved_at = now
        run.approved_by = str(request.user.pk)
        run.acknowledge_deadline = now + timedelta(days=ack_days)
        run.save(update_fields=['status', 'approved_at', 'approved_by',
                                'acknowledge_deadline', 'updated_at', 'updated_by'])

        audit(
            action='payroll.run.approved',
            entity_type='PayrollRun',
            entity_id=run.pk,
            foundation_id=request.foundation_id,
            school_id=run.school_id,
            actor_id=str(request.user.pk),
            diff={'status': 'APPROVED', 'acknowledge_deadline': run.acknowledge_deadline.isoformat()},
        )
        services.safe_emit_partner_event(
            foundation_id=run.foundation_id,
            event_type=services.EVENT_PAYROLL_RUN_APPROVED,
            payload={
                'payroll_run_id': run.pk,
                'school_id': run.school_id,
                'period': run.period,
                'net': {'amount': str(run.net_amount), 'currency': run.currency},
                'acknowledge_deadline': run.acknowledge_deadline.isoformat(),
            },
        )
        return Response(_run_json(run))
