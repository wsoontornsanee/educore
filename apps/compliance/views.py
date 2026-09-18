"""API views for statutory export validation and PII export access audit (spec/14 §3/§4)."""
from rest_framework import generics, status, views
from rest_framework.response import Response

from apps.compliance.models import PiiExportAccessLog, StatutorySystem
from apps.compliance.serializers import PiiExportAccessLogSerializer
from apps.compliance.services import validate_statutory_export
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

