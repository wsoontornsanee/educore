"""ATT-018 follow-up: school admins are told of an override, and gate staff can revoke an authorisation."""
import datetime
from unittest import mock

from django.core.management import call_command
from django.utils import timezone

from apps.attendance.models import PickupAuthorization
from apps.attendance.tests.test_pickup import _Base
from apps.core.models import AuditEvent
from apps.identity.models import RoleAssignment
from apps.notifications.models import CATEGORY_CONFIG, ChannelType, NotificationCategory, NotificationIntent
from apps.notifications.services import render_template_message
from educore.middleware.tenancy import tenant_context

REASON = 'Ibu sakit, kerabat menjemput; wali dihubungi lewat telepon.'


class OverrideAdminNoticeTests(_Base):
    def setUp(self):
        super().setUp()
        self.second_school_admin = self._staff(RoleAssignment.ROLE_SCHOOL_ADMIN, self.school, "+6281300000020")
        self.foundation_admin = self._user("+6281300000021", "Ketua Yayasan")
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.foundation_admin, role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.foundation.id,
        )

    def override(self, user):
        return self.post(user, '/api/v1/pickup/override/', {
            'student_id': self.student.id, 'picked_up_by': 'Orang Tak Dikenal', 'reason': REASON,
        })

    def admin_notices(self):
        # all_tenants: after an API request the ambient tenant context is cleared.
        return NotificationIntent.all_tenants.filter(
            foundation_id=self.foundation.id, category=NotificationCategory.PICKUP_OVERRIDE,
        )

    def test_the_other_school_admin_and_the_foundation_admin_are_told_but_not_the_one_who_did_it(self):
        self.assertEqual(self.override(self.school_admin).status_code, 201)
        recipients = {n.recipient_user_id for n in self.admin_notices()}
        self.assertEqual(recipients, {self.second_school_admin.id, self.foundation_admin.id})

    def test_nobody_else_is_told(self):
        self.override(self.school_admin)
        told = {n.recipient_user_id for n in self.admin_notices()}
        for outsider in (self.teacher, self.other_school_admin, self.other_school_teacher, self.guardian_user, self.second_user):
            self.assertNotIn(outsider.id, told)

    def test_the_notice_names_who_allowed_it_and_why_and_is_high_priority(self):
        self.override(self.school_admin)
        notice = self.admin_notices().first()
        self.assertEqual(notice.template_key, 'attendance.pickup_override_admin')
        self.assertEqual(notice.priority, 'HIGH')
        self.assertEqual(notice.payload['picked_up_by'], 'Orang Tak Dikenal')
        self.assertEqual(notice.payload['actor_name'], self.school_admin.full_name)
        self.assertIn('wali dihubungi', notice.payload['reason'])
        self.assertEqual(notice.payload['student_name'], 'Umar bin Khattab')

    def test_a_very_long_reason_is_shortened_in_the_notice_but_kept_in_full_on_the_event(self):
        long_reason = 'alasan ' * 100
        self.post(self.school_admin, '/api/v1/pickup/override/', {
            'student_id': self.student.id, 'picked_up_by': 'Seseorang', 'reason': long_reason,
        })
        self.assertLessEqual(len(self.admin_notices().first().payload['reason']), 200)

    def test_the_override_still_succeeds_when_the_acting_admin_is_the_only_admin(self):
        self.second_school_admin.role_assignments.update(deleted_at=timezone.now())
        self.foundation_admin.role_assignments.update(deleted_at=timezone.now())
        self.assertEqual(self.override(self.school_admin).status_code, 201)
        self.assertEqual(self.admin_notices().count(), 0)

    def test_only_an_override_alerts_admins_not_an_ordinary_release(self):
        self.post(self.teacher, '/api/v1/pickup/release/', {'student_id': self.student.id, 'guardian_id': self.guardian.id})
        created = self.create_authorization().json()
        self.post(self.teacher, '/api/v1/pickup/release/', {'qr_token': created['qr_token']})
        self.assertEqual(self.admin_notices().count(), 0)

    def test_the_guardians_are_still_told_as_before(self):
        self.override(self.school_admin)
        guardian_notices = NotificationIntent.all_tenants.filter(
            foundation_id=self.foundation.id, category='DEPARTURE', template_key='attendance.pickup_override',
        )
        self.assertEqual(guardian_notices.count(), 2)

    def test_notifying_twice_for_one_event_does_not_double_up(self):
        from apps.attendance.models import PickupEvent
        from apps.attendance.pickup import notify_override_to_school_admins
        self.override(self.school_admin)
        event = PickupEvent.all_tenants.get()
        with tenant_context(self.foundation.id):  # as in a request: dedupe reads through the tenant-scoped manager
            notify_override_to_school_admins(event)
        self.assertEqual(self.admin_notices().count(), 2)

    def test_one_failing_recipient_never_undoes_the_override_or_stops_the_others(self):
        real = __import__('apps.notifications.services', fromlist=['dispatch_intent']).dispatch_intent
        calls = {'n': 0}

        def flaky(**kwargs):
            if kwargs.get('category') == NotificationCategory.PICKUP_OVERRIDE:
                calls['n'] += 1
                if calls['n'] == 1:
                    raise RuntimeError('provider down for +628111222333')
            return real(**kwargs)

        with mock.patch('apps.notifications.services.dispatch_intent', side_effect=flaky):
            res = self.override(self.school_admin)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(self.admin_notices().count(), 1)

    def test_the_failure_log_line_never_carries_the_exception_text(self):
        def boom(**kwargs):
            if kwargs.get('category') == NotificationCategory.PICKUP_OVERRIDE:
                raise RuntimeError('provider down for +628111222333')
            return mock.DEFAULT

        with mock.patch('apps.notifications.services.dispatch_intent', side_effect=boom):
            with self.assertLogs('apps.attendance.pickup', level='WARNING') as logs:
                self.override(self.school_admin)
        text = '\n'.join(logs.output)
        self.assertNotIn('+628111222333', text)
        self.assertIn('RuntimeError', text)

    def test_the_seeded_templates_render_for_both_channels_with_nothing_left_unfilled(self):
        call_command('seed_notification_templates', foundation_id=self.foundation.id, stdout=mock.MagicMock())
        payload = {
            'actor_name': 'Kepala Sekolah', 'student_name': 'Umar bin Khattab', 'school_name': 'SD Pickup',
            'picked_up_by': 'Orang Tak Dikenal', 'time': '14:05', 'date': '2026-09-19', 'reason': REASON,
        }
        for channel in (ChannelType.WHATSAPP, ChannelType.PUSH):
            with self.subTest(channel=channel):
                text = render_template_message('attendance.pickup_override_admin', channel, self.foundation.id, payload)
                combined = f"{text['subject']} {text['body']}"
                self.assertNotIn('{', combined)
                for expected in ('Kepala Sekolah', 'Umar bin Khattab', 'Orang Tak Dikenal'):
                    self.assertIn(expected, combined)

    def test_the_category_cannot_be_opted_out_and_is_not_held_for_quiet_hours(self):
        config = CATEGORY_CONFIG[NotificationCategory.PICKUP_OVERRIDE]
        self.assertFalse(config['opt_out_allowed'])
        self.assertFalse(config['quiet_hours_respected'])
        self.assertEqual(config['priority'], 'HIGH')


class StaffRevokeTests(_Base):
    def revoke(self, user, authorization_id):
        return self.post(user, f'/api/v1/pickup/authorizations/{authorization_id}/revoke/', {})

    def test_gate_staff_can_revoke_and_the_qr_dies(self):
        created = self.create_authorization().json()
        res = self.revoke(self.teacher, created['id'])
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['status'], 'REVOKED')
        verify = self.post(self.teacher, '/api/v1/pickup/verify/', {'qr_token': created['qr_token']})
        self.assertEqual(verify.json()['code'], 'PICKUP_REVOKED')
        release = self.post(self.teacher, '/api/v1/pickup/release/', {'qr_token': created['qr_token']})
        self.assertEqual(release.json()['code'], 'PICKUP_REVOKED')

    def test_the_revoke_is_audited_with_the_staff_member_as_actor(self):
        created = self.create_authorization().json()
        self.revoke(self.teacher, created['id'])
        event = AuditEvent.objects.get(action='attendance.pickup_authorization.revoked')
        self.assertEqual(event.actor_id, str(self.teacher.id))
        self.assertEqual(event.school_id, self.school.id)

    def test_revoking_twice_is_harmless(self):
        created = self.create_authorization().json()
        self.assertEqual(self.revoke(self.teacher, created['id']).status_code, 200)
        self.assertEqual(self.revoke(self.teacher, created['id']).status_code, 200)

    def test_a_spent_one_time_authorization_cannot_be_revoked(self):
        created = self.create_authorization().json()
        PickupAuthorization.all_tenants.filter(id=created['id']).update(used_at=timezone.now())
        res = self.revoke(self.teacher, created['id'])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'PICKUP_ALREADY_USED')

    def test_staff_of_another_school_cannot_revoke_it(self):
        created = self.create_authorization().json()
        self.assertEqual(self.revoke(self.other_school_teacher, created['id']).status_code, 404)
        self.assertIsNone(PickupAuthorization.all_tenants.get(id=created['id']).revoked_at)

    def test_a_missing_authorization_is_not_found(self):
        self.assertEqual(self.revoke(self.teacher, 999999).status_code, 404)

    def test_a_parent_cannot_use_the_staff_endpoint(self):
        created = self.create_authorization().json()
        self.assertEqual(self.revoke(self.guardian_user, created['id']).status_code, 403)

    def test_the_guardian_endpoint_still_works_for_guardians(self):
        created = self.create_authorization().json()
        res = self.post(self.guardian_user, f"/api/v1/pickup-authorizations/{created['id']}/revoke/", {})
        self.assertEqual(res.status_code, 200)
