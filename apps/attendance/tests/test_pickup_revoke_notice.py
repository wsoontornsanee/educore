"""Gate staff withdrawing a pickup authorisation tells the student's guardians; a guardian's own revoke does not."""
from unittest import mock

from django.core.management import call_command
from django.utils import timezone

from apps.attendance.models import PickupAuthorization
from apps.attendance.tests.test_pickup import SECRET_PHONE, _Base
from apps.notifications.models import ChannelType, NotificationCategory, NotificationIntent
from apps.notifications.services import render_template_message
from educore.middleware.tenancy import set_current_foundation_id


class RevokeNoticeTests(_Base):
    def setUp(self):
        super().setUp()
        self.authorization_id = self.create_authorization().json()['id']
        set_current_foundation_id(self.foundation.id)

    def staff_revoke(self, user=None, authorization_id=None):
        return self.post(
            user or self.teacher, f'/api/v1/pickup/authorizations/{authorization_id or self.authorization_id}/revoke/', {},
        )

    def notices(self):
        return NotificationIntent.all_tenants.filter(
            foundation_id=self.foundation.id, template_key='attendance.pickup_revoked',
        )

    def test_every_linked_guardian_is_told_when_staff_revoke(self):
        self.assertEqual(self.staff_revoke().status_code, 200)
        self.assertEqual({n.recipient_user_id for n in self.notices()}, {self.guardian_user.id, self.second_user.id})

    def test_the_notice_names_the_person_and_student_but_carries_no_phone(self):
        self.staff_revoke()
        notice = self.notices().get(recipient_user=self.guardian_user)
        self.assertEqual(notice.category, NotificationCategory.DEPARTURE)
        self.assertEqual(notice.priority, 'HIGH')
        self.assertEqual(notice.payload['person_name'], 'Pak Sopir')
        self.assertEqual(notice.payload['student_name'], 'Umar bin Khattab')
        self.assertNotIn(SECRET_PHONE, str(notice.payload))

    def test_nobody_outside_the_students_guardians_is_told(self):
        self.staff_revoke()
        told = {n.recipient_user_id for n in self.notices()}
        for outsider in (self.stranger_user, self.teacher, self.school_admin):
            self.assertNotIn(outsider.id, told)

    def test_a_guardian_revoking_their_own_authorisation_notifies_nobody(self):
        res = self.post(self.guardian_user, f'/api/v1/pickup-authorizations/{self.authorization_id}/revoke/', {})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(self.notices().exists())

    def test_a_repeated_revoke_does_not_notify_twice(self):
        self.staff_revoke()
        self.staff_revoke()
        self.staff_revoke(user=self.school_admin)
        self.assertEqual(self.notices().count(), 2)

    def test_a_revoke_that_is_refused_notifies_nobody(self):
        PickupAuthorization.all_tenants.filter(id=self.authorization_id).update(used_at=timezone.now())
        self.assertEqual(self.staff_revoke().status_code, 400)
        self.assertFalse(self.notices().exists())

    def test_staff_of_another_school_cannot_revoke_and_nobody_is_told(self):
        self.assertEqual(self.staff_revoke(user=self.other_school_teacher).status_code, 404)
        self.assertFalse(self.notices().exists())

    def test_a_failed_delivery_never_undoes_the_revoke_and_the_log_has_no_secrets(self):
        def boom(**kwargs):
            raise RuntimeError(f'provider down for {SECRET_PHONE}')

        with mock.patch('apps.notifications.services.dispatch_intent', side_effect=boom):
            with self.assertLogs('apps.attendance.pickup', level='WARNING') as logs:
                res = self.staff_revoke()
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(PickupAuthorization.all_tenants.get(id=self.authorization_id).revoked_at)
        text = '\n'.join(logs.output)
        self.assertNotIn(SECRET_PHONE, text)
        self.assertNotIn('Pak Sopir', text)
        self.assertIn('RuntimeError', text)

    def test_the_seeded_templates_render_with_no_placeholder_left(self):
        call_command('seed_notification_templates', foundation_id=self.foundation.id, stdout=mock.MagicMock())
        payload = {
            'student_name': 'Umar bin Khattab', 'school_name': 'SD Pickup', 'person_name': 'Pak Sopir',
            'time': '14:05', 'date': '2026-09-19', 'guardian_name': 'Khalid',
        }
        for channel in (ChannelType.WHATSAPP, ChannelType.PUSH):
            with self.subTest(channel=channel):
                text = render_template_message('attendance.pickup_revoked', channel, self.foundation.id, payload)
                combined = f"{text['subject']} {text['body']}"
                self.assertNotIn('{', combined)
                for expected in ('Pak Sopir', 'Umar bin Khattab'):
                    self.assertIn(expected, combined)
