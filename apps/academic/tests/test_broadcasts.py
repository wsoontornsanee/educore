import datetime
from django.test import TestCase
from rest_framework.test import APIClient

from apps.identity.models import Guardian, GuardianLink, Person, RoleAssignment, User
from apps.academic.models import Broadcast, ClassEnrollment
from apps.academic.services import (
    BroadcastNotAllowedError,
    BroadcastRateLimitedError,
    send_broadcast,
    set_broadcast_policy,
)
from apps.academic.tests.base import build_academic_fixture
from apps.notifications.models import NotificationIntent


def enroll_with_guardian(fx, with_user=True):
    ClassEnrollment.objects.create(
        foundation_id=fx['foundation'].id,
        student=fx['student'],
        class_group=fx['class_group'],
        enrolled_at=datetime.date(2026, 7, 1),
    )
    parent_person = Person.all_tenants.create(
        foundation_id=fx['foundation'].id, nik='3471010101010606', full_name='Bu Ani',
    )
    parent_user = None
    if with_user:
        parent_user = User.objects.create(
            foundation_id=fx['foundation'].id, phone_e164='+628155555555', email='ani@parent.id', full_name='Bu Ani',
        )
    guardian = Guardian.all_tenants.create(
        foundation_id=fx['foundation'].id, person=parent_person, user=parent_user,
    )
    GuardianLink.all_tenants.create(
        foundation_id=fx['foundation'].id, guardian=guardian, student=fx['student'],
        relation=GuardianLink.RELATION_MOTHER, is_primary=True,
    )
    return guardian


class SendBroadcastTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()

    def test_send_broadcast_dispatches_to_guardian_with_user(self):
        enroll_with_guardian(self.fx)
        broadcast = send_broadcast(self.fx['teacher'], self.fx['class_group'], "Info", "Besok libur")
        self.assertEqual(broadcast.recipient_count, 1)
        self.assertEqual(NotificationIntent.objects.filter(foundation_id=self.fx['foundation'].id).count(), 1)

    def test_guardian_without_user_skipped(self):
        enroll_with_guardian(self.fx, with_user=False)
        broadcast = send_broadcast(self.fx['teacher'], self.fx['class_group'], "Info", "Besok libur")
        self.assertEqual(broadcast.recipient_count, 0)
        self.assertEqual(NotificationIntent.objects.filter(foundation_id=self.fx['foundation'].id).count(), 0)

    def test_disabled_policy_rejects(self):
        enroll_with_guardian(self.fx)
        set_broadcast_policy(self.fx['school'], False)
        with self.assertRaises(BroadcastNotAllowedError):
            send_broadcast(self.fx['teacher'], self.fx['class_group'], "Info", "Besok libur")

    def test_daily_rate_limit(self):
        enroll_with_guardian(self.fx)
        for i in range(5):
            send_broadcast(self.fx['teacher'], self.fx['class_group'], f"Info {i}", "Isi")
        with self.assertRaises(BroadcastRateLimitedError):
            send_broadcast(self.fx['teacher'], self.fx['class_group'], "Info 6", "Isi")

    def test_broadcast_persisted(self):
        enroll_with_guardian(self.fx)
        send_broadcast(self.fx['teacher'], self.fx['class_group'], "Info", "Besok libur")
        self.assertEqual(Broadcast.objects.filter(class_group=self.fx['class_group']).count(), 1)


class BroadcastViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        enroll_with_guardian(self.fx)
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.fx['teacher_user'],
            role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )

    def test_send_broadcast_via_api(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post('/api/v1/academic/teacher/broadcasts/', {
            'class_group_id': self.fx['class_group'].id,
            'title': 'Pengumuman',
            'body': 'Besok libur nasional.',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['recipient_count'], 1)

    def test_policy_toggle_via_api(self):
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.fx['teacher_user'],
            role='school_admin',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res_patch = self.client.patch(
            f'/api/v1/academic/schools/{self.fx["school"].id}/broadcast-policy/',
            {'teacher_can_broadcast': False}, format='json',
        )
        self.assertEqual(res_patch.status_code, 200)
        self.assertFalse(res_patch.json()['teacher_can_broadcast'])

        res_send = self.client.post('/api/v1/academic/teacher/broadcasts/', {
            'class_group_id': self.fx['class_group'].id,
            'title': 'Pengumuman', 'body': 'Besok libur.',
        }, format='json')
        self.assertEqual(res_send.status_code, 400)
