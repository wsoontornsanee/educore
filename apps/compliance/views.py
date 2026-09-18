"""API views for statutory export validation and PII export access audit (spec/14 §3/§4)."""
from rest_framework import generics, status, views
from rest_framework.response import Response

from apps.compliance.models import ConsentRecord, DataSubjectRequest, PiiExportAccessLog, StatutorySystem
from apps.compliance.serializers import PiiExportAccessLogSerializer
from apps.compliance.services import (
    ConsentNotFoundError,
    PersonNotErasableError,
    erase_person,
    validate_statutory_export,
    withdraw_biometric_consent,
)
from apps.core.pagination import StandardCursorPagination
from apps.identity.models import School
from apps.identity.permissions import HasRequiredPermission
from educore.middleware.tenancy import get_current_foundation_id



class StatutoryValidationView(views.APIView):
    """GET /foundation/statutory-validation?school_id=&system= — pre-export
    validation report (CMP-018): every incomplete record and the specific
    missing field, students listed by name and rombel (spec/14 §7 criterion 1)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'school_config.read'

    def get(self, request):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return Response({"detail": "Konteks Yayasan tidak ditemukan."}, status=status.HTTP_400_BAD_REQUEST)

        school_id = request.query_params.get('school_id')
        system = (request.query_params.get('system') or StatutorySystem.DAPODIK).upper()

        if system not in StatutorySystem.values:
            return Response(
                {"detail": f"Sistem '{system}' tidak dikenali (gunakan DAPODIK atau EMIS)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        school = School.objects.filter(foundation_id=foundation_id, id=school_id).first()
        if school is None:
            return Response(
                {"detail": "Sekolah tidak ditemukan dalam Yayasan ini."},
                status=status.HTTP_404_NOT_FOUND,
            )

        issues = validate_statutory_export(school, system)
        total = sum(len(v) for v in issues.values())
        return Response({
            'school_id': school.id,
            'school_name': school.name,
            'system': system,
            'is_valid': total == 0,
            'total_issues': total,
            'issues': issues,
        })


class PiiExportAccessLogView(generics.ListAPIView):
    """
    GET /foundation/compliance/pii-exports — Audit trail of all PII-bearing exports (CMP-016, RPT-004).

    Filters:
        - report_key: e.g. 'statutory_dapodik', 'statutory_emis'
        - exported_by_id: actor ID
        - school_id: integer school ID
        - from: YYYY-MM-DD
        - to: YYYY-MM-DD
    """
    serializer_class = PiiExportAccessLogSerializer
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]
    required_permission = 'audit_log.read'

    def get_queryset(self):
        foundation_id = get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)
        if not foundation_id:
            return PiiExportAccessLog.objects.none()

        qs = PiiExportAccessLog.objects.filter(foundation_id=foundation_id)

        report_key = self.request.query_params.get('report_key')
        if report_key:
            qs = qs.filter(report_key=report_key)

        exported_by_id = self.request.query_params.get('exported_by_id')
        if exported_by_id:
            qs = qs.filter(exported_by_id=exported_by_id)

        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(school_id=school_id)

        from_date = self.request.query_params.get('from') or self.request.query_params.get('from_date')
        if from_date:
            qs = qs.filter(created_at__date__gte=from_date)

        to_date = self.request.query_params.get('to') or self.request.query_params.get('to_date')
        if to_date:
            qs = qs.filter(created_at__date__lte=to_date)

        return qs.order_by('-created_at')


class ErasureRequestView(views.APIView):
    """POST /foundation/compliance/erasure-requests — run a CMP-012 right-to-
    erasure request synchronously. GET — list past requests for this
    foundation."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'school_config.write'

    def post(self, request):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return Response({"detail": "Konteks Yayasan tidak ditemukan."}, status=status.HTTP_400_BAD_REQUEST)

        subject_type = request.data.get('subject_type')
        subject_id = request.data.get('subject_id')
        if subject_type not in ('STUDENT', 'STAFF') or not subject_id:
            return Response(
                {"detail": "subject_type (STUDENT/STAFF) dan subject_id wajib diisi."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        requested_by = str(request.user.id)
        requested_by_name = getattr(request.user, 'full_name', '') or ''

        try:
            dsar = erase_person(
                subject_type=subject_type, subject_id=subject_id, foundation_id=foundation_id,
                requested_by=requested_by, requested_by_name=requested_by_name,
            )
            return Response(_serialize_erasure_request(dsar), status=status.HTTP_201_CREATED)
        except PersonNotErasableError:
            dsar = (
                DataSubjectRequest.objects.filter(
                    foundation_id=foundation_id, subject_type=subject_type, subject_id=subject_id,
                )
                .order_by('-id')
                .first()
            )
            if dsar is None:
                # The refusal row should always exist (erase_person writes it
                # before raising), but never 500 on a missing bookkeeping row.
                return Response(
                    {"detail": "Permintaan penghapusan data ditolak dan tidak dapat diproses."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(_serialize_erasure_request(dsar), status=status.HTTP_200_OK)

    def get(self, request):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return Response({"detail": "Konteks Yayasan tidak ditemukan."}, status=status.HTTP_400_BAD_REQUEST)
        requests_qs = DataSubjectRequest.objects.filter(foundation_id=foundation_id).order_by('-id')[:100]
        return Response([_serialize_erasure_request(r) for r in requests_qs])


class BiometricConsentWithdrawalView(views.APIView):
    """POST /foundation/compliance/biometric-consent/withdraw — CMP-010:
    withdraw a subject's biometric consent, deleting their face template(s)
    within 24h (done synchronously). This is the admin-initiated equivalent
    of the parent-app withdrawal action — no parent-facing client exists yet
    in this repo (same precedent as every other parent-app-blocked item)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'school_config.write'

    def post(self, request):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return Response({"detail": "Konteks Yayasan tidak ditemukan."}, status=status.HTTP_400_BAD_REQUEST)

        subject_type = request.data.get('subject_type')
        subject_id = request.data.get('subject_id')
        if subject_type not in ('STUDENT', 'STAFF') or not subject_id:
            return Response(
                {"detail": "subject_type (STUDENT/STAFF) dan subject_id wajib diisi."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        actor_id = str(request.user.id)
        actor_name = getattr(request.user, 'full_name', '') or ''

        try:
            consent = withdraw_biometric_consent(
                subject_type=subject_type, subject_id=subject_id, foundation_id=foundation_id,
                actor_id=actor_id, actor_name=actor_name,
            )
        except ConsentNotFoundError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)

        return Response(_serialize_consent_record(consent), status=status.HTTP_200_OK)


def _serialize_consent_record(consent: ConsentRecord) -> dict:
    return {
        'id': consent.id,
        'subject_type': consent.subject_type,
        'subject_id': consent.subject_id,
        'purpose': consent.purpose,
        'version': consent.version,
        'granted_at': consent.granted_at.isoformat() if consent.granted_at else None,
        'withdrawn_at': consent.withdrawn_at.isoformat() if consent.withdrawn_at else None,
    }


def _serialize_erasure_request(dsar: DataSubjectRequest) -> dict:
    return {
        'id': dsar.id,
        'subject_type': dsar.subject_type,
        'subject_id': dsar.subject_id,
        'status': dsar.status,
        'requested_by_name': dsar.requested_by_name,
        'refusal_reason': dsar.refusal_reason,
    }

