from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from apps.attendance.models import Credential
from apps.attendance.serializers import (
    CredentialIssueSerializer,
    CredentialRevokeSerializer,
    CredentialSerializer,
    CredentialVerifySerializer,
)
from apps.attendance.services import issue_credential, revoke_credential, verify_credential
from apps.core.pagination import StandardCursorPagination
from apps.identity.models import RoleAssignment, Staff, Student
from apps.identity.permissions import HasRequiredPermission


class CredentialViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Credential management and verification endpoints (spec/05 §2, §8, spec/12 §5).
    """
    pagination_class = StandardCursorPagination
    required_permission = 'student_records.read'

    def get_serializer_class(self):
        if self.action == 'create':
            return CredentialIssueSerializer
        if self.action == 'revoke':
            return CredentialRevokeSerializer
        if self.action == 'verify':
            return CredentialVerifySerializer
        return CredentialSerializer

    def get_permissions(self):
        if self.action in ['create', 'revoke']:
            self.required_permission = 'student_records.write'
        elif self.action == 'verify':
            # Edge devices and staff check-in can verify credentials
            self.required_permission = 'student_records.read'
        else:
            self.required_permission = 'student_records.read'
        return [HasRequiredPermission()]

    def get_queryset(self):
        foundation_id = getattr(self.request, 'foundation_id', None)
        if not foundation_id:
            return Credential.objects.none()

        qs = Credential.objects.filter(foundation_id=foundation_id).select_related(
            'student', 'student__person', 'student__school',
            'staff', 'staff__person', 'staff__school'
        )

        user = self.request.user
        if not user.is_authenticated:
            return Credential.objects.none()

        has_fnd_admin = RoleAssignment.all_tenants.filter(
            user=user,
            foundation_id=foundation_id,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            deleted_at__isnull=True,
        ).exists()
        if not has_fnd_admin:
            user_school_ids = RoleAssignment.all_tenants.filter(
                user=user,
                foundation_id=foundation_id,
                scope_type=RoleAssignment.SCOPE_SCHOOL,
                deleted_at__isnull=True,
            ).values_list('scope_id', flat=True)

            from django.db.models import Q
            qs = qs.filter(
                Q(student__school_id__in=user_school_ids) |
                Q(staff__school_id__in=user_school_ids)
            )

        student_param = self.request.query_params.get('student_id')
        if student_param:
            qs = qs.filter(student_id=student_param)

        staff_param = self.request.query_params.get('staff_id')
        if staff_param:
            qs = qs.filter(staff_id=staff_param)

        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)

        type_param = self.request.query_params.get('type')
        if type_param:
            qs = qs.filter(type=type_param)

        uid_param = self.request.query_params.get('uid')
        if uid_param:
            qs = qs.filter(uid__iexact=uid_param)

        return qs.order_by('-created_at')

    def get_object(self):
        foundation_id = getattr(self.request, 'foundation_id', None)
        obj_id = self.kwargs.get('pk')
        try:
            credential = Credential.objects.select_related(
                'student', 'student__school',
                'staff', 'staff__school'
            ).get(id=obj_id, foundation_id=foundation_id)
        except (Credential.DoesNotExist, ValueError):
            raise NotFound("Kredensial tidak ditemukan.")

        user = self.request.user
        has_fnd_admin = RoleAssignment.all_tenants.filter(
            user=user,
            foundation_id=foundation_id,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            deleted_at__isnull=True,
        ).exists()
        if not has_fnd_admin:
            user_school_ids = set(RoleAssignment.all_tenants.filter(
                user=user,
                foundation_id=foundation_id,
                scope_type=RoleAssignment.SCOPE_SCHOOL,
                deleted_at__isnull=True,
            ).values_list('scope_id', flat=True))

            cred_school_id = credential.student.school_id if credential.student else (
                credential.staff.school_id if credential.staff else None
            )
            if cred_school_id not in user_school_ids:
                raise NotFound("Kredensial tidak ditemukan.")

        self.check_object_permissions(self.request, credential)
        return credential

    def create(self, request, *args, **kwargs):
        """
        Issue a new credential (spec/05 §8, spec/12 §5).
        """
        serializer = CredentialIssueSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        foundation_id = getattr(request, 'foundation_id', None)
        student_id = data.get('student_id')
        staff_id = data.get('staff_id')

        user = request.user
        is_foundation_admin = RoleAssignment.all_tenants.filter(
            user=user,
            foundation_id=foundation_id,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            deleted_at__isnull=True,
        ).exists()
        allowed_school_ids = set(RoleAssignment.all_tenants.filter(
            user=user,
            foundation_id=foundation_id,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            deleted_at__isnull=True,
        ).values_list('scope_id', flat=True))

        student = None
        staff = None
        if student_id:
            try:
                student = Student.objects.get(id=student_id, foundation_id=foundation_id)
            except Student.DoesNotExist:
                raise NotFound("Siswa tidak ditemukan.")
            if not is_foundation_admin and student.school_id not in allowed_school_ids:
                raise NotFound("Siswa tidak ditemukan.")
        elif staff_id:
            try:
                staff = Staff.objects.get(id=staff_id, foundation_id=foundation_id)
            except Staff.DoesNotExist:
                raise NotFound("Staf tidak ditemukan.")
            if not is_foundation_admin and staff.school_id and staff.school_id not in allowed_school_ids:
                raise NotFound("Staf tidak ditemukan.")

        try:
            credential = issue_credential(
                foundation_id=foundation_id,
                student=student,
                staff=staff,
                type=data.get('type'),
                uid=data.get('uid'),
                card_number=data.get('card_number', ''),
                expires_in_minutes=data.get('expires_in_minutes', 15),
                user=request.user,
            )
        except Exception as e:
            raise ValidationError(str(e))

        output_serializer = CredentialSerializer(credential)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='revoke')
    def revoke(self, request, pk=None):
        """
        Revoke an active credential (spec/05 §8, spec/12 §5 HW-018).
        """
        credential = self.get_object()
        serializer = CredentialRevokeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        revoked = revoke_credential(
            credential=credential,
            reason=serializer.validated_data['reason'],
            post_replacement_fee=serializer.validated_data.get('post_replacement_fee', False),
            user=request.user,
        )

        output_serializer = CredentialSerializer(revoked)
        return Response(output_serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='verify')
    def verify(self, request):
        """
        Verify credential validity for turnstiles, face cameras, or POS terminals (spec/05 §4).
        """
        serializer = CredentialVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        foundation_id = getattr(request, 'foundation_id', None)
        uid = serializer.validated_data['uid']
        mark_used = serializer.validated_data.get('mark_used', False)

        result = verify_credential(
            foundation_id=foundation_id,
            uid=uid,
            mark_used=mark_used,
        )

        response_data = {
            'valid': result['valid'],
            'status': result['status'],
            'reason': str(result['reason']),
            'holder_type': result.get('holder_type'),
            'holder_name': result.get('holder_name'),
            'credential_id': str(result['credential'].id) if result.get('credential') else None,
        }
        if result.get('student'):
            response_data['student'] = {
                'id': str(result['student'].id),
                'nis': result['student'].nis,
                'nisn': result['student'].nisn,
                'school_id': str(result['student'].school_id),
            }
        elif result.get('staff'):
            response_data['staff'] = {
                'id': str(result['staff'].id),
                'nip': result['staff'].nip,
                'school_id': str(result['staff'].school_id) if result['staff'].school_id else None,
            }

        http_status = status.HTTP_200_OK if result['valid'] else status.HTTP_400_BAD_REQUEST
        return Response(response_data, status=http_status)
