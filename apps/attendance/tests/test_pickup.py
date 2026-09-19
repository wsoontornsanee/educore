"""Pickup safety (spec/05 §5, ATT-014..ATT-018)."""
import datetime
from unittest import mock, skipUnless

from django.core import signing
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.attendance.models import PickupAuthorization, PickupEvent, PickupMethod
from apps.attendance.pickup import TOKEN_SALT, pickup_qr_token
from apps.core.models import AuditEvent
from apps.identity.models import (
    Foundation, Guardian, GuardianLink, Person, RoleAssignment, School, Staff, Student, User,
)
from apps.notifications.models import ChannelType, NotificationIntent
from apps.notifications.services import render_template_message
from educore.middleware.tenancy import set_current_foundation_id

SECRET_PHONE = '+628111222333'


class _PickupFixture:
    """Shared fixture: a foundation, two schools, a student with two guardians, and staff at each school."""

    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Pickup", brand_name="Pickup", npwp="01.999.111.2-000.000", address="Jakarta",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = self._school("SD Pickup", "20500001")
        self.other_school = self._school("SMP Lain", "20500002")
        self.student = self._student(self.school, "Umar bin Khattab", "25001")

        self.guardian_user, self.guardian = self._guardian("Khalid bin Walid", "+6281300000001", can_pickup=True)
        self.second_user, self.second_guardian = self._guardian("Aisyah binti Abu Bakar", "+6281300000002", can_pickup=False)
        self.stranger_user = self._user("+6281300000003", "Orang Lain")
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.stranger_user, role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.foundation.id,
        )

        self.teacher = self._staff(RoleAssignment.ROLE_TEACHER, self.school, "+6281300000010")
        self.other_school_teacher = self._staff(RoleAssignment.ROLE_TEACHER, self.other_school, "+6281300000011")
        self.school_admin = self._staff(RoleAssignment.ROLE_SCHOOL_ADMIN, self.school, "+6281300000012")
        self.other_school_admin = self._staff(RoleAssignment.ROLE_SCHOOL_ADMIN, self.other_school, "+6281300000013")
        self.client = APIClient()

    # -- fixtures ---------------------------------------------------------------------------------
    def _school(self, name, npsn):
        return School.all_tenants.create(
            foundation_id=self.foundation.id, name=name, npsn=npsn, level=School.LEVEL_SD,
        )

    def _student(self, school, name, nis):
        person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name=name)
        return Student.all_tenants.create(foundation_id=self.foundation.id, school=school, person=person, nis=nis)

    def _user(self, phone, name):
        return User.objects.create(foundation_id=self.foundation.id, phone_e164=phone, full_name=name, is_active=True)

    def _guardian(self, name, phone, can_pickup):
        user = self._user(phone, name)
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=user, role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.foundation.id,
        )
        person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name=name)
        guardian = Guardian.all_tenants.create(foundation_id=self.foundation.id, person=person, user=user)
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id, guardian=guardian, student=self.student,
            relation=GuardianLink.RELATION_FATHER, can_pickup=can_pickup,
        )
        return user, guardian

    def _staff(self, role, school, phone):
        user = self._user(phone, f"{role} {phone}")
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=user, role=role,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=school.id,
        )
        return user

    def post(self, user, path, data):
        self.client.force_authenticate(user=user)
        return self.client.post(path, data, format='json')

    def get(self, user, path):
        self.client.force_authenticate(user=user)
        return self.client.get(path)

    def create_authorization(self, user=None, hours=(0, 3), one_time=True, **extra):
        now = timezone.now()
        body = {
            'student_id': self.student.id, 'person_name': 'Pak Sopir', 'relation': 'Sopir antar jemput',
            'phone': SECRET_PHONE, 'valid_from': (now - datetime.timedelta(minutes=1) + datetime.timedelta(hours=hours[0])).isoformat(),
            'valid_to': (now + datetime.timedelta(hours=hours[1])).isoformat(), 'one_time': one_time,
        }
        body.update(extra)
        return self.post(user or self.guardian_user, '/api/v1/pickup-authorizations/', body)


class _Base(_PickupFixture, TestCase):
    pass


class CreateAuthorizationTests(_Base):
    def test_a_guardian_who_may_pick_up_creates_an_authorization_and_gets_a_qr(self):
        res = self.create_authorization()
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body['status'], 'ACTIVE')
        self.assertTrue(body['qr_token'])
        row = PickupAuthorization.all_tenants.get(id=body['id'])
        self.assertEqual(row.student_id, self.student.id)
        self.assertEqual(row.created_by_guardian_id, self.guardian.id)
        self.assertTrue(row.one_time)

    def test_the_audit_event_carries_no_third_party_details(self):
        self.create_authorization()
        event = AuditEvent.objects.get(action='attendance.pickup_authorization.created')
        text = str(event.diff)
        self.assertNotIn(SECRET_PHONE, text)
        self.assertNotIn('Pak Sopir', text)

    def test_a_guardian_without_can_pickup_may_not_delegate(self):
        res = self.create_authorization(user=self.second_user)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'PICKUP_NOT_GUARDIAN')

    def test_a_parent_of_another_child_may_not_create_one_for_this_student(self):
        res = self.create_authorization(user=self.stranger_user)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'PICKUP_NOT_GUARDIAN')
        self.assertFalse(PickupAuthorization.all_tenants.exists())

    def test_staff_cannot_create_one_through_the_guardian_endpoint(self):
        self.assertEqual(self.create_authorization(user=self.teacher).status_code, 403)

    def test_the_window_must_end_in_the_future_after_it_starts_and_within_a_year(self):
        now = timezone.now()
        cases = {
            'ends in the past': (now - datetime.timedelta(hours=3), now - datetime.timedelta(hours=1)),
            'ends before it starts': (now + datetime.timedelta(hours=3), now + datetime.timedelta(hours=1)),
            'longer than a year': (now, now + datetime.timedelta(days=400)),
        }
        for label, (start, end) in cases.items():
            with self.subTest(label):
                res = self.create_authorization(valid_from=start.isoformat(), valid_to=end.isoformat())
                self.assertEqual(res.status_code, 400)
                self.assertEqual(res.json()['code'], 'PICKUP_INVALID_WINDOW')

    def test_a_blank_name_is_refused(self):
        res = self.create_authorization(person_name='   ')
        self.assertEqual(res.status_code, 400)

    def test_a_student_of_another_foundation_reads_as_not_found(self):
        other = Foundation.objects.create(legal_name="Lain", brand_name="Lain", npwp="02.000.000.0-000.000", address="X")
        person = Person.all_tenants.create(foundation_id=other.id, full_name="Anak Lain")
        school = School.all_tenants.create(foundation_id=other.id, name="SD Lain", npsn="20500009", level=School.LEVEL_SD)
        foreign = Student.all_tenants.create(foundation_id=other.id, school=school, person=person, nis="9")
        res = self.create_authorization(student_id=foreign.id)
        self.assertEqual(res.status_code, 404)


class ListAndRevokeTests(_Base):
    def test_a_linked_guardian_lists_authorizations_with_a_qr_only_while_usable(self):
        active = self.create_authorization().json()['id']
        used = self.create_authorization().json()['id']
        PickupAuthorization.all_tenants.filter(id=used).update(used_at=timezone.now())
        res = self.get(self.second_user, f'/api/v1/pickup-authorizations/?student_id={self.student.id}')
        self.assertEqual(res.status_code, 200)
        by_id = {row['id']: row for row in res.json()}
        self.assertEqual(by_id[active]['status'], 'ACTIVE')
        self.assertTrue(by_id[active]['qr_token'])
        self.assertEqual(by_id[used]['status'], 'USED')
        self.assertIsNone(by_id[used]['qr_token'])

    def test_a_stranger_cannot_list_them(self):
        self.create_authorization()
        res = self.get(self.stranger_user, f'/api/v1/pickup-authorizations/?student_id={self.student.id}')
        self.assertEqual(res.status_code, 404)

    def test_listing_needs_a_student_id(self):
        self.assertEqual(self.get(self.guardian_user, '/api/v1/pickup-authorizations/').status_code, 400)

    def test_any_linked_guardian_can_revoke_and_the_qr_dies(self):
        created = self.create_authorization().json()
        token = created['qr_token']
        res = self.post(self.second_user, f"/api/v1/pickup-authorizations/{created['id']}/revoke/", {})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['status'], 'REVOKED')
        self.assertIsNone(res.json()['qr_token'])
        verify = self.post(self.teacher, '/api/v1/pickup/verify/', {'qr_token': token})
        self.assertEqual(verify.status_code, 400)
        self.assertEqual(verify.json()['code'], 'PICKUP_REVOKED')

    def test_revoking_twice_is_harmless(self):
        created = self.create_authorization().json()
        url = f"/api/v1/pickup-authorizations/{created['id']}/revoke/"
        self.assertEqual(self.post(self.guardian_user, url, {}).status_code, 200)
        self.assertEqual(self.post(self.guardian_user, url, {}).status_code, 200)

    def test_a_stranger_cannot_revoke(self):
        created = self.create_authorization().json()
        res = self.post(self.stranger_user, f"/api/v1/pickup-authorizations/{created['id']}/revoke/", {})
        self.assertEqual(res.status_code, 404)

    def test_a_spent_one_time_authorization_cannot_be_revoked(self):
        created = self.create_authorization().json()
        PickupAuthorization.all_tenants.filter(id=created['id']).update(used_at=timezone.now())
        res = self.post(self.guardian_user, f"/api/v1/pickup-authorizations/{created['id']}/revoke/", {})
        self.assertEqual(res.json()['code'], 'PICKUP_ALREADY_USED')


class VerifyTests(_Base):
    def setUp(self):
        super().setUp()
        self.created = self.create_authorization().json()
        self.token = self.created['qr_token']

    def test_staff_see_the_person_and_student_but_only_the_last_four_phone_digits(self):
        res = self.post(self.teacher, '/api/v1/pickup/verify/', {'qr_token': self.token})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['person_name'], 'Pak Sopir')
        self.assertEqual(body['relation'], 'Sopir antar jemput')
        self.assertEqual(body['student_name'], 'Umar bin Khattab')
        self.assertEqual(body['phone_last4'], SECRET_PHONE[-4:])
        self.assertNotIn(SECRET_PHONE, res.content.decode())

    def test_verifying_changes_nothing(self):
        self.post(self.teacher, '/api/v1/pickup/verify/', {'qr_token': self.token})
        self.post(self.teacher, '/api/v1/pickup/verify/', {'qr_token': self.token})
        row = PickupAuthorization.all_tenants.get(id=self.created['id'])
        self.assertIsNone(row.used_at)
        self.assertFalse(PickupEvent.all_tenants.exists())

    def test_an_authorization_id_works_like_the_qr(self):
        res = self.post(self.teacher, '/api/v1/pickup/verify/', {'authorization_id': self.created['id']})
        self.assertEqual(res.status_code, 200)

    def test_a_scan_outside_the_window_is_window_expired(self):
        for label, change in {
            'already over': {'valid_from': timezone.now() - datetime.timedelta(hours=5), 'valid_to': timezone.now() - datetime.timedelta(hours=1)},
            'not started': {'valid_from': timezone.now() + datetime.timedelta(hours=1), 'valid_to': timezone.now() + datetime.timedelta(hours=5)},
        }.items():
            with self.subTest(label):
                PickupAuthorization.all_tenants.filter(id=self.created['id']).update(**change)
                res = self.post(self.teacher, '/api/v1/pickup/verify/', {'qr_token': self.token})
                self.assertEqual(res.status_code, 400)
                self.assertEqual(res.json()['code'], 'PICKUP_WINDOW_EXPIRED')

    def test_a_forged_or_edited_token_is_invalid(self):
        for label, token in {
            'garbage': 'not-a-token',
            'wrong salt': signing.dumps({'a': self.created['id']}, salt='some.other.salt'),
            'tampered': self.token[:-3] + ('abc' if not self.token.endswith('abc') else 'xyz'),
        }.items():
            with self.subTest(label):
                res = self.post(self.teacher, '/api/v1/pickup/verify/', {'qr_token': token})
                self.assertEqual(res.status_code, 404)
                self.assertEqual(res.json()['code'], 'PICKUP_INVALID_TOKEN')

    def test_a_correctly_signed_token_for_a_missing_authorization_is_invalid(self):
        token = signing.dumps({'a': 999999}, salt=TOKEN_SALT)
        self.assertEqual(self.post(self.teacher, '/api/v1/pickup/verify/', {'qr_token': token}).status_code, 404)

    def test_staff_of_another_school_cannot_verify_it(self):
        res = self.post(self.other_school_teacher, '/api/v1/pickup/verify/', {'qr_token': self.token})
        self.assertEqual(res.status_code, 404)

    def test_a_parent_cannot_verify(self):
        self.assertEqual(self.post(self.guardian_user, '/api/v1/pickup/verify/', {'qr_token': self.token}).status_code, 403)

    def test_send_exactly_one_of_token_or_id(self):
        self.assertEqual(self.post(self.teacher, '/api/v1/pickup/verify/', {}).status_code, 400)
        both = {'qr_token': self.token, 'authorization_id': self.created['id']}
        self.assertEqual(self.post(self.teacher, '/api/v1/pickup/verify/', both).status_code, 400)


class ReleaseTests(_Base):
    def release(self, user, **body):
        return self.post(user, '/api/v1/pickup/release/', body)

    def test_releasing_with_a_qr_records_the_event_and_spends_a_one_time_authorization(self):
        created = self.create_authorization().json()
        res = self.release(self.teacher, qr_token=created['qr_token'])
        self.assertEqual(res.status_code, 201, res.content)
        event = PickupEvent.all_tenants.get(id=res.json()['id'])
        self.assertEqual(event.method, PickupMethod.QR)
        self.assertEqual(event.picked_up_by, 'Pak Sopir')
        self.assertEqual(event.verified_by_id, self.teacher.id)
        self.assertEqual(event.authorized_by_guardian_id, self.guardian.id)
        self.assertEqual(event.student_id, self.student.id)
        self.assertIsNotNone(PickupAuthorization.all_tenants.get(id=created['id']).used_at)

    def test_a_one_time_qr_cannot_release_twice(self):
        created = self.create_authorization().json()
        self.assertEqual(self.release(self.teacher, qr_token=created['qr_token']).status_code, 201)
        again = self.release(self.teacher, qr_token=created['qr_token'])
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.json()['code'], 'PICKUP_ALREADY_USED')
        self.assertEqual(PickupEvent.all_tenants.count(), 1)

    def test_a_standing_authorization_can_be_used_more_than_once_inside_its_window(self):
        created = self.create_authorization(one_time=False).json()
        self.assertEqual(self.release(self.teacher, qr_token=created['qr_token']).status_code, 201)
        self.assertEqual(self.release(self.teacher, qr_token=created['qr_token']).status_code, 201)
        self.assertEqual(PickupEvent.all_tenants.count(), 2)
        self.assertIsNone(PickupAuthorization.all_tenants.get(id=created['id']).used_at)

    def test_release_outside_the_window_is_refused_and_records_nothing(self):
        created = self.create_authorization().json()
        PickupAuthorization.all_tenants.filter(id=created['id']).update(valid_to=timezone.now() - datetime.timedelta(minutes=1))
        res = self.release(self.teacher, qr_token=created['qr_token'])
        self.assertEqual(res.json()['code'], 'PICKUP_WINDOW_EXPIRED')
        self.assertFalse(PickupEvent.all_tenants.exists())

    def test_a_registered_guardian_with_can_pickup_is_released_to(self):
        res = self.release(self.teacher, student_id=self.student.id, guardian_id=self.guardian.id)
        self.assertEqual(res.status_code, 201, res.content)
        event = PickupEvent.all_tenants.get(id=res.json()['id'])
        self.assertEqual(event.method, PickupMethod.GUARDIAN)
        self.assertEqual(event.picked_up_by, 'Khalid bin Walid')
        self.assertIsNone(event.authorization_id)

    def test_a_guardian_without_can_pickup_is_not_authorised(self):
        res = self.release(self.teacher, student_id=self.student.id, guardian_id=self.second_guardian.id)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'PICKUP_NOT_AUTHORISED')
        self.assertFalse(PickupEvent.all_tenants.exists())

    def test_a_guardian_of_another_student_is_not_authorised(self):
        other_student = self._student(self.school, "Anak Lain", "25002")
        res = self.release(self.teacher, student_id=other_student.id, guardian_id=self.guardian.id)
        self.assertEqual(res.status_code, 403)

    def test_staff_of_another_school_cannot_release(self):
        created = self.create_authorization().json()
        self.assertEqual(self.release(self.other_school_teacher, qr_token=created['qr_token']).status_code, 404)
        by_guardian = self.release(self.other_school_teacher, student_id=self.student.id, guardian_id=self.guardian.id)
        self.assertEqual(by_guardian.status_code, 404)

    def test_the_body_must_pick_exactly_one_way(self):
        self.assertEqual(self.release(self.teacher).status_code, 400)
        created = self.create_authorization().json()
        both = self.release(
            self.teacher, qr_token=created['qr_token'], student_id=self.student.id, guardian_id=self.guardian.id,
        )
        self.assertEqual(both.status_code, 400)
        self.assertEqual(self.release(self.teacher, student_id=self.student.id).status_code, 400)

    def test_a_parent_cannot_release(self):
        created = self.create_authorization().json()
        self.assertEqual(self.release(self.guardian_user, qr_token=created['qr_token']).status_code, 403)

    def test_the_release_is_audited_without_the_persons_details(self):
        created = self.create_authorization().json()
        self.release(self.teacher, qr_token=created['qr_token'])
        event = AuditEvent.objects.get(action='attendance.pickup.completed')
        self.assertNotIn('Pak Sopir', str(event.diff))
        self.assertNotIn(SECRET_PHONE, str(event.diff))


class NotificationTests(_Base):
    def intents(self):
        # all_tenants: after an API request the ambient tenant context is cleared.
        return NotificationIntent.all_tenants.filter(foundation_id=self.foundation.id, category='DEPARTURE')

    def test_every_linked_guardian_is_told_including_one_who_was_not_involved(self):
        created = self.create_authorization(user=self.guardian_user).json()
        PickupAuthorization.all_tenants.filter(id=created['id']).update()
        self.post(self.teacher, '/api/v1/pickup/release/', {'qr_token': created['qr_token']})
        phones = {i.recipient_phone for i in self.intents()}
        self.assertEqual(phones, {self.guardian_user.phone_e164, self.second_user.phone_e164})
        for intent in self.intents():
            self.assertEqual(intent.template_key, 'attendance.pickup')
            self.assertEqual(intent.payload['picked_up_by'], 'Pak Sopir')
            self.assertEqual(intent.priority, 'HIGH')
            self.assertRegex(intent.payload['time'], r'^\d{2}:\d{2}$')

    def test_a_guardian_release_also_notifies_everyone(self):
        self.post(self.teacher, '/api/v1/pickup/release/', {'student_id': self.student.id, 'guardian_id': self.guardian.id})
        self.assertEqual(self.intents().count(), 2)

    def test_one_failing_recipient_never_undoes_the_release_or_stops_the_others(self):
        real = __import__('apps.notifications.services', fromlist=['dispatch_intent']).dispatch_intent
        calls = {'n': 0}

        def flaky(**kwargs):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('provider down for +628111222333')
            return real(**kwargs)

        with mock.patch('apps.notifications.services.dispatch_intent', side_effect=flaky):
            res = self.post(self.teacher, '/api/v1/pickup/release/', {'student_id': self.student.id, 'guardian_id': self.guardian.id})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(PickupEvent.all_tenants.count(), 1)
        self.assertEqual(self.intents().count(), 1)  # the second guardian still got theirs

    def test_the_failure_log_line_never_carries_the_exception_text(self):
        def boom(**kwargs):
            raise RuntimeError('provider down for +628111222333')

        with mock.patch('apps.notifications.services.dispatch_intent', side_effect=boom):
            with self.assertLogs('apps.attendance.pickup', level='WARNING') as logs:
                self.post(self.teacher, '/api/v1/pickup/release/', {'student_id': self.student.id, 'guardian_id': self.guardian.id})
        self.assertNotIn('+628111222333', '\n'.join(logs.output))
        self.assertIn('RuntimeError', '\n'.join(logs.output))

    def test_the_seeded_templates_render_for_both_pickup_notices_with_no_placeholder_left(self):
        call_command('seed_notification_templates', foundation_id=self.foundation.id, stdout=mock.MagicMock())
        payload = {
            'student_name': 'Umar bin Khattab', 'school_name': 'SD Pickup', 'picked_up_by': 'Pak Sopir',
            'time': '14:05', 'date': '2026-09-19', 'guardian_name': 'Khalid',
        }
        for key in ('attendance.pickup', 'attendance.pickup_override'):
            for channel in (ChannelType.WHATSAPP, ChannelType.PUSH):
                with self.subTest(key=key, channel=channel):
                    text = render_template_message(key, channel, self.foundation.id, payload)
                    combined = f"{text['subject']} {text['body']}"
                    self.assertNotIn('{', combined)
                    self.assertIn('Pak Sopir', combined)
                    self.assertIn('Umar bin Khattab', combined)


class OverrideTests(_Base):
    def override(self, user, **body):
        base = {'student_id': self.student.id, 'picked_up_by': 'Orang Tak Dikenal', 'reason': 'Ibu sakit, kerabat menjemput; wali dihubungi lewat telepon.'}
        base.update(body)
        return self.post(user, '/api/v1/pickup/override/', base)

    def test_a_school_admin_can_release_to_an_unauthorised_person_with_a_reason(self):
        res = self.override(self.school_admin)
        self.assertEqual(res.status_code, 201, res.content)
        event = PickupEvent.all_tenants.get(id=res.json()['id'])
        self.assertEqual(event.method, PickupMethod.OVERRIDE)
        self.assertEqual(event.picked_up_by, 'Orang Tak Dikenal')
        self.assertIn('wali dihubungi', event.override_reason)
        self.assertEqual(event.verified_by_id, self.school_admin.id)
        self.assertIsNone(event.authorization_id)

    def test_it_writes_a_high_priority_audit_event_with_the_reason(self):
        self.override(self.school_admin)
        event = AuditEvent.objects.get(action='attendance.pickup.override')
        self.assertEqual(event.diff['priority'], 'HIGH')
        self.assertIn('wali dihubungi', event.diff['reason'])
        self.assertEqual(event.school_id, self.school.id)

    def test_a_blank_reason_is_refused(self):
        for reason in ('', '   '):
            with self.subTest(reason=repr(reason)):
                res = self.override(self.school_admin, reason=reason)
                self.assertEqual(res.status_code, 400)
        self.assertFalse(PickupEvent.all_tenants.exists())
        self.assertFalse(AuditEvent.objects.filter(action='attendance.pickup.override').exists())

    def test_a_blank_person_is_refused(self):
        res = self.override(self.school_admin, picked_up_by='  ')
        self.assertEqual(res.status_code, 400)

    def test_only_a_school_admin_may_override_not_a_teacher(self):
        self.assertEqual(self.override(self.teacher).status_code, 403)
        self.assertEqual(self.override(self.guardian_user).status_code, 403)

    def test_a_school_admin_of_another_school_cannot_override_here(self):
        self.assertEqual(self.override(self.other_school_admin).status_code, 404)

    def test_the_override_tells_every_guardian_with_the_stronger_wording(self):
        self.override(self.school_admin)
        intents = NotificationIntent.all_tenants.filter(foundation_id=self.foundation.id, category='DEPARTURE')
        self.assertEqual(intents.count(), 2)
        self.assertEqual({i.template_key for i in intents}, {'attendance.pickup_override'})


class OverrideServiceInvariantTests(_Base):
    """The rules live in the service, not only in the serializer: a caller that skips the API must hit them too."""

    def test_the_service_refuses_a_missing_reason_on_its_own(self):
        from apps.attendance.pickup import PickupError, override_release
        for reason in ('', '   ', None):
            with self.subTest(reason=repr(reason)):
                with self.assertRaises(PickupError) as ctx:
                    override_release(staff_user=self.school_admin, student=self.student, picked_up_by='Seseorang', reason=reason)
                self.assertEqual(ctx.exception.code, 'PICKUP_OVERRIDE_REASON_REQUIRED')
        self.assertFalse(PickupEvent.all_tenants.exists())
        self.assertFalse(AuditEvent.objects.filter(action='attendance.pickup.override').exists())

    def test_the_service_refuses_a_missing_person_on_its_own(self):
        from apps.attendance.pickup import PickupError, override_release
        with self.assertRaises(PickupError) as ctx:
            override_release(staff_user=self.school_admin, student=self.student, picked_up_by='  ', reason='alasan')
        self.assertEqual(ctx.exception.code, 'PICKUP_PERSON_REQUIRED')

    def test_the_service_refuses_a_window_that_is_not_in_the_future_on_its_own(self):
        from apps.attendance.pickup import PickupError, create_pickup_authorization
        now = timezone.now()
        with self.assertRaises(PickupError) as ctx:
            create_pickup_authorization(
                user=self.guardian_user, student=self.student, person_name='Pak Sopir',
                valid_from=now - datetime.timedelta(hours=2), valid_to=now - datetime.timedelta(hours=1),
            )
        self.assertEqual(ctx.exception.code, 'PICKUP_INVALID_WINDOW')

    def test_a_pickup_token_is_never_valid_across_foundations(self):
        from apps.attendance.pickup import PickupError, load_authorization
        created = self.create_authorization().json()
        other = Foundation.objects.create(legal_name="Lain", brand_name="Lain", npwp="03.000.000.0-000.000", address="X")
        with self.assertRaises(PickupError) as ctx:
            load_authorization(other.id, qr_token=created['qr_token'])
        self.assertEqual(ctx.exception.code, 'PICKUP_INVALID_TOKEN')


@skipUnless(connection.vendor == 'mysql', 'real-concurrency test needs MySQL/InnoDB')
class OneTimeQrConcurrencyTests(_PickupFixture, TransactionTestCase):
    """Two scans of the same one-time QR at once must release exactly once (the row lock, spec/05 ATT-014)."""

    def test_two_simultaneous_scans_of_a_one_time_qr_release_the_student_once(self):
        from apps.attendance.pickup import PickupError, release_with_authorization
        from apps.wallet.tests.test_qr_mysql_concurrency import _run_together

        created = self.create_authorization().json()
        authorization_id = created['id']

        def scan():
            return release_with_authorization(
                staff_user=self.teacher, authorization=PickupAuthorization.objects.get(id=authorization_id),
            )

        outcomes = _run_together(self.foundation.id, scan, scan)
        winners = [result for result, error in outcomes if error is None]
        losers = [error for _result, error in outcomes if error is not None]
        self.assertEqual((len(winners), len(losers)), (1, 1), outcomes)
        self.assertIsInstance(losers[0], PickupError)
        self.assertEqual(losers[0].code, 'PICKUP_ALREADY_USED')
        self.assertEqual(PickupEvent.all_tenants.filter(authorization_id=authorization_id).count(), 1)


class PickupPhotoTests(_Base):
    """A guardian supplies `photo_key`; it must be their own confirmed pickup photo, never another tenant's file."""

    def stored(self, key='PRD/pickup_photo/abc_photo.jpg', user=None, foundation=None, purpose='pickup_photo', confirmed=True):
        from apps.core.models import StoredFile
        return StoredFile.all_tenants.create(
            foundation_id=(foundation or self.foundation).id, bucket='b', key=key, purpose=purpose,
            content_type='image/jpeg', size=1000, uploaded_by=str((user or self.guardian_user).id),
            confirmed_at=timezone.now() if confirmed else None,
        )

    def test_the_guardians_own_confirmed_photo_is_accepted(self):
        self.stored()
        res = self.create_authorization(photo_key='PRD/pickup_photo/abc_photo.jpg')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['photo_key'], 'PRD/pickup_photo/abc_photo.jpg')

    def test_a_key_that_is_not_a_stored_file_is_refused(self):
        res = self.create_authorization(photo_key='PRD/other_tenant/secret.pdf')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'PICKUP_INVALID_PHOTO')
        self.assertFalse(PickupAuthorization.all_tenants.exists())

    def test_another_users_upload_another_purpose_another_foundation_or_unconfirmed_are_all_refused(self):
        other = Foundation.objects.create(legal_name="Lain", brand_name="Lain", npwp="04.000.000.0-000.000", address="X")
        cases = {
            'uploaded by someone else': dict(key='k1', user=self.second_user),
            'wrong purpose': dict(key='k2', purpose='homework_submission'),
            'another foundation': dict(key='k3', foundation=other),
            'not confirmed': dict(key='k4', confirmed=False),
        }
        for label, kwargs in cases.items():
            with self.subTest(label):
                self.stored(**kwargs)
                res = self.create_authorization(photo_key=kwargs['key'])
                self.assertEqual(res.json().get('code'), 'PICKUP_INVALID_PHOTO', label)
        self.assertFalse(PickupAuthorization.all_tenants.exists())

    def test_a_soft_deleted_photo_is_refused(self):
        stored = self.stored()
        stored.deleted_at = timezone.now()
        stored.save()
        res = self.create_authorization(photo_key='PRD/pickup_photo/abc_photo.jpg')
        self.assertEqual(res.json()['code'], 'PICKUP_INVALID_PHOTO')

    def test_no_photo_is_fine(self):
        self.assertEqual(self.create_authorization().status_code, 201)

    def test_verify_returns_a_short_lived_link_for_a_valid_photo(self):
        self.stored()
        created = self.create_authorization(photo_key='PRD/pickup_photo/abc_photo.jpg').json()
        with mock.patch('apps.core.storage.generate_download_url', return_value='https://signed.example/photo') as sign:
            res = self.post(self.teacher, '/api/v1/pickup/verify/', {'qr_token': created['qr_token']})
        self.assertEqual(res.json()['photo_url'], 'https://signed.example/photo')
        self.assertEqual(sign.call_args.kwargs['expires_seconds'], 300)

    def test_an_arbitrary_key_stored_before_validation_existed_is_never_signed(self):
        created = self.create_authorization().json()
        PickupAuthorization.all_tenants.filter(id=created['id']).update(photo_key='PRD/other_tenant/secret.pdf')
        with mock.patch('apps.core.storage.generate_download_url', return_value='https://signed.example/x') as sign:
            res = self.post(self.teacher, '/api/v1/pickup/verify/', {'qr_token': created['qr_token']})
        self.assertEqual(res.json()['photo_url'], '')
        sign.assert_not_called()

    def test_a_photo_of_another_foundation_is_never_signed_even_if_the_key_matches(self):
        other = Foundation.objects.create(legal_name="Lain", brand_name="Lain", npwp="05.000.000.0-000.000", address="X")
        self.stored(key='PRD/pickup_photo/shared.jpg', foundation=other)
        created = self.create_authorization().json()
        PickupAuthorization.all_tenants.filter(id=created['id']).update(photo_key='PRD/pickup_photo/shared.jpg')
        with mock.patch('apps.core.storage.generate_download_url', return_value='https://signed.example/x') as sign:
            res = self.post(self.teacher, '/api/v1/pickup/verify/', {'qr_token': created['qr_token']})
        self.assertEqual(res.json()['photo_url'], '')
        sign.assert_not_called()

    def test_a_storage_failure_reads_as_no_photo_and_never_blocks_the_verify(self):
        self.stored()
        created = self.create_authorization(photo_key='PRD/pickup_photo/abc_photo.jpg').json()
        with mock.patch('apps.core.storage.generate_download_url', side_effect=RuntimeError('gcs down')):
            res = self.post(self.teacher, '/api/v1/pickup/verify/', {'qr_token': created['qr_token']})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['photo_url'], '')


class PickupPhotoUploadPurposeTests(_Base):
    UPLOAD = '/api/v1/files/uploads/'

    def initiate(self, user, **body):
        base = {'purpose': 'pickup_photo', 'filename': 'sopir.jpg', 'content_type': 'image/jpeg', 'size': 500_000}
        base.update(body)
        with mock.patch('apps.core.services.storage.generate_upload_url', return_value='https://upload.example/put'):
            return self.post(user, self.UPLOAD, base)

    def test_a_guardian_can_start_a_pickup_photo_upload(self):
        res = self.initiate(self.guardian_user)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['purpose'], 'pickup_photo')

    def test_only_small_jpeg_or_png_images_are_accepted(self):
        self.assertEqual(self.initiate(self.guardian_user, content_type='image/png').status_code, 201)
        self.assertEqual(self.initiate(self.guardian_user, content_type='application/pdf').status_code, 400)
        self.assertEqual(self.initiate(self.guardian_user, size=3 * 1024 * 1024).status_code, 400)

    def test_someone_without_pickup_authorize_cannot_start_one(self):
        self.assertEqual(self.initiate(self.teacher).status_code, 403)


class PickupConsoleWebTests(_Base):
    """The staff screen at /web/attendance/pickup/: same services as the API, so this covers wiring and scoping."""
    PAGE = '/web/attendance/pickup/'

    def setUp(self):
        super().setUp()
        for user, school in ((self.teacher, self.school), (self.school_admin, self.school),
                             (self.other_school_teacher, self.other_school), (self.other_school_admin, self.other_school)):
            Staff.all_tenants.create(
                foundation_id=self.foundation.id, person=Person.all_tenants.create(
                    foundation_id=self.foundation.id, full_name=user.full_name),
                user=user, school=school, join_date=timezone.localdate(),
            )
        self.scope = f'?school_id={self.school.id}'

    def web_post(self, user, name, data, school=None):
        self.client.force_authenticate(user=user)
        set_current_foundation_id(self.foundation.id)
        return self.client.post(f'{self.PAGE}{name}/?school_id={(school or self.school).id}', data)

    def web_get(self, user, query=''):
        self.client.force_authenticate(user=user)
        set_current_foundation_id(self.foundation.id)
        return self.client.get(f'{self.PAGE}{query}')

    def flashes(self, res):
        return [str(m) for m in res.wsgi_request._messages]

    def token(self):
        set_current_foundation_id(self.foundation.id)
        return self.create_authorization().json()['qr_token']

    def test_verify_shows_who_the_code_releases_the_student_to_and_changes_nothing(self):
        token = self.token()
        res = self.web_post(self.teacher, 'verify', {'qr_token': token})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Pak Sopir')
        self.assertContains(res, 'Umar bin Khattab')
        self.assertContains(res, '2333')  # last four digits only
        self.assertNotContains(res, SECRET_PHONE)
        self.assertFalse(PickupEvent.all_tenants.exists())
        self.assertFalse(PickupAuthorization.all_tenants.get().used_at)

    def test_the_verify_page_signs_the_photo_only_for_a_confirmed_one(self):
        from apps.core.models import StoredFile
        StoredFile.all_tenants.create(
            foundation_id=self.foundation.id, bucket='b', key='PRD/pickup_photo/x.jpg', purpose='pickup_photo',
            content_type='image/jpeg', size=1, uploaded_by=str(self.guardian_user.id), confirmed_at=timezone.now(),
        )
        set_current_foundation_id(self.foundation.id)
        token = self.create_authorization(photo_key='PRD/pickup_photo/x.jpg').json()['qr_token']
        with mock.patch('apps.core.storage.generate_download_url', return_value='https://signed.example/p.jpg'):
            res = self.web_post(self.teacher, 'verify', {'qr_token': token})
        self.assertContains(res, 'https://signed.example/p.jpg')

    def test_a_bad_expired_revoked_or_other_school_code_is_refused_with_a_message(self):
        token = self.token()
        res = self.web_post(self.teacher, 'verify', {'qr_token': 'garbage'})
        self.assertEqual(res.status_code, 302)
        self.assertIn('Kode QR penjemputan tidak valid.', self.flashes(res))
        # a token that is valid but belongs to another school's student reads as invalid too
        res = self.web_post(self.other_school_teacher, 'verify', {'qr_token': token}, school=self.other_school)
        self.assertIn('Kode QR penjemputan tidak valid.', self.flashes(res))
        PickupAuthorization.all_tenants.update(revoked_at=timezone.now())
        res = self.web_post(self.teacher, 'verify', {'qr_token': token})
        self.assertTrue(any('dicabut' in m for m in self.flashes(res)))

    def test_release_by_authorization_records_the_event_and_spends_a_one_time_code(self):
        self.token()
        auth = PickupAuthorization.all_tenants.get()
        res = self.web_post(self.teacher, 'release', {'authorization_id': auth.id})
        self.assertEqual(res.status_code, 302)
        self.assertEqual(PickupEvent.all_tenants.get().method, PickupMethod.QR)
        self.assertTrue(any('diserahkan kepada Pak Sopir' in m for m in self.flashes(res)))
        again = self.web_post(self.teacher, 'release', {'authorization_id': auth.id})
        self.assertEqual(PickupEvent.all_tenants.count(), 1)
        self.assertTrue(any('sudah digunakan' in m for m in self.flashes(again)))

    def test_release_to_a_guardian_on_file_and_refuses_one_without_can_pickup(self):
        res = self.web_post(self.teacher, 'release', {'nis': self.student.nis, 'guardian_id': self.guardian.id})
        self.assertEqual(PickupEvent.all_tenants.get().method, PickupMethod.GUARDIAN)
        res = self.web_post(self.teacher, 'release', {'nis': self.student.nis, 'guardian_id': self.second_guardian.id})
        self.assertEqual(PickupEvent.all_tenants.count(), 1)
        self.assertTrue(any('tidak terdaftar' in m for m in self.flashes(res)))

    def test_staff_of_another_school_can_neither_release_nor_revoke(self):
        self.token()
        auth = PickupAuthorization.all_tenants.get()
        for action in ('release', 'revoke'):
            res = self.web_post(self.other_school_teacher, action, {'authorization_id': auth.id}, school=self.other_school)
            self.assertIn('Kode QR penjemputan tidak valid.', self.flashes(res))
        # and cannot act for a school they are not assigned to
        res = self.web_post(self.other_school_teacher, 'release', {'authorization_id': auth.id})
        self.assertRedirects(res, reverse('web-console-home'), fetch_redirect_response=False)
        self.assertFalse(PickupEvent.all_tenants.exists())
        self.assertIsNone(PickupAuthorization.all_tenants.get().revoked_at)

    def test_revoke_withdraws_the_authorization(self):
        self.token()
        auth = PickupAuthorization.all_tenants.get()
        res = self.web_post(self.teacher, 'revoke', {'authorization_id': auth.id, 'nis': self.student.nis})
        self.assertEqual(res.status_code, 302)
        self.assertIsNotNone(PickupAuthorization.all_tenants.get().revoked_at)
        self.assertIn('Otorisasi penjemputan dicabut.', self.flashes(res))

    def test_only_a_school_admin_can_override_and_the_reason_is_mandatory(self):
        data = {'nis': self.student.nis, 'picked_up_by': 'Paman Budi', 'reason': 'Orang tua tidak dapat dihubungi'}
        res = self.web_post(self.teacher, 'override', data)
        self.assertEqual(res.status_code, 302)  # sent home: a teacher lacks pickup.override
        self.assertFalse(PickupEvent.all_tenants.exists())
        res = self.web_post(self.school_admin, 'override', {**data, 'reason': '  '})
        self.assertFalse(PickupEvent.all_tenants.exists())
        self.assertTrue(any('Alasan pengecualian wajib' in m for m in self.flashes(res)))
        self.web_post(self.school_admin, 'override', data)
        event = PickupEvent.all_tenants.get()
        self.assertEqual((event.method, event.picked_up_by), (PickupMethod.OVERRIDE, 'Paman Budi'))
        self.assertTrue(AuditEvent.objects.filter(action='attendance.pickup.override').exists())

    def test_the_page_lists_guardians_and_live_authorizations_and_shows_override_only_to_admins(self):
        self.token()
        res = self.web_get(self.teacher, f'{self.scope}&nis={self.student.nis}')
        self.assertContains(res, 'Khalid bin Walid')
        self.assertNotContains(res, 'Aisyah binti Abu Bakar')  # no can_pickup
        self.assertContains(res, 'Pak Sopir')
        self.assertNotContains(res, 'Serahkan dengan pengecualian')
        res = self.web_get(self.school_admin, f'{self.scope}&nis={self.student.nis}')
        self.assertContains(res, 'Serahkan dengan pengecualian')

    def test_a_spent_or_revoked_authorization_is_no_longer_listed(self):
        self.token()
        PickupAuthorization.all_tenants.update(revoked_at=timezone.now())
        res = self.web_get(self.teacher, f'{self.scope}&nis={self.student.nis}')
        self.assertNotContains(res, 'Pak Sopir')

    def test_the_qr_token_never_appears_in_a_page_or_url(self):
        token = self.token()
        res = self.web_get(self.teacher, f'{self.scope}&nis={self.student.nis}')
        self.assertNotContains(res, token)
        res = self.web_post(self.teacher, 'verify', {'qr_token': token})
        self.assertNotContains(res, token)

    def test_a_guardian_cannot_reach_the_screen(self):
        for method, url in (('get', self.PAGE), ('post', f'{self.PAGE}release/')):
            self.client.force_authenticate(user=self.guardian_user)
            set_current_foundation_id(self.foundation.id)
            res = getattr(self.client, method)(url)
            self.assertIn(res.status_code, (302, 403, 404))
        self.assertFalse(PickupEvent.all_tenants.exists())

    def test_the_gate_page_links_to_the_pickup_screen(self):
        self.client.force_authenticate(user=self.teacher)
        set_current_foundation_id(self.foundation.id)
        self.assertContains(self.client.get('/web/attendance/gate/'), self.PAGE)
