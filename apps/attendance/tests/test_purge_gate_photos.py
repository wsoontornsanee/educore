"""Tests for the gate-photo retention sweeper (spec/14 §3/§7, CMP-013)."""
import datetime
import uuid

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.attendance.models import GateDirection, GateEvent, GateEventStatus, GateMethod
from apps.core.models import JobRun
from apps.hardware.models import Device, DeviceClass, DeviceDirection
from apps.identity.models import Foundation, Person, School, Student
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class PurgeGatePhotosTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji Retensi", brand_name="Uji Retensi", npwp="01.000.000.0-006.000",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Uji Retensi", npsn="40100095", level=School.LEVEL_SMA,
        )
        self.device = Device.objects.create(
            foundation_id=self.foundation.id, school=self.school, device_code="GATE-RET-001",
            name="Gerbang Retensi", device_class=DeviceClass.GATE_READER, direction=DeviceDirection.IN,
            ip_address="192.168.1.60", mac_address="00:11:22:33:44:66",
        )
        self.person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name="Siswa Retensi")
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.person,
            nis="2026094", status=Student.STATUS_ACTIVE,
        )

        now = timezone.now()
        self.old_event = GateEvent.objects.create(
            foundation_id=self.foundation.id, school=self.school, device=self.device, student=self.student,
            event_uuid=uuid.uuid4(), direction=GateDirection.IN, occurred_at=now - datetime.timedelta(days=91),
            method=GateMethod.FACE, status=GateEventStatus.ACCEPTED, photo_key='gate/old_photo.jpg',
        )
        self.recent_event = GateEvent.objects.create(
            foundation_id=self.foundation.id, school=self.school, device=self.device, student=self.student,
            event_uuid=uuid.uuid4(), direction=GateDirection.IN, occurred_at=now - datetime.timedelta(days=5),
            method=GateMethod.FACE, status=GateEventStatus.ACCEPTED, photo_key='gate/recent_photo.jpg',
        )
        self.no_photo_event = GateEvent.objects.create(
            foundation_id=self.foundation.id, school=self.school, device=self.device, student=self.student,
            event_uuid=uuid.uuid4(), direction=GateDirection.IN, occurred_at=now - datetime.timedelta(days=200),
            method=GateMethod.RFID, status=GateEventStatus.ACCEPTED, photo_key='',
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_dry_run_reports_without_deleting(self):
        call_command('purge_gate_photos', '--dry-run', '--force')

        self.old_event.refresh_from_db()
        self.assertEqual(self.old_event.photo_key, 'gate/old_photo.jpg')

    def test_real_run_purges_only_photos_past_retention(self):
        call_command('purge_gate_photos', '--force')

        self.old_event.refresh_from_db()
        self.assertEqual(self.old_event.photo_key, '')

        self.recent_event.refresh_from_db()
        self.assertEqual(self.recent_event.photo_key, 'gate/recent_photo.jpg')

        job_run = JobRun.objects.filter(job_name='purge_gate_photos').latest('started_at')
        self.assertEqual(job_run.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(job_run.items_processed, 1)

    def test_custom_retention_days(self):
        call_command('purge_gate_photos', '--retention-days=3', '--force')

        self.recent_event.refresh_from_db()
        self.assertEqual(self.recent_event.photo_key, '')

    def test_purges_without_ambient_tenant_context(self):
        """Regression: real cron invokes this command bare, with no thread-local
        foundation set. GateEvent.objects (TenantManager) fails closed to
        .none() in that state, so the sweep used to report SUCCESS while
        purging nothing at all. The command must enter tenant_context itself."""
        clear_current_foundation_id()

        call_command('purge_gate_photos', '--force')

        self.old_event.refresh_from_db()
        self.assertEqual(self.old_event.photo_key, '')

        self.recent_event.refresh_from_db()
        self.assertEqual(self.recent_event.photo_key, 'gate/recent_photo.jpg')

        job_run = JobRun.objects.filter(job_name='purge_gate_photos').latest('started_at')
        self.assertEqual(job_run.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(job_run.items_processed, 1)

    def test_purges_across_multiple_foundations(self):
        clear_current_foundation_id()
        other_foundation = Foundation.objects.create(
            legal_name="Yayasan Retensi Lain", brand_name="Retensi Lain", npwp="02.000.000.0-006.000",
        )
        other_school = School.all_tenants.create(
            foundation_id=other_foundation.id, name="SMA Retensi Lain", npsn="40100094", level=School.LEVEL_SMA,
        )
        other_device = Device.all_tenants.create(
            foundation_id=other_foundation.id, school=other_school, device_code="GATE-RET-002",
            name="Gerbang Retensi Lain", device_class=DeviceClass.GATE_READER, direction=DeviceDirection.IN,
            ip_address="192.168.1.61", mac_address="00:11:22:33:44:67",
        )
        other_person = Person.all_tenants.create(foundation_id=other_foundation.id, full_name="Siswa Lain")
        other_student = Student.all_tenants.create(
            foundation_id=other_foundation.id, school=other_school, person=other_person,
            nis="2026093", status=Student.STATUS_ACTIVE,
        )
        other_event = GateEvent.all_tenants.create(
            foundation_id=other_foundation.id, school=other_school, device=other_device, student=other_student,
            event_uuid=uuid.uuid4(), direction=GateDirection.IN,
            occurred_at=timezone.now() - datetime.timedelta(days=120),
            method=GateMethod.FACE, status=GateEventStatus.ACCEPTED, photo_key='gate/other_old.jpg',
        )

        call_command('purge_gate_photos', '--force')

        self.old_event.refresh_from_db()
        self.assertEqual(self.old_event.photo_key, '')
        other_event.refresh_from_db()
        self.assertEqual(other_event.photo_key, '')

        job_run = JobRun.objects.filter(job_name='purge_gate_photos').latest('started_at')
        self.assertEqual(job_run.items_processed, 2)

    def test_dry_run_without_tenant_context_counts_candidates(self):
        clear_current_foundation_id()

        call_command('purge_gate_photos', '--dry-run', '--force')

        self.old_event.refresh_from_db()
        self.assertEqual(self.old_event.photo_key, 'gate/old_photo.jpg')

        job_run = JobRun.objects.filter(job_name='purge_gate_photos').latest('started_at')
        self.assertEqual(job_run.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(job_run.items_processed, 0)
