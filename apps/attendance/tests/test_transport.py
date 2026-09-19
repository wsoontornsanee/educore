"""School transport (spec/05 §6, ATT-019..ATT-022)."""
import datetime
import uuid
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.attendance.models import (
    BusBoardingEvent, BusRoute, BusRun, BusRunStatus, BusStop, BusStopAssignment, Credential, CredentialStatus,
    CredentialType,
)
from apps.attendance.tests.test_pickup import _PickupFixture
from apps.attendance.transport import DEFAULT_SPEED_MPS, distance_m
from apps.core.models import AuditEvent, JobRun
from apps.hardware.models import Device, DeviceClass, DeviceEventStaging, DeviceEventStagingStatus
from apps.hardware.services import ingest_device_events
from apps.identity.models import Foundation, GuardianLink, RoleAssignment
from apps.notifications.models import NotificationCategory, NotificationIntent
from educore.middleware.tenancy import tenant_context

STOP_LAT, STOP_LNG = Decimal('-6.200000'), Decimal('106.800000')
FAR = Decimal('-6.222000')     # ~2.4 km south of the stop: 350 s at the default speed
NEAR = Decimal('-6.215000')    # ~1.7 km: 240 s at the default speed, inside a 5-minute lead
AT_STOP = Decimal('-6.200500')  # ~55 m: inside the 150 m geofence


class _Base(_PickupFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.route = self.make_route()
        self.stop = self.make_stop(self.route)
        self.assign(self.stop, self.student)
        self.card = self.make_card(self.student, 'CARD-UMAR')

    # -- fixtures ---------------------------------------------------------------------------------
    def make_route(self, school=None, name='Rute Timur'):
        return BusRoute.all_tenants.create(foundation_id=self.foundation.id, school=school or self.school, name=name)

    def make_stop(self, route, name='Jl. Melati', lat=STOP_LAT, lng=STOP_LNG, radius=150, sequence=0):
        return BusStop.all_tenants.create(
            foundation_id=self.foundation.id, route=route, name=name, latitude=lat, longitude=lng,
            radius_m=radius, sequence=sequence,
        )

    def assign(self, stop, student):
        return BusStopAssignment.all_tenants.create(
            foundation_id=self.foundation.id, route=stop.route, stop=stop, student=student,
        )

    def make_card(self, student, uid):
        return Credential.all_tenants.create(
            foundation_id=self.foundation.id, student=student, type=CredentialType.RFID, uid=uid,
            status=CredentialStatus.ACTIVE,
        )

    def start(self, direction='TO_SCHOOL', user=None, route=None):
        return self.post(user or self.teacher, f'/api/v1/bus/routes/{(route or self.route).id}/runs/', {'direction': direction})

    def new_run(self, direction='TO_SCHOOL'):
        res = self.start(direction)
        self.assertEqual(res.status_code, 201, res.content)
        return BusRun.all_tenants.get(id=res.json()['id'])

    def tap(self, run, uid='CARD-UMAR', kind=None, user=None, **extra):
        body = {
            'run_id': run.id, 'event_uuid': str(uuid.uuid4()), 'occurred_at': timezone.now().isoformat(),
            'raw_uid': uid, 'latitude': '-6.201000', 'longitude': '106.801000',
        }
        if kind:
            body['kind'] = kind
        body.update(extra)
        return self.post(user or self.teacher, '/api/v1/bus/events/', {'events': [body]})

    def position(self, run, lat, lng='106.800000', speed=None, user=None):
        body = {'latitude': str(lat), 'longitude': lng}
        if speed is not None:
            body['speed_mps'] = speed
        return self.post(user or self.teacher, f'/api/v1/bus/runs/{run.id}/position/', body)

    def notices(self, category):
        return NotificationIntent.all_tenants.filter(foundation_id=self.foundation.id, category=category)


class SetupTests(_Base):
    def test_a_school_admin_creates_a_route_and_it_is_audited(self):
        res = self.post(self.school_admin, '/api/v1/bus/routes/', {'school_id': self.school.id, 'name': 'Rute Barat'})
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['approach_minutes'], 5)
        self.assertTrue(AuditEvent.objects.filter(action='attendance.bus_route.created').exists())

    def test_a_teacher_cannot_set_up_routes(self):
        res = self.post(self.teacher, '/api/v1/bus/routes/', {'school_id': self.school.id, 'name': 'Rute Barat'})
        self.assertEqual(res.status_code, 403)

    def test_a_guardian_cannot_list_routes(self):
        self.assertEqual(self.get(self.guardian_user, '/api/v1/bus/routes/').status_code, 403)

    def test_an_admin_of_another_school_cannot_create_or_see_the_route(self):
        res = self.post(self.other_school_admin, '/api/v1/bus/routes/', {'school_id': self.school.id, 'name': 'X'})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(self.get(self.other_school_admin, f'/api/v1/bus/routes/{self.route.id}/').status_code, 404)
        self.assertEqual(self.get(self.other_school_admin, '/api/v1/bus/routes/').json(), [])

    def test_another_foundation_gets_404_for_every_route_and_run_endpoint(self):
        other = Foundation.objects.create(legal_name="Yayasan Lain", brand_name="Lain", npwp="02.999.111.2-000.000", address="X")
        outsider = self._user("+6281399990001", "Admin Yayasan Lain")
        RoleAssignment.objects.create(
            foundation_id=other.id, user=outsider, role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=other.id,
        )
        outsider.foundation_id = other.id
        outsider.save(update_fields=['foundation_id'])
        run = self.new_run()
        from educore.middleware.tenancy import set_current_foundation_id
        for method, path, body in [
            ('get', f'/api/v1/bus/routes/{self.route.id}/', None),
            ('post', f'/api/v1/bus/routes/{self.route.id}/runs/', {'direction': 'TO_SCHOOL'}),
            ('post', f'/api/v1/bus/runs/{run.id}/end/', {}),
            ('post', f'/api/v1/bus/runs/{run.id}/position/', {'latitude': '-6.2', 'longitude': '106.8'}),
            ('delete', f'/api/v1/bus/stops/{self.stop.id}/', None),
        ]:
            set_current_foundation_id(other.id)
            self.client.force_authenticate(user=outsider)
            res = getattr(self.client, method)(path, body, format='json') if body is not None else getattr(self.client, method)(path)
            self.assertEqual(res.status_code, 404, (method, path, res.content))

    def test_a_route_name_is_unique_within_a_school(self):
        res = self.post(self.school_admin, '/api/v1/bus/routes/', {'school_id': self.school.id, 'name': 'Rute Timur'})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'BUS_ROUTE_NAME_TAKEN')

    def test_lead_time_is_configurable_per_route(self):
        res = self.client_patch(self.school_admin, f'/api/v1/bus/routes/{self.route.id}/', {'approach_minutes': 10})
        self.assertEqual(res.status_code, 200, res.content)
        self.route.refresh_from_db()
        self.assertEqual(self.route.approach_minutes, 10)

    def client_patch(self, user, path, data):
        self.client.force_authenticate(user=user)
        return self.client.patch(path, data, format='json')

    def test_stops_are_added_in_order_and_students_assigned_and_moved(self):
        res = self.post(self.school_admin, f'/api/v1/bus/routes/{self.route.id}/stops/', {
            'name': 'Pasar', 'latitude': '-6.21', 'longitude': '106.81',
        })
        self.assertEqual(res.status_code, 201, res.content)
        second = BusStop.all_tenants.get(name='Pasar')
        self.assertEqual(second.sequence, 1)
        res = self.post(self.school_admin, f'/api/v1/bus/stops/{second.id}/students/', {'student_id': self.student.id})
        self.assertEqual(res.status_code, 201, res.content)
        rows = BusStopAssignment.all_tenants.filter(student=self.student, deleted_at__isnull=True)
        self.assertEqual([r.stop_id for r in rows], [second.id])  # moved, not duplicated

    def test_a_student_of_another_school_cannot_be_assigned(self):
        outsider = self._student(self.other_school, "Anak Sekolah Lain", "99001")
        res = self.post(self.school_admin, f'/api/v1/bus/stops/{self.stop.id}/students/', {'student_id': outsider.id})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'BUS_STUDENT_OTHER_SCHOOL')

    def test_removing_a_stop_removes_its_assignments_without_deleting_rows(self):
        self.client.force_authenticate(user=self.school_admin)
        self.assertEqual(self.client.delete(f'/api/v1/bus/stops/{self.stop.id}/').status_code, 204)
        self.assertIsNotNone(BusStop.all_tenants.with_deleted().get(id=self.stop.id).deleted_at)
        self.assertFalse(BusStopAssignment.all_tenants.filter(deleted_at__isnull=True).exists())

    def test_a_student_can_be_taken_off_a_stop(self):
        self.client.force_authenticate(user=self.school_admin)
        url = f'/api/v1/bus/stops/{self.stop.id}/students/{self.student.id}/'
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(self.client.delete(url).status_code, 404)

    def test_route_detail_lists_stops_with_their_students(self):
        body = self.get(self.school_admin, f'/api/v1/bus/routes/{self.route.id}/').json()
        self.assertEqual(body['stops'][0]['students'], [{'id': self.student.id, 'name': 'Umar bin Khattab'}])


class RunTests(_Base):
    def test_a_route_has_one_active_run_at_a_time(self):
        self.assertEqual(self.start().status_code, 201)
        res = self.start('FROM_SCHOOL')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'BUS_RUN_ACTIVE')

    def test_a_guardian_cannot_start_a_run_and_a_teacher_of_another_school_gets_404(self):
        self.assertEqual(self.start(user=self.guardian_user).status_code, 403)
        self.assertEqual(self.start(user=self.other_school_teacher).status_code, 404)

    def test_an_inactive_route_cannot_start_a_run(self):
        BusRoute.all_tenants.filter(id=self.route.id).update(is_active=False)
        self.assertEqual(self.start().json()['code'], 'BUS_ROUTE_INACTIVE')

    def test_position_of_an_ended_run_is_refused(self):
        run = self.new_run()
        self.post(self.teacher, f'/api/v1/bus/runs/{run.id}/end/', {})
        res = self.position(run, NEAR)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'BUS_RUN_NOT_ACTIVE')

    def test_ending_a_run_twice_is_harmless(self):
        run = self.new_run()
        self.assertEqual(self.post(self.teacher, f'/api/v1/bus/runs/{run.id}/end/', {}).status_code, 200)
        self.assertEqual(self.post(self.teacher, f'/api/v1/bus/runs/{run.id}/end/', {}).json()['status'], 'COMPLETED')
        self.assertEqual(AuditEvent.objects.filter(action='attendance.bus_run.ended').count(), 1)


class BoardAlightTests(_Base):
    def test_a_card_tap_boards_and_the_next_alights(self):
        run = self.new_run()
        first = self.tap(run).json()
        second = self.tap(run).json()
        self.assertEqual(first['results'][0]['status'], 'RECORDED')
        self.assertEqual(second['results'][0]['status'], 'RECORDED')
        kinds = list(BusBoardingEvent.all_tenants.order_by('id').values_list('kind', flat=True))
        self.assertEqual(kinds, ['BOARD', 'ALIGHT'])

    def test_the_event_is_geotagged(self):
        run = self.new_run()
        self.tap(run)
        event = BusBoardingEvent.all_tenants.get()
        self.assertEqual((event.latitude, event.longitude), (Decimal('-6.201000'), Decimal('106.801000')))

    def test_a_retry_with_the_same_event_uuid_records_nothing_new(self):
        run = self.new_run()
        body = {
            'run_id': run.id, 'event_uuid': str(uuid.uuid4()), 'occurred_at': timezone.now().isoformat(),
            'raw_uid': 'CARD-UMAR',
        }
        self.post(self.teacher, '/api/v1/bus/events/', {'events': [body]})
        res = self.post(self.teacher, '/api/v1/bus/events/', {'events': [body]}).json()
        self.assertEqual(res['results'][0]['status'], 'DUPLICATE')
        self.assertEqual(BusBoardingEvent.all_tenants.count(), 1)

    def test_a_second_board_without_alighting_is_a_duplicate(self):
        run = self.new_run()
        self.tap(run, kind='BOARD')
        res = self.tap(run, kind='BOARD').json()
        self.assertEqual(res['results'][0]['status'], 'DUPLICATE')
        self.assertEqual(res['results'][0]['reason'], 'ALREADY_BOARD')

    def test_an_unknown_or_revoked_card_is_rejected(self):
        run = self.new_run()
        self.assertEqual(self.tap(run, uid='NOPE').json()['results'][0]['reason'], 'CREDENTIAL_INVALID')
        Credential.all_tenants.filter(uid='CARD-UMAR').update(status=CredentialStatus.REVOKED)
        self.assertEqual(self.tap(run).json()['results'][0]['status'], 'REJECTED')
        self.assertEqual(BusBoardingEvent.all_tenants.count(), 0)

    def test_a_card_of_a_student_of_another_school_is_rejected(self):
        outsider = self._student(self.other_school, "Anak Sekolah Lain", "99001")
        self.make_card(outsider, 'CARD-OTHER')
        res = self.tap(self.new_run(), uid='CARD-OTHER').json()
        self.assertEqual(res['results'][0]['reason'], 'NOT_A_STUDENT_OF_SCHOOL')

    def test_a_run_of_another_school_is_rejected_for_a_teacher_there(self):
        run = self.new_run()
        res = self.tap(run, user=self.other_school_teacher).json()
        self.assertEqual(res['results'][0]['reason'], 'RUN_NOT_FOUND')

    def test_an_offline_replay_keeps_its_original_time_and_may_arrive_after_the_run_ended(self):
        run = self.new_run()
        self.post(self.teacher, f'/api/v1/bus/runs/{run.id}/end/', {})
        earlier = (timezone.now() - datetime.timedelta(minutes=20)).isoformat()
        res = self.tap(run, occurred_at=earlier, replayed=True).json()
        self.assertEqual(res['results'][0]['status'], 'RECORDED')
        event = BusBoardingEvent.all_tenants.get()
        self.assertTrue(event.replayed)
        self.assertLess(event.occurred_at, timezone.now() - datetime.timedelta(minutes=19))

    def test_a_batch_is_applied_in_time_order(self):
        run = self.new_run()
        now = timezone.now()
        events = [
            {'run_id': run.id, 'event_uuid': str(uuid.uuid4()), 'raw_uid': 'CARD-UMAR', 'occurred_at': (now + datetime.timedelta(minutes=5)).isoformat()},
            {'run_id': run.id, 'event_uuid': str(uuid.uuid4()), 'raw_uid': 'CARD-UMAR', 'occurred_at': now.isoformat()},
        ]
        self.post(self.teacher, '/api/v1/bus/events/', {'events': events})
        kinds = list(BusBoardingEvent.all_tenants.order_by('occurred_at').values_list('kind', flat=True))
        self.assertEqual(kinds, ['BOARD', 'ALIGHT'])

    def test_an_event_needs_a_card_or_a_student_id(self):
        run = self.new_run()
        res = self.post(self.teacher, '/api/v1/bus/events/', {'events': [{
            'run_id': run.id, 'event_uuid': str(uuid.uuid4()), 'occurred_at': timezone.now().isoformat(),
        }]})
        self.assertEqual(res.status_code, 400)

    def test_a_guardian_cannot_post_events(self):
        run = self.new_run()
        self.assertEqual(self.tap(run, user=self.guardian_user).status_code, 403)


class DeviceIngestTests(_Base):
    def setUp(self):
        super().setUp()
        self.handheld = Device.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, device_code='HH-01', name='Handheld Bus 1',
            device_class=DeviceClass.HANDHELD,
        )

    def stage(self, run, uid='CARD-UMAR'):
        return self.post(self.school_admin, '/api/v1/device/events', {
            'school_id': self.school.id,
            'batch': [{
                'event_uuid': str(uuid.uuid4()), 'device_id': self.handheld.id, 'occurred_at': timezone.now().isoformat(),
                'raw_uid': uid, 'bus_run_id': run.id, 'latitude': '-6.201000', 'longitude': '106.801000',
            }],
        })

    def test_a_staged_handheld_tap_becomes_a_board_event_not_a_gate_scan(self):
        from apps.attendance.models import GateEvent
        run = self.new_run()
        self.assertEqual(self.stage(run).status_code, 202)
        result = ingest_device_events()
        self.assertEqual(result['applied'], 1)
        event = BusBoardingEvent.all_tenants.get()
        self.assertEqual((event.kind, event.student_id, event.run_id, event.device_id), ('BOARD', self.student.id, run.id, self.handheld.id))
        self.assertEqual(GateEvent.all_tenants.count(), 0)

    def test_a_refused_tap_fails_its_staging_row_with_the_reason(self):
        run = self.new_run()
        self.stage(run, uid='NOPE')
        ingest_device_events()
        row = DeviceEventStaging.all_tenants.get()
        self.assertEqual(row.status, DeviceEventStagingStatus.FAILED)
        self.assertIn('CREDENTIAL_INVALID', row.error_text)

    def test_a_plain_gate_event_still_goes_to_the_gate_path(self):
        from apps.attendance.models import GateEvent
        self.post(self.school_admin, '/api/v1/device/events', {
            'school_id': self.school.id,
            'batch': [{
                'event_uuid': str(uuid.uuid4()), 'device_id': self.handheld.id,
                'occurred_at': timezone.now().isoformat(), 'raw_uid': 'CARD-UMAR', 'direction': 'IN',
            }],
        })
        ingest_device_events()
        self.assertEqual(GateEvent.all_tenants.count(), 1)
        self.assertEqual(BusBoardingEvent.all_tenants.count(), 0)


class ApproachNoticeTests(_Base):
    def approach(self):
        return self.notices(NotificationCategory.BUS_APPROACH)

    def test_distance_is_about_right(self):
        self.assertAlmostEqual(distance_m(-6.2, 106.8, -6.21, 106.8), 1112, delta=10)

    def test_a_bus_more_than_the_lead_time_away_notifies_nobody(self):
        run = self.new_run()
        self.assertEqual(self.position(run, FAR).status_code, 200)
        self.assertEqual(self.approach().count(), 0)

    def test_a_bus_within_the_lead_time_notifies_every_guardian_once(self):
        run = self.new_run()
        self.position(run, NEAR)
        self.assertEqual(self.approach().count(), 2)
        notice = self.approach().filter(recipient_user=self.guardian_user).get()
        self.assertEqual(notice.template_key, 'attendance.bus_approaching_pickup')
        self.assertEqual(notice.payload['stop_name'], 'Jl. Melati')
        self.assertEqual(notice.payload['student_name'], 'Umar bin Khattab')
        self.position(run, AT_STOP)
        self.position(run, NEAR)
        self.assertEqual(self.approach().count(), 2)  # the stop is announced once per run
        run.refresh_from_db()
        self.assertEqual(run.announced_stop_ids, [self.stop.id])

    def test_the_lead_time_is_the_routes_setting(self):
        BusRoute.all_tenants.filter(id=self.route.id).update(approach_minutes=10)
        run = self.new_run()
        self.position(run, FAR)  # 350 s is inside 10 minutes
        self.assertEqual(self.approach().count(), 2)

    def test_a_reported_speed_shortens_the_estimate(self):
        run = self.new_run()
        self.position(run, FAR, speed=DEFAULT_SPEED_MPS * 2)  # 350 s at 7 m/s, 175 s at 14 m/s
        self.assertEqual(self.approach().count(), 2)

    def test_a_nearly_still_bus_uses_the_default_speed(self):
        run = self.new_run()
        self.position(run, FAR, speed=0.3)
        self.assertEqual(self.approach().count(), 0)

    def test_inside_the_stop_radius_always_counts(self):
        BusRoute.all_tenants.filter(id=self.route.id).update(approach_minutes=1)
        run = self.new_run()
        self.position(run, AT_STOP)
        self.assertEqual(self.approach().count(), 2)

    def test_a_student_already_on_the_bus_is_not_told_the_bus_is_coming_on_a_run_to_school(self):
        run = self.new_run('TO_SCHOOL')
        self.tap(run)
        self.position(run, NEAR)
        self.assertEqual(self.approach().count(), 0)

    def test_on_a_run_home_a_student_on_the_bus_is_told_and_one_already_dropped_is_not(self):
        run = self.new_run('FROM_SCHOOL')
        self.tap(run)  # boards
        self.position(run, NEAR)
        notice = self.approach().first()
        self.assertEqual(notice.template_key, 'attendance.bus_approaching_dropoff')
        self.assertEqual(self.approach().count(), 2)

        second = self.make_stop(self.route, name='Jl. Kenanga', lat=Decimal('-6.300000'), sequence=1)
        other = self._student(self.school, "Ali bin Abi Thalib", "25002")
        self.assign(second, other)
        self.make_card(other, 'CARD-ALI')
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id, guardian=self.guardian, student=other,
            relation=GuardianLink.RELATION_FATHER, can_pickup=True,
        )
        self.tap(run, uid='CARD-ALI', kind='BOARD')
        self.tap(run, uid='CARD-ALI', kind='ALIGHT')
        self.position(run, Decimal('-6.299500'))
        self.assertEqual(self.approach().filter(payload__student_id=other.id).count(), 0)

    def test_only_students_at_the_stop_are_told(self):
        other_stop = self.make_stop(self.route, name='Jl. Jauh', lat=Decimal('-6.500000'), sequence=1)
        other = self._student(self.school, "Zaid bin Haritsah", "25003")
        self.assign(other_stop, other)
        run = self.new_run()
        self.position(run, NEAR)
        self.assertEqual({n.payload['student_id'] for n in self.approach()}, {self.student.id})

    def test_a_failing_recipient_never_breaks_the_position_report_or_the_others(self):
        run = self.new_run()
        real = __import__('apps.notifications.services', fromlist=['dispatch_intent']).dispatch_intent
        calls = {'n': 0}

        def flaky(**kwargs):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('provider down for +628111222333')
            return real(**kwargs)

        with mock.patch('apps.notifications.services.dispatch_intent', side_effect=flaky):
            with self.assertLogs('apps.attendance.transport', level='WARNING') as logs:
                res = self.position(run, NEAR)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.approach().count(), 1)
        self.assertNotIn('628111222333', '\n'.join(logs.output))

    def test_the_notice_carries_no_phone_number(self):
        run = self.new_run()
        self.position(run, NEAR)
        self.assertNotIn('+62813', str(self.approach().first().payload))

    def test_the_position_is_stored(self):
        run = self.new_run()
        self.position(run, NEAR, speed=8.5)
        run.refresh_from_db()
        self.assertEqual(run.last_latitude, NEAR)
        self.assertEqual(run.last_speed_mps, 8.5)
        self.assertIsNotNone(run.last_position_at)

    def test_out_of_range_coordinates_are_refused(self):
        run = self.new_run()
        self.assertEqual(self.position(run, '95.0').status_code, 400)


class UnaccountedStudentTests(_Base):
    def setUp(self):
        super().setUp()
        self.admin2 = self._staff(RoleAssignment.ROLE_SCHOOL_ADMIN, self.school, "+6281300000030")
        self.second_student = self._student(self.school, "Ali bin Abi Thalib", "25002")
        self.assign(self.stop, self.second_student)
        self.make_card(self.second_student, 'CARD-ALI')
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id, guardian=self.second_guardian, student=self.second_student,
            relation=GuardianLink.RELATION_MOTHER, can_pickup=True,
        )

    def alerts(self):
        return self.notices(NotificationCategory.EMERGENCY)

    def end(self, run, user=None):
        return self.post(user or self.teacher, f'/api/v1/bus/runs/{run.id}/end/', {})

    def test_a_student_boarded_and_not_alighted_alerts_admins_and_guardians_at_once(self):
        run = self.new_run()
        self.tap(run)
        self.tap(run, uid='CARD-ALI')
        self.tap(run, uid='CARD-ALI')  # Ali alights
        self.end(run)
        recipients = {n.recipient_user_id for n in self.alerts()}
        self.assertEqual(recipients, {self.school_admin.id, self.admin2.id, self.guardian_user.id, self.second_user.id})
        guardian_notice = self.alerts().get(recipient_user=self.guardian_user)
        self.assertEqual(guardian_notice.template_key, 'attendance.bus_unaccounted')
        self.assertEqual(guardian_notice.priority, 'CRITICAL')
        self.assertEqual(guardian_notice.payload['student_name'], 'Umar bin Khattab')
        # Ali alighted: his own guardian (Aisyah, second_user) is told only through Umar (linked to both).
        self.assertEqual(self.alerts().filter(recipient_user=self.second_user, payload__student_id=self.second_student.id).count(), 0)
        admin_notice = self.alerts().get(recipient_user=self.school_admin)
        self.assertEqual(admin_notice.template_key, 'attendance.bus_unaccounted_admin')
        self.assertEqual(admin_notice.payload['count'], 1)
        self.assertIn('Umar bin Khattab', admin_notice.payload['student_names'])

    def test_a_clean_run_alerts_nobody_and_is_marked_checked(self):
        run = self.new_run()
        self.tap(run)
        self.tap(run)
        self.end(run)
        self.assertEqual(self.alerts().count(), 0)
        run.refresh_from_db()
        self.assertIsNotNone(run.unaccounted_checked_at)
        self.assertFalse(AuditEvent.objects.filter(action='attendance.bus_run.unaccounted').exists())

    def test_the_alert_is_audited_high_priority(self):
        run = self.new_run()
        self.tap(run)
        self.end(run)
        event = AuditEvent.objects.get(action='attendance.bus_run.unaccounted')
        self.assertEqual(event.diff['priority'], 'HIGH')
        self.assertEqual(event.diff['student_ids'], [self.student.id])

    def test_an_active_run_is_not_checked(self):
        from apps.attendance.transport import check_unaccounted
        run = self.new_run()
        self.tap(run)
        self.assertIsNone(check_unaccounted(run))
        self.assertEqual(self.alerts().count(), 0)

    def test_ending_the_run_again_or_running_the_cron_never_alerts_twice(self):
        run = self.new_run()
        self.tap(run)
        self.end(run)
        before = self.alerts().count()
        self.assertGreater(before, 0)
        self.end(run)
        call_command('check_unaccounted_students', '--force')
        self.assertEqual(self.alerts().count(), before)
        self.assertEqual(AuditEvent.objects.filter(action='attendance.bus_run.unaccounted').count(), 1)

    def test_a_failed_alert_is_retried_by_the_cron_without_repeating_the_ones_that_went_out(self):
        run = self.new_run()
        self.tap(run)
        real = __import__('apps.notifications.services', fromlist=['dispatch_intent']).dispatch_intent
        calls = {'n': 0}

        def flaky(**kwargs):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('boom')
            return real(**kwargs)

        with mock.patch('apps.notifications.services.dispatch_intent', side_effect=flaky):
            self.end(run)
        run.refresh_from_db()
        self.assertIsNone(run.unaccounted_checked_at)  # left for the cron
        partial = self.alerts().count()
        call_command('check_unaccounted_students', '--force')
        run.refresh_from_db()
        self.assertIsNotNone(run.unaccounted_checked_at)
        self.assertGreater(self.alerts().count(), partial)
        self.assertEqual(self.alerts().count(), 4)  # 2 admins + 2 guardians of Umar, each exactly once

    def test_the_cron_closes_a_forgotten_run_and_checks_it(self):
        run = self.new_run()
        self.tap(run)
        BusRun.all_tenants.filter(id=run.id).update(started_at=timezone.now() - datetime.timedelta(hours=5))
        BusBoardingEvent.all_tenants.update(occurred_at=timezone.now() - datetime.timedelta(hours=5))
        call_command('check_unaccounted_students', '--force')
        run.refresh_from_db()
        self.assertEqual(run.status, BusRunStatus.COMPLETED)
        self.assertIsNone(run.ended_by_id)
        self.assertGreater(self.alerts().count(), 0)

    def test_the_cron_leaves_a_recent_active_run_alone(self):
        run = self.new_run()
        self.tap(run)
        call_command('check_unaccounted_students', '--force')
        run.refresh_from_db()
        self.assertEqual(run.status, BusRunStatus.ACTIVE)
        self.assertEqual(self.alerts().count(), 0)

    def test_the_cron_records_a_job_run(self):
        call_command('check_unaccounted_students', '--force')
        job = JobRun.objects.get(job_name='check_unaccounted_students')
        self.assertEqual(job.status, JobRun.STATUS_SUCCESS)

    def test_a_late_alight_after_the_alert_is_still_recorded_and_does_not_alert_again(self):
        run = self.new_run()
        self.tap(run)
        self.end(run)
        before = self.alerts().count()
        self.tap(run)  # the offline handheld replays the alight
        self.assertEqual(BusBoardingEvent.all_tenants.filter(kind='ALIGHT').count(), 1)
        call_command('check_unaccounted_students', '--force')
        self.assertEqual(self.alerts().count(), before)


class LiveViewTests(_Base):
    def live(self, user, student=None, route=None):
        student = student or self.student
        return self.get(user, f'/api/v1/bus/routes/{(route or self.route).id}/live/?student_id={student.id}')

    def test_no_run_means_nothing_to_show(self):
        res = self.live(self.guardian_user)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {'active': False})

    def test_a_guardian_sees_the_bus_and_only_their_childs_stop_during_the_run(self):
        other = self._student(self.school, "Ali bin Abi Thalib", "25002")
        far_stop = self.make_stop(self.route, name='Rumah Ali', lat=Decimal('-6.3'), sequence=1)
        self.assign(far_stop, other)
        run = self.new_run()
        self.position(run, NEAR, speed=6.0)
        body = self.live(self.guardian_user).json()
        self.assertTrue(body['active'])
        self.assertEqual(body['stop']['name'], 'Jl. Melati')
        self.assertEqual(body['student_status'], 'WAITING')
        self.assertEqual(body['bus']['latitude'], float(NEAR))
        self.assertNotIn('Rumah Ali', str(body))
        self.assertNotIn('Ali bin Abi Thalib', str(body))
        self.assertNotIn('students', body)

    def test_the_students_status_follows_their_taps(self):
        run = self.new_run()
        self.tap(run)
        self.assertEqual(self.live(self.guardian_user).json()['student_status'], 'ON_BUS')
        self.tap(run)
        self.assertEqual(self.live(self.guardian_user).json()['student_status'], 'ALIGHTED')

    def test_the_view_closes_when_the_run_ends(self):
        run = self.new_run()
        self.assertTrue(self.live(self.guardian_user).json()['active'])
        self.post(self.teacher, f'/api/v1/bus/runs/{run.id}/end/', {})
        self.assertEqual(self.live(self.guardian_user).json(), {'active': False})

    def test_a_bus_that_has_not_reported_a_position_has_no_bus_entry(self):
        self.new_run()
        self.assertIsNone(self.live(self.guardian_user).json()['bus'])

    def test_another_childs_guardian_and_unlinked_users_get_404(self):
        self.new_run()
        other = self._student(self.school, "Ali bin Abi Thalib", "25002")
        self.assign(self.stop, other)
        self.assertEqual(self.live(self.stranger_user).status_code, 404)
        self.assertEqual(self.live(self.stranger_user, student=other).status_code, 404)
        # A guardian of Umar asking about Ali (not their child) learns nothing either.
        self.assertEqual(self.live(self.guardian_user, student=other).status_code, 404)

    def test_a_child_not_assigned_to_the_route_gets_404(self):
        unassigned = self._student(self.school, "Zaid bin Haritsah", "25003")
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id, guardian=self.guardian, student=unassigned,
            relation=GuardianLink.RELATION_FATHER, can_pickup=True,
        )
        self.new_run()
        self.assertEqual(self.live(self.guardian_user, student=unassigned).status_code, 404)

    def test_a_child_on_another_route_cannot_be_seen_through_this_one(self):
        second_route = self.make_route(name='Rute Selatan')
        self.new_run()
        self.assertEqual(self.live(self.guardian_user, route=second_route).status_code, 404)

    def test_a_missing_or_bad_student_id_is_404(self):
        self.assertEqual(self.get(self.guardian_user, f'/api/v1/bus/routes/{self.route.id}/live/').status_code, 404)
        self.assertEqual(self.get(self.guardian_user, f'/api/v1/bus/routes/{self.route.id}/live/?student_id=abc').status_code, 404)


class ModelConstraintTests(_Base):
    def test_a_student_has_one_active_stop_per_route(self):
        other_stop = self.make_stop(self.route, name='Lain', sequence=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.assign(other_stop, self.student)

    def test_the_event_uuid_is_unique(self):
        run = self.new_run()
        event_uuid = uuid.uuid4()
        kwargs = dict(
            foundation_id=self.foundation.id, event_uuid=event_uuid, school=self.school, run=run,
            student=self.student, kind='BOARD', occurred_at=timezone.now(),
        )
        BusBoardingEvent.all_tenants.create(**kwargs)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BusBoardingEvent.all_tenants.create(**kwargs)
