"""Web (session-auth) console for the pickup safety flow (spec/05 §5, ATT-014..ATT-018).

The gate staff's screen over the same services the JSON API uses (`apps.attendance.pickup`): scan or type a
pickup QR and see who it releases the student to BEFORE releasing (ATT-016), release to an authorised person or to
a guardian on file, withdraw a lost authorisation, and (school admin, `pickup.override`) release to anyone else
with a written reason (ATT-018). Every action is tied to the school picked with `?school_id=`, which
`console_context` restricts to the schools this user may act for; the service layer enforces the rules (window,
one-time use, mandatory reason) and writes the audit events.

The QR token is a bearer credential, so it is only ever POSTed, never put in a URL.
"""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.views import APIView

from apps.attendance.models import PickupAuthorization
from apps.attendance.pickup import (
    PickupError, assert_usable, authorization_status, load_authorization, override_release, release_to_guardian,
    release_with_authorization, revoke_pickup_authorization, verification_summary,
)
from apps.identity.console_access import StaffConsoleMixin, permitted_schools
from apps.identity.models import GuardianLink, Student

# Statuses an authorisation can still act on; anything else is history and stays off the screen.
_LIVE_STATUSES = ('ACTIVE', 'SCHEDULED')


class _PickupConsoleView(StaffConsoleMixin, APIView):
    def get_required_permission(self):
        return 'attendance.write'

    def _find_student(self, foundation_id, school, nis):
        nis = (nis or '').strip()
        if not nis:
            return None
        return Student.objects.filter(
            foundation_id=foundation_id, school_id=school.id, nis=nis, deleted_at__isnull=True,
        ).select_related('person').first()

    def _find_authorization(self, foundation_id, school, authorization_id):
        """An authorisation of THIS school, or a PickupError that reads as an unknown code."""
        raw = str(authorization_id or '').strip()
        authorization = load_authorization(foundation_id, authorization_id=int(raw)) if raw.isdigit() else None
        if authorization is None or authorization.school_id != school.id:
            raise PickupError('PICKUP_INVALID_TOKEN', _("Kode QR penjemputan tidak valid."))
        return authorization

    def _back(self, school, nis=''):
        url = f"{reverse('attendance-pickup-console-page')}?school_id={school.id}"
        return redirect(f"{url}&nis={nis}" if nis else url)

    def _fail(self, request, school, exc_or_msg, nis=''):
        messages.error(request, exc_or_msg.message if isinstance(exc_or_msg, PickupError) else str(exc_or_msg))
        return self._back(school, nis)


class PickupConsolePageView(_PickupConsoleView):
    """GET /web/attendance/pickup/?school_id=[&nis=] — verify form, plus the student panel once a NIS is looked up."""

    def get(self, request):
        foundation_id, schools, school = self.console_context(request)
        return self._render(request, foundation_id, schools, school)

    def _render(self, request, foundation_id, schools, school, verification=None):
        context = {'schools': schools, 'school': school, 'verification': verification, 'student': None}
        if school is not None:
            can_override = any(
                s.id == school.id for s in permitted_schools(request.user, foundation_id, 'pickup.override')
            )
            context['can_override'] = can_override
            nis = request.query_params.get('nis') or request.POST.get('nis') or ''
            context['nis'] = nis.strip()
            student = self._find_student(foundation_id, school, nis)
            context['student'] = student
            if student is not None:
                context.update(self._student_panel(foundation_id, student))
            elif context['nis']:
                messages.error(request, _("Siswa dengan NIS tersebut tidak ditemukan di sekolah ini."))
        return render(request, 'pages/pickup_console_page.html', context)

    def _student_panel(self, foundation_id, student):
        now = timezone.now()
        guardians = GuardianLink.objects.filter(
            foundation_id=foundation_id, student=student, can_pickup=True, deleted_at__isnull=True,
            guardian__deleted_at__isnull=True,
        ).select_related('guardian__person').order_by('guardian__person__full_name')
        authorizations = []
        for row in PickupAuthorization.objects.filter(
            foundation_id=foundation_id, student=student, deleted_at__isnull=True,
        ).order_by('-created_at', '-id'):
            state = authorization_status(row, now)
            if state in _LIVE_STATUSES:
                authorizations.append({'row': row, 'status': state})
        return {'guardian_links': list(guardians), 'authorizations': authorizations}


class PickupVerifyWebView(PickupConsolePageView):
    """POST /web/attendance/pickup/verify/?school_id= {qr_token | authorization_id} — show who the code releases the
    student to. Changes nothing; renders the page in place so the token never lands in a URL."""
    http_method_names = ['post', 'options']

    def post(self, request):
        foundation_id, schools, school = self.console_context(request)
        token = (request.POST.get('qr_token') or '').strip()
        try:
            if token:
                authorization = load_authorization(foundation_id, qr_token=token)
                if authorization.school_id != school.id:
                    raise PickupError('PICKUP_INVALID_TOKEN', _("Kode QR penjemputan tidak valid."))
            else:
                authorization = self._find_authorization(foundation_id, school, request.POST.get('authorization_id'))
            assert_usable(authorization)
        except PickupError as exc:
            return self._fail(request, school, exc)
        return self._render(request, foundation_id, schools, school, verification=verification_summary(authorization))


class PickupReleaseWebView(_PickupConsoleView):
    """POST /web/attendance/pickup/release/?school_id= {authorization_id | nis + guardian_id}."""

    def post(self, request):
        foundation_id, _schools, school = self.console_context(request)
        nis = request.POST.get('nis', '')
        try:
            if request.POST.get('authorization_id'):
                authorization = self._find_authorization(foundation_id, school, request.POST['authorization_id'])
                event = release_with_authorization(staff_user=request.user, authorization=authorization)
            else:
                student = self._find_student(foundation_id, school, nis)
                guardian_id = request.POST.get('guardian_id', '')
                if student is None or not guardian_id.isdigit():
                    return self._fail(request, school, _("Siswa dengan NIS tersebut tidak ditemukan di sekolah ini."))
                event = release_to_guardian(staff_user=request.user, student=student, guardian_id=int(guardian_id))
        except PickupError as exc:
            return self._fail(request, school, exc, nis)
        messages.success(request, _("%(name)s diserahkan kepada %(person)s.") % {
            'name': event.student.person.full_name if event.student.person else '', 'person': event.picked_up_by,
        })
        return self._back(school)


class PickupRevokeWebView(_PickupConsoleView):
    """POST /web/attendance/pickup/revoke/?school_id= {authorization_id} — withdraw a lost or stolen authorisation."""

    def post(self, request):
        foundation_id, _schools, school = self.console_context(request)
        nis = request.POST.get('nis', '')
        try:
            authorization = self._find_authorization(foundation_id, school, request.POST.get('authorization_id'))
            revoke_pickup_authorization(authorization, request.user)
        except PickupError as exc:
            return self._fail(request, school, exc, nis)
        messages.success(request, _("Otorisasi penjemputan dicabut."))
        return self._back(school, nis)


class PickupOverrideWebView(_PickupConsoleView):
    """POST /web/attendance/pickup/override/?school_id= {nis, picked_up_by, reason} — school admin only
    (`pickup.override`); the service demands the reason and writes the HIGH-priority audit event."""

    def get_required_permission(self):
        return 'pickup.override'

    def post(self, request):
        foundation_id, _schools, school = self.console_context(request)
        nis = request.POST.get('nis', '')
        student = self._find_student(foundation_id, school, nis)
        if student is None:
            return self._fail(request, school, _("Siswa dengan NIS tersebut tidak ditemukan di sekolah ini."))
        try:
            override_release(
                staff_user=request.user, student=student, picked_up_by=request.POST.get('picked_up_by', ''),
                reason=request.POST.get('reason', ''),
            )
        except PickupError as exc:
            return self._fail(request, school, exc, nis)
        messages.success(request, _("Penjemputan dengan pengecualian dicatat dan diberitahukan ke admin sekolah."))
        return self._back(school)
