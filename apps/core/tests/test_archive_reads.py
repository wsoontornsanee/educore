"""NFR-009 readers: history past the retention window is still reachable, erasable and never rebuilt short."""
import datetime
import uuid
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.attendance.models import GateDirection, GateEvent, GateEventArchive, GateEventStatus, GateMethod
from apps.compliance.services import _erase_subject_photos
from apps.core.archiving import archive_batch, policy_for
from apps.core.models import AuditEvent, AuditEventArchive, ExportJob
from apps.foundation.services import filter_foundation_audit_events, render_foundation_audit_export
from apps.foundation.web_views import read_audit_filters
from apps.hardware.models import Device, DeviceClass, DeviceDirection
from apps.identity.models import Foundation, Person, School, Student
from apps.reporting.models import RptWalletActivity
from apps.reporting.services import refresh_wallet_activity
from apps.wallet.models import WalletTransaction, WalletTransactionType
from apps.wallet.services import get_or_create_wallet, record_wallet_transaction
from apps.wallet.tests.test_wallet_core import build_wallet_fixture
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id

LONG_AGO = timezone.now() - datetime.timedelta(days=1000)


class AuditExplorerArchiveTests(TestCase):
    def setUp(self):
        for foundation_id, entity in ((1, 'mine-old'), (1, 'mine-new'), (2, 'other-old')):
            AuditEvent.objects.create(action='finance.invoice.issue', entity_type='Invoice', entity_id=entity, foundation_id=foundation_id)
        AuditEvent.objects.filter(entity_id__endswith='-old').update(timestamp=LONG_AGO)
        archive_batch(policy_for('core.AuditEvent'), 100)

    def entities(self, **kwargs):
        return sorted(filter_foundation_audit_events(1, **kwargs).values_list('entity_id', flat=True))

    def test_hot_view_excludes_archived_events(self):
        self.assertEqual(self.entities(), ['mine-new'])

    def test_archived_view_returns_only_the_callers_archived_events(self):
        self.assertEqual(self.entities(archived=True), ['mine-old'])

    def test_filters_apply_to_the_archive(self):
        self.assertEqual(self.entities(archived=True, from_date='2020-01-01', to_date='2020-12-31'), [])
        self.assertEqual(self.entities(archived=True, module='finance', entity_id='mine-old'), ['mine-old'])

    def test_export_reads_the_archive_when_asked(self):
        def exported(**filters):
            job = ExportJob(id=1, foundation_id=1, filters=filters)
            return render_foundation_audit_export(job)[0].decode('utf-8-sig')
        self.assertIn('mine-old', exported(archived=True))
        self.assertNotIn('mine-old', exported())
        self.assertNotIn('other-old', exported(archived=True))

    def test_web_viewer_reads_the_archived_flag(self):
        filters, _ = read_audit_filters({'archived': '1'})
        self.assertEqual(filters['archived'], '1')
        self.assertEqual(read_audit_filters({})[0]['archived'], '')

    def test_archive_model_backs_the_query(self):
        self.assertIs(filter_foundation_audit_events(1, archived=True).model, AuditEventArchive)


class GatePhotoArchiveTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(legal_name="Yayasan Arsip Foto", brand_name="Arsip Foto", npwp="01.000.000.0-007.000")
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(foundation_id=self.foundation.id, name="SMA Arsip", npsn="40100096", level=School.LEVEL_SMA)
        self.device = Device.objects.create(
            foundation_id=self.foundation.id, school=self.school, device_code="GATE-ARS-001", name="Gerbang Arsip",
            device_class=DeviceClass.GATE_READER, direction=DeviceDirection.IN,
            ip_address="192.168.1.61", mac_address="00:11:22:33:44:67",
        )
        self.person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name="Siswa Arsip")
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.person, nis="2026095", status=Student.STATUS_ACTIVE,
        )
        self.event = GateEvent.objects.create(
            foundation_id=self.foundation.id, school=self.school, device=self.device, student=self.student,
            event_uuid=uuid.uuid4(), direction=GateDirection.IN, occurred_at=timezone.now() - datetime.timedelta(days=120),
            method=GateMethod.FACE, status=GateEventStatus.ACCEPTED, photo_key='gate/arsip.jpg',
        )
        archive_batch(policy_for('attendance.GateEvent'), 100)
        self.assertFalse(GateEvent.all_tenants.filter(pk=self.event.pk).exists())

    def tearDown(self):
        clear_current_foundation_id()

    def photo(self):
        return GateEventArchive.objects.get(pk=self.event.pk).photo_key

    def test_retention_sweep_blanks_archived_photos(self):
        call_command('purge_gate_photos', '--force')
        self.assertEqual(self.photo(), '')

    def test_dry_run_leaves_archived_photos_alone(self):
        call_command('purge_gate_photos', '--dry-run', '--force')
        self.assertEqual(self.photo(), 'gate/arsip.jpg')

    def test_erasure_blanks_archived_photos_of_that_subject_only(self):
        other = GateEventArchive.objects.get(pk=self.event.pk)
        other.pk, other.student_id, other.foundation_id = self.event.pk + 1000, self.student.id + 1000, self.foundation.id + 1000
        other.save()
        _erase_subject_photos('STUDENT', self.student)
        self.assertEqual(self.photo(), '')
        self.assertEqual(GateEventArchive.objects.get(pk=other.pk).photo_key, 'gate/arsip.jpg')


class WalletRollupArchiveTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.wallet = get_or_create_wallet(self.fx['student'])
        self.foundation_id = self.fx['foundation'].id

    def rollup(self, day):
        return RptWalletActivity.all_tenants.get(foundation_id=self.foundation_id, date=day)

    def test_full_rebuild_keeps_days_the_archive_already_holds(self):
        now = timezone.now()
        old = now - datetime.timedelta(days=450)
        boundary = now - datetime.timedelta(days=401)
        for when, key in ((old, 'old-a'), (boundary, 'b-1'), (boundary + datetime.timedelta(minutes=5), 'b-2')):
            record_wallet_transaction(self.wallet, WalletTransactionType.TOPUP, Decimal('1000'), key, occurred_at=when)
        refresh_wallet_activity(scope='full')
        self.assertEqual(self.rollup(boundary.date()).topups, Decimal('2000.00'))

        # Everything past 400 days moves to the archive; a straggler then lands on the boundary day.
        archive_batch(policy_for('wallet.WalletTransaction'), 100)
        self.assertEqual(WalletTransaction.all_tenants.filter(idempotency_key__startswith='b-').count(), 0)
        record_wallet_transaction(self.wallet, WalletTransactionType.TOPUP, Decimal('500'), 'b-late', occurred_at=boundary + datetime.timedelta(hours=1))

        refresh_wallet_activity(scope='full')

        self.assertEqual(self.rollup(boundary.date()).topups, Decimal('2000.00'))
        self.assertEqual(self.rollup(old.date()).topups, Decimal('1000.00'))

    def test_without_archived_rows_history_is_rebuilt_in_full(self):
        old = timezone.now() - datetime.timedelta(days=450)
        record_wallet_transaction(self.wallet, WalletTransactionType.TOPUP, Decimal('700'), 'old-b', occurred_at=old)
        refresh_wallet_activity(scope='full')
        self.assertEqual(self.rollup(old.date()).topups, Decimal('700.00'))
