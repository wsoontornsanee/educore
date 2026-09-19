"""Pickup safety endpoints (spec/05 §5, §8: `POST /pickup-authorizations`, `POST /pickup/verify`).

Guardian side (`pickup.authorize`): create, list and revoke authorisations for their own linked students.
Staff side (`attendance.write` for the student's school): verify a QR or authorisation BEFORE release (ATT-016,
no state change), then release; a school admin (`pickup.override`) may release to an unauthorised person with a
written reason (ATT-018). The foundation always comes from the authenticated user, never from the request.
"""
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.attendance.models import PickupAuthorization
from apps.attendance.pickup import (
    PickupError, assert_usable, authorization_status, create_pickup_authorization, load_authorization,
    override_release, pickup_qr_token, release_to_guardian, release_with_authorization,
    revoke_pickup_authorization, verification_summary,
)
from apps.identity.console_access import accessible_school_ids
from apps.identity.models import GuardianLink, Student
from apps.identity.permissions import HasRequiredPermission
from educore.middleware.tenancy import get_current_foundation_id

_HTTP_STATUS = {
    'PICKUP_INVALID_TOKEN': status.HTTP_404_NOT_FOUND,
    'PICKUP_NOT_GUARDIAN': status.HTTP_403_FORBIDDEN,
    'PICKUP_NOT_AUTHORISED': status.HTTP_403_FORBIDDEN,
}


def _error(exc: PickupError) -> Response:
    return Response(
        {'error': exc.message, 'code': exc.code}, status=_HTTP_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST),
    )


def _not_found() -> Response:
    return _error(PickupError('PICKUP_INVALID_TOKEN', _("Kode QR penjemputan tidak valid.")))


def _may_act_at_school(user, foundation_id, permission, school_id) -> bool:
    ceiling = accessible_school_ids(user, foundation_id, permission)
    return ceiling is None or school_id in ceiling


def _authorization_data(authorization: PickupAuthorization, now=None) -> dict:
    """What the creating guardian sees; the QR token is included only while it can still be used."""
    now = now or timezone.now()
    state = authorization_status(authorization, now)
    return {
        'id': authorization.id,
        'student_id': authorization.student_id,
        'person_name': authorization.person_name,
        'relation': authorization.relation,
        'phone': authorization.phone,
        'photo_key': authorization.photo_key,
        'valid_from': authorization.valid_from,
        'valid_to': authorization.valid_to,
        'one_time': authorization.one_time,
        'status': state,
        'used_at': authorization.used_at,
        'revoked_at': authorization.revoked_at,
        'qr_token': pickup_qr_token(authorization) if state in ('ACTIVE', 'SCHEDULED') else None,
    }


class _CreateSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    person_name = serializers.CharField(max_length=120)
    relation = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default='')
    photo_key = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')
    valid_from = serializers.DateTimeField()
    valid_to = serializers.DateTimeField()
    one_time = serializers.BooleanField(required=False, default=True)


class PickupAuthorizationView(APIView):
    """GET/POST /pickup-authorizations/: a guardian lists and creates authorisations for their linked student."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'pickup.authorize'

    def get(self, request):
        foundation_id = get_current_foundation_id()
        raw = request.query_params.get('student_id', '')
        if not raw.isdigit():
            return Response({'error': _("student_id wajib diisi.")}, status=status.HTTP_400_BAD_REQUEST)
        linked = GuardianLink.objects.filter(
            foundation_id=foundation_id, student_id=int(raw), guardian__user=request.user, deleted_at__isnull=True,
        ).exists()
        if not linked:
            return _not_found()
        now = timezone.now()
        rows = PickupAuthorization.objects.filter(
            foundation_id=foundation_id, student_id=int(raw), deleted_at__isnull=True,
        ).order_by('-created_at', '-id')
        return Response([_authorization_data(row, now) for row in rows])

    def post(self, request):
        foundation_id = get_current_foundation_id()
        serializer = _CreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        student = Student.objects.filter(
            id=data['student_id'], foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('school').first()
        if student is None:
            return _not_found()
        try:
            authorization = create_pickup_authorization(
                user=request.user, student=student, person_name=data['person_name'], relation=data['relation'],
                phone=data['phone'], photo_key=data['photo_key'], valid_from=data['valid_from'],
                valid_to=data['valid_to'], one_time=data['one_time'],
            )
        except PickupError as exc:
            return _error(exc)
        return Response(_authorization_data(authorization), status=status.HTTP_201_CREATED)


class PickupAuthorizationRevokeView(APIView):
    """POST /pickup-authorizations/<id>/revoke/: any guardian linked to the student withdraws an authorisation."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'pickup.authorize'

    def post(self, request, authorization_id):
        foundation_id = get_current_foundation_id()
        authorization = PickupAuthorization.objects.filter(
            id=authorization_id, foundation_id=foundation_id, deleted_at__isnull=True,
        ).first()
        linked = authorization is not None and GuardianLink.objects.filter(
            foundation_id=foundation_id, student_id=authorization.student_id, guardian__user=request.user,
            deleted_at__isnull=True,
        ).exists()
        if not linked:
            return _not_found()
        try:
            revoked = revoke_pickup_authorization(authorization, request.user)
        except PickupError as exc:
            return _error(exc)
        return Response(_authorization_data(revoked))


class _TokenOrIdSerializer(serializers.Serializer):
    qr_token = serializers.CharField(required=False, allow_blank=False)
    authorization_id = serializers.IntegerField(required=False)

    def validate(self, attrs):
        if bool(attrs.get('qr_token')) == (attrs.get('authorization_id') is not None):
            raise serializers.ValidationError(_("Kirim salah satu: qr_token atau authorization_id."))
        return attrs


class PickupVerifyView(APIView):
    """POST /pickup/verify/ {qr_token | authorization_id}: what staff see before release (ATT-016). Changes nothing."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'attendance.write'

    def post(self, request):
        foundation_id = get_current_foundation_id()
        serializer = _TokenOrIdSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            authorization = load_authorization(foundation_id, **serializer.validated_data)
            if not _may_act_at_school(request.user, foundation_id, self.required_permission, authorization.school_id):
                return _not_found()
            assert_usable(authorization)
        except PickupError as exc:
            return _error(exc)
        return Response(verification_summary(authorization))


class _ReleaseSerializer(serializers.Serializer):
    qr_token = serializers.CharField(required=False, allow_blank=False)
    authorization_id = serializers.IntegerField(required=False)
    student_id = serializers.IntegerField(required=False)
    guardian_id = serializers.IntegerField(required=False)

    def validate(self, attrs):
        by_authorization = bool(attrs.get('qr_token')) or attrs.get('authorization_id') is not None
        by_guardian = attrs.get('student_id') is not None and attrs.get('guardian_id') is not None
        if by_authorization == by_guardian:
            raise serializers.ValidationError(
                _("Kirim qr_token/authorization_id, atau student_id bersama guardian_id."),
            )
        if by_authorization and bool(attrs.get('qr_token')) == (attrs.get('authorization_id') is not None):
            raise serializers.ValidationError(_("Kirim salah satu: qr_token atau authorization_id."))
        return attrs


def _event_data(event) -> dict:
    return {
        'id': event.id, 'student_id': event.student_id, 'method': event.method, 'picked_up_by': event.picked_up_by,
        'verified_by_id': event.verified_by_id, 'occurred_at': event.occurred_at,
    }


class PickupReleaseView(APIView):
    """POST /pickup/release/: release the student to an authorised person (QR / authorisation id) or to a
    guardian on file with `can_pickup`. Anyone else needs the override."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'attendance.write'

    def post(self, request):
        foundation_id = get_current_foundation_id()
        serializer = _ReleaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            if data.get('student_id') is not None:
                student = Student.objects.filter(
                    id=data['student_id'], foundation_id=foundation_id, deleted_at__isnull=True,
                ).select_related('school', 'person').first()
                if student is None or not _may_act_at_school(
                    request.user, foundation_id, self.required_permission, student.school_id,
                ):
                    return _not_found()
                event = release_to_guardian(staff_user=request.user, student=student, guardian_id=data['guardian_id'])
            else:
                authorization = load_authorization(
                    foundation_id, qr_token=data.get('qr_token'), authorization_id=data.get('authorization_id'),
                )
                if not _may_act_at_school(request.user, foundation_id, self.required_permission, authorization.school_id):
                    return _not_found()
                event = release_with_authorization(staff_user=request.user, authorization=authorization)
        except PickupError as exc:
            return _error(exc)
        return Response(_event_data(event), status=status.HTTP_201_CREATED)


class _OverrideSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    picked_up_by = serializers.CharField(max_length=120)
    reason = serializers.CharField()


class PickupOverrideView(APIView):
    """POST /pickup/override/: a school admin releases a student to a non-authorised person (ATT-018). The
    reason is mandatory and the audit event is flagged HIGH priority."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'pickup.override'

    def post(self, request):
        foundation_id = get_current_foundation_id()
        serializer = _OverrideSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        student = Student.objects.filter(
            id=data['student_id'], foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('school', 'person').first()
        if student is None or not _may_act_at_school(request.user, foundation_id, self.required_permission, student.school_id):
            return _not_found()
        try:
            event = override_release(
                staff_user=request.user, student=student, picked_up_by=data['picked_up_by'], reason=data['reason'],
            )
        except PickupError as exc:
            return _error(exc)
        return Response(_event_data(event), status=status.HTTP_201_CREATED)


class PickupStaffRevokeView(APIView):
    """POST /pickup/authorizations/<id>/revoke/: gate staff withdraw an authorisation a guardian reports lost or
    stolen. Same rules as the guardian's revoke (idempotent; a spent one-time authorisation cannot be revoked)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'attendance.write'

    def post(self, request, authorization_id):
        foundation_id = get_current_foundation_id()
        authorization = PickupAuthorization.objects.filter(
            id=authorization_id, foundation_id=foundation_id, deleted_at__isnull=True,
        ).first()
        if authorization is None or not _may_act_at_school(
            request.user, foundation_id, self.required_permission, authorization.school_id,
        ):
            return _not_found()
        try:
            revoked = revoke_pickup_authorization(authorization, request.user)
        except PickupError as exc:
            return _error(exc)
        return Response(_authorization_data(revoked))
