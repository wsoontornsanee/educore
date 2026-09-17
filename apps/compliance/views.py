"""API views for statutory export validation (spec/14 §4, CMP-018)."""
from rest_framework import status, views
from rest_framework.response import Response

from apps.compliance.models import StatutorySystem
from apps.compliance.services import validate_statutory_export
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
