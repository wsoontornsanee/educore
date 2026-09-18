"""Tests for UU PDP data subject rights & retention tooling (spec/14 §3, CMP-011..013).

CMP-011 (DSAR access export) is covered by apps/compliance/tests/test_statutory_export.py-style
ExportJob renderer tests in Task 2, reusing the CMP-016 PII-export pipeline directly — there is
no separate DataSubjectRequest bookkeeping for access requests. This file covers erasure
(CMP-012) and, via apps/attendance/tests/test_purge_gate_photos.py, retention (CMP-013).
"""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.compliance.models import (
    DataSubjectRequest,
    DataSubjectRequestStatus,
    DataSubjectRequestSubjectType,
)
from apps.compliance.services import PersonNotErasableError, erase_person
from apps.core.models import AuditEvent
from apps.finance.models import Invoice, InvoiceStatus, Payment, PaymentMethod, PaymentStatus
from apps.identity.models import Foundation, Person, School, Staff, Student, User
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class DataSubjectRequestModelTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji", brand_name="Uji", npwp="01.000.000.0-001.000",
        )
        set_current_foundation_id(self.foundation.id)

    def tearDown(self):
        clear_current_foundation_id()

    def test_create_refused_erasure_request(self):
        req = DataSubjectRequest.objects.create(
            foundation_id=self.foundation.id,
            subject_type=DataSubjectRequestSubjectType.STUDENT,
            subject_id=1,
            status=DataSubjectRequestStatus.REFUSED,
            requested_by="42",
            requested_by_name="Ketua Yayasan",
            refusal_reason="Status saat ini bukan status keluar/lulus.",
        )
        self.assertEqual(req.status, DataSubjectRequestStatus.REFUSED)
        self.assertTrue(req.refusal_reason)


class ErasePersonTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji", brand_name="Uji", npwp="01.000.000.0-004.000",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Uji Hapus", npsn="40100097", level=School.LEVEL_SMA,
        )
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Andi Lulus",
            nik="3171010101017777", address="Jl. Contoh No. 1",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.person,
            nis="2026097", status=Student.STATUS_GRADUATED,
        )
        self.invoice = Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            number="INV/UJI/2026/000097", period="2026-06", due_date=datetime.date(2026, 6, 10),
            subtotal=Decimal('500000.00'), total=Decimal('500000.00'), paid=Decimal('500000.00'),
            currency='IDR', status=InvoiceStatus.PAID,
        )
        self.payment = Payment.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            invoice=self.invoice, amount=Decimal('500000.00'), currency='IDR',
            method=PaymentMethod.VA, channel='BCA_VA', reference="PAY/UJI/2026/000097",
            status=PaymentStatus.SETTLED,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_erasure_refused_when_active(self):
        active_person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Masih Aktif", nik="3171010101016666",
        )
        active_student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=active_person,
            nis="2026096", status=Student.STATUS_ACTIVE,
        )
        with self.assertRaises(PersonNotErasableError):
            erase_person(
                subject_type='STUDENT', subject_id=active_student.id, foundation_id=self.foundation.id,
                requested_by='7', requested_by_name='Ketua Yayasan',
            )
        active_person.refresh_from_db()
        self.assertEqual(active_person.full_name, 'Masih Aktif')

    def test_erasure_anonymizes_person_preserves_ledger(self):
        dsar = erase_person(
            subject_type='STUDENT', subject_id=self.student.id, foundation_id=self.foundation.id,
            requested_by='7', requested_by_name='Ketua Yayasan',
        )
        self.assertEqual(dsar.status, DataSubjectRequestStatus.COMPLETED)

        self.person.refresh_from_db()
        self.assertEqual(self.person.full_name, f"[ERASED-{self.person.id}]")
        self.assertIsNone(self.person.nik)
        self.assertEqual(self.person.address, '')

        # Financial rows untouched — 10-year retention (CMP-012) falls out
        # of the PII-vault design automatically.
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.total, Decimal('500000.00'))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.amount, Decimal('500000.00'))

    def test_erasure_writes_audit_event(self):
        erase_person(
            subject_type='STUDENT', subject_id=self.student.id, foundation_id=self.foundation.id,
            requested_by='7', requested_by_name='Ketua Yayasan',
        )
        self.assertTrue(
            AuditEvent.objects.filter(action='compliance.person.erase', entity_id=str(self.person.id)).exists()
        )

    def test_erasure_unknown_subject_raises(self):
        with self.assertRaises(PersonNotErasableError):
            erase_person(
                subject_type='STUDENT', subject_id=999999, foundation_id=self.foundation.id,
                requested_by='7', requested_by_name='Ketua Yayasan',
            )

    def test_erasure_blanks_student_photo_and_gate_photos(self):
        """CMP-012: an accepted erasure must take the subject's facial imagery
        with it immediately, not wait out the CMP-013 90-day gate-photo window."""
        import uuid

        from apps.attendance.models import GateDirection, GateEvent, GateEventStatus, GateMethod
        from apps.hardware.models import Device, DeviceClass, DeviceDirection

        self.student.photo_key = 'students/andi.jpg'
        self.student.save(update_fields=['photo_key'])

        device = Device.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, device_code="GATE-HAPUS-001",
            name="Gerbang Hapus", device_class=DeviceClass.GATE_READER, direction=DeviceDirection.IN,
            ip_address="192.168.1.70", mac_address="00:11:22:33:44:70",
        )
        gate_event = GateEvent.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, device=device, student=self.student,
            event_uuid=uuid.uuid4(), direction=GateDirection.IN, occurred_at=timezone.now(),
            method=GateMethod.FACE, status=GateEventStatus.ACCEPTED, photo_key='gate/andi_face.jpg',
        )

        erase_person(
            subject_type='STUDENT', subject_id=self.student.id, foundation_id=self.foundation.id,
            requested_by='7', requested_by_name='Ketua Yayasan',
        )

        self.student.refresh_from_db()
        self.assertEqual(self.student.photo_key, '')
        gate_event.refresh_from_db()
        self.assertEqual(gate_event.photo_key, '')


class EraseStaffTests(TestCase):
    """The STAFF erasure branch (CMP-012), including the linked login account."""

    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji Staf", brand_name="Uji Staf", npwp="01.000.000.0-009.000",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Uji Staf Hapus", npsn="40100091", level=School.LEVEL_SMA,
        )
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Dewi Berhenti",
            nik="3171010101012222", address="Jl. Guru No. 2",
        )
        self.user = User.all_tenants.create_user(
            phone_e164="+6281666666661", foundation_id=self.foundation.id,
            full_name="Dewi Berhenti", email="dewi@example.com",
        )
        self.staff = Staff.all_tenants.create(
            foundation_id=self.foundation.id, person=self.person, user=self.user, school=self.school,
            nip="198001012005012001", join_date=datetime.date(2005, 1, 1),
            status=Staff.STATUS_OFFBOARDED,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_offboarded_staff_erasure_anonymizes_person(self):
        dsar = erase_person(
            subject_type='STAFF', subject_id=self.staff.id, foundation_id=self.foundation.id,
            requested_by='7', requested_by_name='Ketua Yayasan',
        )
        self.assertEqual(dsar.status, DataSubjectRequestStatus.COMPLETED)
        self.assertEqual(dsar.subject_type, DataSubjectRequestSubjectType.STAFF)

        self.person.refresh_from_db()
        self.assertEqual(self.person.full_name, f"[ERASED-{self.person.id}]")
        self.assertIsNone(self.person.nik)
        self.assertEqual(self.person.address, '')

    def test_staff_erasure_anonymizes_linked_user_account(self):
        erase_person(
            subject_type='STAFF', subject_id=self.staff.id, foundation_id=self.foundation.id,
            requested_by='7', requested_by_name='Ketua Yayasan',
        )

        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, f"[ERASED-{self.user.id}]")
        self.assertNotEqual(self.user.phone_e164, "+6281666666661")
        self.assertIn(str(self.user.id), self.user.phone_e164)
        self.assertIsNone(self.user.email)
        # Account lifecycle (activation/permissions) is a separate concern.
        self.assertTrue(self.user.is_active)

    def test_staff_erasure_blanks_gate_photos(self):
        import uuid

        from apps.attendance.models import GateDirection, GateEvent, GateEventStatus, GateMethod
        from apps.hardware.models import Device, DeviceClass, DeviceDirection

        device = Device.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, device_code="GATE-HAPUS-002",
            name="Gerbang Hapus Staf", device_class=DeviceClass.GATE_READER, direction=DeviceDirection.IN,
            ip_address="192.168.1.71", mac_address="00:11:22:33:44:71",
        )
        gate_event = GateEvent.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, device=device, staff=self.staff,
            event_uuid=uuid.uuid4(), direction=GateDirection.IN, occurred_at=timezone.now(),
            method=GateMethod.FACE, status=GateEventStatus.ACCEPTED, photo_key='gate/dewi_face.jpg',
        )

        erase_person(
            subject_type='STAFF', subject_id=self.staff.id, foundation_id=self.foundation.id,
            requested_by='7', requested_by_name='Ketua Yayasan',
        )

        gate_event.refresh_from_db()
        self.assertEqual(gate_event.photo_key, '')

    def test_active_staff_erasure_refused(self):
        active_person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Guru Aktif", nik="3171010101011111",
        )
        active_user = User.all_tenants.create_user(
            phone_e164="+6281666666662", foundation_id=self.foundation.id, full_name="Guru Aktif",
        )
        active_staff = Staff.all_tenants.create(
            foundation_id=self.foundation.id, person=active_person, user=active_user, school=self.school,
            join_date=datetime.date(2015, 1, 1), status=Staff.STATUS_ACTIVE,
        )

        with self.assertRaises(PersonNotErasableError):
            erase_person(
                subject_type='STAFF', subject_id=active_staff.id, foundation_id=self.foundation.id,
                requested_by='7', requested_by_name='Ketua Yayasan',
            )

        active_person.refresh_from_db()
        self.assertEqual(active_person.full_name, 'Guru Aktif')
        active_user.refresh_from_db()
        self.assertEqual(active_user.full_name, 'Guru Aktif')
        self.assertEqual(active_user.phone_e164, '+6281666666662')

        refused = DataSubjectRequest.objects.filter(
            subject_type=DataSubjectRequestSubjectType.STAFF, subject_id=active_staff.id,
        ).latest('id')
        self.assertEqual(refused.status, DataSubjectRequestStatus.REFUSED)
        self.assertTrue(refused.refusal_reason)

    def test_unknown_staff_erasure_refused(self):
        with self.assertRaises(PersonNotErasableError):
            erase_person(
                subject_type='STAFF', subject_id=999999, foundation_id=self.foundation.id,
                requested_by='7', requested_by_name='Ketua Yayasan',
            )


from rest_framework import status as http_status
from rest_framework.test import APITestCase

from apps.identity.models import User
from apps.identity.rbac import ROLE_FOUNDATION_ADMIN, ROLE_TEACHER, SCOPE_FOUNDATION, assign_role
from educore.middleware.tenancy import tenant_context


class ErasureRequestEndpointTests(APITestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji", brand_name="Uji", npwp="01.000.000.0-005.000",
        )
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Uji Endpoint", npsn="40100096", level=School.LEVEL_SMA,
        )
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Citra Dewi", nik="3171010101015555",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.person,
            nis="2026095", status=Student.STATUS_GRADUATED,
        )
        self.admin = User.all_tenants.create_user(
            phone_e164="+6281999999991", foundation_id=self.foundation.id, full_name="Ketua Yayasan",
        )
        assign_role(
            user=self.admin, role=ROLE_FOUNDATION_ADMIN, scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id, foundation_id=self.foundation.id,
        )
        self.teacher = User.all_tenants.create_user(
            phone_e164="+6281999999992", foundation_id=self.foundation.id, full_name="Guru",
        )
        assign_role(
            user=self.teacher, role=ROLE_TEACHER, scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id, foundation_id=self.foundation.id,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_admin_erases_graduated_student(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.post('/api/v1/foundation/compliance/erasure-requests', {
                'subject_type': 'STUDENT', 'subject_id': self.student.id,
            }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertEqual(response.json()['status'], 'COMPLETED')

    def test_admin_erases_offboarded_staff(self):
        staff_person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Staf Berhenti", nik="3171010101013333",
        )
        staff_user = User.all_tenants.create_user(
            phone_e164="+6281999999994", foundation_id=self.foundation.id, full_name="Staf Berhenti",
        )
        staff = Staff.all_tenants.create(
            foundation_id=self.foundation.id, person=staff_person, user=staff_user, school=self.school,
            join_date=datetime.date(2012, 1, 1), status=Staff.STATUS_OFFBOARDED,
        )

        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.post('/api/v1/foundation/compliance/erasure-requests', {
                'subject_type': 'STAFF', 'subject_id': staff.id,
            }, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertEqual(response.json()['status'], 'COMPLETED')
        self.assertEqual(response.json()['subject_type'], 'STAFF')

        staff_person.refresh_from_db()
        self.assertEqual(staff_person.full_name, f"[ERASED-{staff_person.id}]")
        staff_user.refresh_from_db()
        self.assertEqual(staff_user.full_name, f"[ERASED-{staff_user.id}]")

    def test_active_staff_erasure_returns_200_refused(self):
        staff_person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Staf Aktif", nik="3171010101012222",
        )
        staff_user = User.all_tenants.create_user(
            phone_e164="+6281999999995", foundation_id=self.foundation.id, full_name="Staf Aktif",
        )
        staff = Staff.all_tenants.create(
            foundation_id=self.foundation.id, person=staff_person, user=staff_user, school=self.school,
            join_date=datetime.date(2012, 1, 1), status=Staff.STATUS_ACTIVE,
        )

        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.post('/api/v1/foundation/compliance/erasure-requests', {
                'subject_type': 'STAFF', 'subject_id': staff.id,
            }, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.json()['status'], 'REFUSED')
        self.assertTrue(response.json()['refusal_reason'])

    def test_teacher_forbidden(self):
        self.client.force_authenticate(user=self.teacher)
        with tenant_context(self.foundation.id):
            response = self.client.post('/api/v1/foundation/compliance/erasure-requests', {
                'subject_type': 'STUDENT', 'subject_id': self.student.id,
            }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_403_FORBIDDEN)

    def test_refused_when_active_returns_200_with_reason(self):
        active_person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Masih Aktif", nik="3171010101014444",
        )
        active_student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=active_person,
            nis="2026094", status=Student.STATUS_ACTIVE,
        )
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.post('/api/v1/foundation/compliance/erasure-requests', {
                'subject_type': 'STUDENT', 'subject_id': active_student.id,
            }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.json()['status'], 'REFUSED')
        self.assertTrue(response.json()['refusal_reason'])

    def test_cross_tenant_list_is_scoped(self):
        other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain", brand_name="Lain", npwp="02.000.000.0-005.000",
        )
        with tenant_context(other_foundation.id):
            other_admin = User.all_tenants.create_user(
                phone_e164="+6281999999993", foundation_id=other_foundation.id, full_name="Admin Lain",
            )
            assign_role(
                user=other_admin, role=ROLE_FOUNDATION_ADMIN, scope_type=SCOPE_FOUNDATION,
                scope_id=other_foundation.id, foundation_id=other_foundation.id,
            )

        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            self.client.post('/api/v1/foundation/compliance/erasure-requests', {
                'subject_type': 'STUDENT', 'subject_id': self.student.id,
            }, format='json')

        self.client.force_authenticate(user=other_admin)
        with tenant_context(other_foundation.id):
            response = self.client.get('/api/v1/foundation/compliance/erasure-requests')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.json(), [])
