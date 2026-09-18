"""Tests for UU PDP data subject rights & retention tooling (spec/14 §3, CMP-011..013).

CMP-011 (DSAR access export) is covered by apps/compliance/tests/test_statutory_export.py-style
ExportJob renderer tests in Task 2, reusing the CMP-016 PII-export pipeline directly — there is
no separate DataSubjectRequest bookkeeping for access requests. This file covers erasure
(CMP-012) and, via apps/attendance/tests/test_purge_gate_photos.py, retention (CMP-013).
"""
import datetime
from decimal import Decimal

from django.test import TestCase

from apps.compliance.models import (
    DataSubjectRequest,
    DataSubjectRequestStatus,
    DataSubjectRequestSubjectType,
)
from apps.compliance.services import PersonNotErasableError, erase_person
from apps.core.models import AuditEvent
from apps.finance.models import Invoice, InvoiceStatus, Payment, PaymentMethod, PaymentStatus
from apps.identity.models import Foundation, Person, School, Student
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
