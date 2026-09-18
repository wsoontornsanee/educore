import uuid

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.attendance.models import CredentialType, GateEvent
from apps.attendance.services import issue_credential
from apps.hardware.models import Device, DeviceClass, DeviceEventStaging, DeviceEventStagingStatus
from apps.hardware.services import ingest_device_events
from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id

# APIClient requests go through TenancyMiddleware, which clears the
# thread-local foundation_id once the request finishes (it must not leak
# across requests) - so every assertion made *after* a `_post_batch` call
# reads through `.all_tenants` rather than the fail-closed `.objects`
# TenantManager, same as any other post-request test-assertion in this app.


class IngestDeviceEventsTests(TestCase):
    """spec/12 §3/§7 HW-005: staging intake (POST /device/events) + the
    ingest_device_events cron that applies staged rows via ingest_gate_events.
    """

    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Cendekia", brand_name="Cendekia",
            npwp="01.555.666.7-888.000", status=Foundation.STATUS_ACTIVE,
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SD Cendekia", npsn="40500001", level=School.LEVEL_SD,
        )
        self.device = Device.objects.create(
            foundation_id=self.foundation.id, school=self.school, device_code="GT-01",
            name="Gerbang Utama", device_class=DeviceClass.GATE_READER,
        )
        self.person = Person.objects.create(foundation_id=self.foundation.id, full_name="Budi Santoso")
        self.student = Student.objects.create(
            foundation_id=self.foundation.id, school=self.school, person=self.person,
            nis="2026-SD-001", nisn="0098765432", status=Student.STATUS_ACTIVE,
        )
        self.credential = issue_credential(
            foundation_id=str(self.foundation.id), student=self.student,
            type=CredentialType.RFID, uid="RFID-STAGE-001", card_number="CARD-STAGE-1",
        )
        self.admin_user = User.objects.create(
            foundation_id=self.foundation.id, phone_e164="+6281300000001",
            email="admin@cendekia.sch.id", full_name="Admin",
        )
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.admin_user,
            role=RoleAssignment.ROLE_SCHOOL_ADMIN, scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school.id,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin_user)

    def tearDown(self):
        clear_current_foundation_id()

    def _post_batch(self, events):
        return self.client.post(
            '/api/v1/device/events',
            data={'school_id': self.school.id, 'batch': events},
            format='json',
        )

    def _event_payload(self, event_uuid=None, raw_uid="RFID-STAGE-001"):
        return {
            'event_uuid': str(event_uuid or uuid.uuid4()),
            'device_id': self.device.id,
            'occurred_at': timezone.now().isoformat(),
            'raw_uid': raw_uid,
            'method': 'RFID',
        }

    def test_post_device_events_stages_without_applying(self):
        resp = self._post_batch([self._event_payload()])
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(resp.data['staged'], 1)
        self.assertEqual(DeviceEventStaging.all_tenants.filter(status=DeviceEventStagingStatus.PENDING).count(), 1)
        self.assertEqual(GateEvent.all_tenants.count(), 0)

    def test_post_device_events_dedupes_on_event_uuid(self):
        event_uuid = uuid.uuid4()
        self._post_batch([self._event_payload(event_uuid=event_uuid)])
        resp = self._post_batch([self._event_payload(event_uuid=event_uuid)])

        self.assertEqual(resp.data['staged'], 0)
        self.assertEqual(resp.data['duplicates_skipped'], 1)
        self.assertEqual(DeviceEventStaging.all_tenants.count(), 1)

    def test_post_device_events_skips_unknown_device(self):
        payload = self._event_payload()
        payload['device_id'] = 999999
        resp = self._post_batch([payload])
        self.assertEqual(resp.data['staged'], 0)
        self.assertEqual(DeviceEventStaging.all_tenants.count(), 0)

    def test_cron_applies_staged_event_into_gate_event(self):
        self._post_batch([self._event_payload()])
        result = ingest_device_events()

        self.assertEqual(result, {'checked': 1, 'applied': 1, 'failed': 0})
        self.assertEqual(GateEvent.all_tenants.count(), 1)
        row = DeviceEventStaging.all_tenants.get()
        self.assertEqual(row.status, DeviceEventStagingStatus.APPLIED)
        self.assertIsNotNone(row.applied_at)

    def test_cron_is_idempotent_and_skips_already_applied_rows(self):
        self._post_batch([self._event_payload()])
        ingest_device_events()
        second = ingest_device_events()

        self.assertEqual(second, {'checked': 0, 'applied': 0, 'failed': 0})
        self.assertEqual(GateEvent.all_tenants.count(), 1)

    def test_cron_respects_limit(self):
        self._post_batch([self._event_payload(), self._event_payload()])
        result = ingest_device_events(limit=1)
        self.assertEqual(result['checked'], 1)
        self.assertEqual(GateEvent.all_tenants.count(), 1)

    def test_cron_isolates_a_bad_row_from_the_rest(self):
        good_uuid = uuid.uuid4()
        self._post_batch([self._event_payload(event_uuid=good_uuid)])
        bad_row = DeviceEventStaging.all_tenants.get()
        # Corrupt the payload's occurred_at to force a parsing failure in ingest_gate_events.
        bad_row.payload['occurred_at'] = 'not-a-real-timestamp'
        bad_row.save(update_fields=['payload'])

        good_uuid_2 = uuid.uuid4()
        self._post_batch([self._event_payload(event_uuid=good_uuid_2)])

        result = ingest_device_events()

        self.assertEqual(result, {'checked': 2, 'applied': 1, 'failed': 1})
        bad_row.refresh_from_db()
        self.assertEqual(bad_row.status, DeviceEventStagingStatus.FAILED)
        self.assertTrue(bad_row.error_text)
        self.assertEqual(GateEvent.all_tenants.count(), 1)

    def test_cron_scoped_to_school_id(self):
        other_school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Cendekia", npsn="40500002", level=School.LEVEL_SMP,
        )
        other_device = Device.objects.create(
            foundation_id=self.foundation.id, school=other_school, device_code="GT-02",
            name="Gerbang SMP", device_class=DeviceClass.GATE_READER,
        )
        DeviceEventStaging.objects.create(
            foundation_id=self.foundation.id, school=other_school, device=other_device,
            event_uuid=uuid.uuid4(),
            payload={'event_uuid': str(uuid.uuid4()), 'device_id': other_device.id,
                     'occurred_at': timezone.now().isoformat(), 'raw_uid': ''},
        )
        self._post_batch([self._event_payload()])

        result = ingest_device_events(school_id=self.school.id)
        self.assertEqual(result['checked'], 1)
        self.assertEqual(
            DeviceEventStaging.all_tenants.filter(school=other_school, status=DeviceEventStagingStatus.PENDING).count(),
            1,
        )
