from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.attendance.models import Credential, CredentialStatus, CredentialType
from apps.attendance.services import issue_credential, revoke_credential, verify_credential
from apps.core.models import AuditEvent, DomainEvent
from apps.identity.models import Foundation, Person, RoleAssignment, School, Staff, Student, User
from educore.middleware.tenancy import set_current_foundation_id


class CredentialTests(TestCase):
    """
    Test suite for credential lifecycle, single-active-card invariant (HW-019),
    time-limited QR fallback (HW-020), revocation (HW-018), and API isolation.
    """

    def setUp(self):
        # Foundation 1
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Al-Hikmah",
            brand_name="Al-Hikmah",
            npwp="01.234.567.8-901.000",
            status=Foundation.STATUS_ACTIVE,
        )
        set_current_foundation_id(self.foundation.id)

        # Schools in Foundation 1
        self.school_a = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SD Al-Hikmah 1",
            npsn="10000001",
            level=School.LEVEL_SD,
        )
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP Al-Hikmah 1",
            npsn="10000002",
            level=School.LEVEL_SMP,
        )

        # Foundation 2
        self.foundation_2 = Foundation.objects.create(
            legal_name="Yayasan Darul Ulum",
            brand_name="Darul Ulum",
            status=Foundation.STATUS_ACTIVE,
        )
        self.school_f2 = School.all_tenants.create(
            foundation_id=self.foundation_2.id,
            name="SMA Darul Ulum",
            npsn="20000001",
            level=School.LEVEL_SMA,
        )

        # Persons & Students
        self.person_student_a = Person.objects.create(
            foundation_id=self.foundation.id,
            full_name="Ahmad Dahlan",
            nik="3171012345678901",
        )
        self.student_a = Student.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            person=self.person_student_a,
            nisn="0012345678",
            nis="2026-SD-001",
            status=Student.STATUS_ACTIVE,
        )

        self.person_student_b = Person.objects.create(
            foundation_id=self.foundation.id,
            full_name="Fatimah Az-Zahra",
            nik="3171012345678902",
        )
        self.student_b = Student.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_b,
            person=self.person_student_b,
            nisn="0012345679",
            nis="2026-SMP-001",
            status=Student.STATUS_ACTIVE,
        )

        # Staff in School A
        self.person_staff_a = Person.objects.create(
            foundation_id=self.foundation.id,
            full_name="Budi Santoso, S.Pd.",
            nik="3171012345678903",
        )
        self.user_staff_a = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6283333333333",
            email="budi@alhikmah.sch.id",
            full_name="Budi Santoso, S.Pd.",
        )
        self.staff_a = Staff.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            person=self.person_staff_a,
            user=self.user_staff_a,
            nip="198501012010011001",
            employment_type=Staff.TYPE_PERMANENT,
            join_date=timezone.now().date(),
            status=Staff.STATUS_ACTIVE,
        )

        # Users & Admins
        self.person_admin = Person.objects.create(
            foundation_id=self.foundation.id,
            full_name="Admin Yayasan",
        )
        self.user_admin = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281111111111",
            email="admin@alhikmah.sch.id",
            full_name="Admin Yayasan",
        )
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id,
            user=self.user_admin,
            role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

        self.user_school_a = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6282222222222",
            email="adminsd@alhikmah.sch.id",
            full_name="Admin SD",
        )
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id,
            user=self.user_school_a,
            role=RoleAssignment.ROLE_SCHOOL_ADMIN,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school_a.id,
        )

        self.client = APIClient()

    def test_credential_model_validation(self):
        """Credential requires exactly one holder (student or staff)."""
        # Neither student nor staff
        with self.assertRaises(ValidationError):
            cred = Credential(
                foundation_id=self.foundation.id,
                uid="CARD-001",
            )
            cred.clean()

        # Both student and staff
        with self.assertRaises(ValidationError):
            cred = Credential(
                foundation_id=self.foundation.id,
                student=self.student_a,
                staff=self.staff_a,
                uid="CARD-002",
            )
            cred.clean()

    def test_issue_rfid_card(self):
        """Issuing an RFID card sets active status, records AuditEvent and DomainEvent."""
        card = issue_credential(
            foundation_id=self.foundation.id,
            student=self.student_a,
            type=CredentialType.RFID,
            uid="E0040150A1B2C3D4",
            card_number="SD-001",
            user=self.user_admin,
        )
        self.assertEqual(card.status, CredentialStatus.ACTIVE)
        self.assertEqual(card.uid, "E0040150A1B2C3D4")
        self.assertEqual(card.holder_type, "student")
        self.assertEqual(card.holder_name, "Ahmad Dahlan")

        # Verify AuditEvent
        self.assertTrue(
            AuditEvent.objects.filter(
                foundation_id=self.foundation.id,
                action='attendance.credential.issued',
                entity_id=card.id,
            ).exists()
        )

        # Verify DomainEvent
        self.assertTrue(
            DomainEvent.objects.filter(
                foundation_id=self.foundation.id,
                name='attendance.credential.issued',
            ).exists()
        )

    def test_single_active_card_invariant_hw_019(self):
        """A student can hold at most ONE active physical card; issuing new auto-revokes previous (HW-019)."""
        # 1. Issue first card
        card_1 = issue_credential(
            foundation_id=self.foundation.id,
            student=self.student_a,
            type=CredentialType.RFID,
            uid="CARD_FIRST_001",
            user=self.user_admin,
        )
        self.assertEqual(card_1.status, CredentialStatus.ACTIVE)

        # 2. Issue second card for the same student
        card_2 = issue_credential(
            foundation_id=self.foundation.id,
            student=self.student_a,
            type=CredentialType.RFID,
            uid="CARD_REPLACEMENT_002",
            user=self.user_admin,
        )
        self.assertEqual(card_2.status, CredentialStatus.ACTIVE)

        # 3. Assert card 1 is auto-revoked
        card_1.refresh_from_db()
        self.assertEqual(card_1.status, CredentialStatus.REVOKED)
        self.assertIsNotNone(card_1.revoked_at)
        self.assertIn("Otomatis dicabut", card_1.revoked_reason)

        # 4. Assert holder has exactly ONE active physical card
        active_cards_count = Credential.objects.filter(
            foundation_id=self.foundation.id,
            student=self.student_a,
            type__in=[CredentialType.RFID, CredentialType.NFC],
            status=CredentialStatus.ACTIVE,
        ).count()
        self.assertEqual(active_cards_count, 1)

    def test_duplicate_active_uid_rejected(self):
        """Cannot issue active physical card with an already-used active UID in the foundation."""
        issue_credential(
            foundation_id=self.foundation.id,
            student=self.student_a,
            type=CredentialType.RFID,
            uid="SHARED_UID_999",
            user=self.user_admin,
        )

        with self.assertRaises(ValidationError):
            issue_credential(
                foundation_id=self.foundation.id,
                student=self.student_b,
                type=CredentialType.RFID,
                uid="SHARED_UID_999",
                user=self.user_admin,
            )

    def test_time_limited_qr_credential_hw_020(self):
        """QR fallback credential expires after 15 minutes (HW-020)."""
        qr_cred = issue_credential(
            foundation_id=self.foundation.id,
            student=self.student_a,
            type=CredentialType.QR,
            expires_in_minutes=15,
            user=self.user_admin,
        )
        self.assertEqual(qr_cred.status, CredentialStatus.ACTIVE)
        self.assertIsNotNone(qr_cred.expires_at)

        # 1. Immediate verify -> VALID
        res = verify_credential(self.foundation.id, qr_cred.uid)
        self.assertTrue(res['valid'])
        self.assertEqual(res['status'], 'ACTIVE')

        # 2. Fast forward time past expires_at
        qr_cred.expires_at = timezone.now() - timedelta(seconds=10)
        qr_cred.save(update_fields=['expires_at'])

        # 3. Subsequent verify -> EXPIRED
        res_expired = verify_credential(self.foundation.id, qr_cred.uid)
        self.assertFalse(res_expired['valid'])
        self.assertEqual(res_expired['status'], 'EXPIRED')

    def test_single_use_qr_credential(self):
        """Single-use QR code is rejected on second scan once marked used."""
        qr_cred = issue_credential(
            foundation_id=self.foundation.id,
            student=self.student_a,
            type=CredentialType.QR,
            expires_in_minutes=15,
            user=self.user_admin,
        )

        # Scan 1 with mark_used=True (e.g. gate turnstile entry)
        res_first = verify_credential(self.foundation.id, qr_cred.uid, mark_used=True)
        self.assertTrue(res_first['valid'])

        # Scan 2 -> ALREADY_USED
        res_second = verify_credential(self.foundation.id, qr_cred.uid)
        self.assertFalse(res_second['valid'])
        self.assertEqual(res_second['status'], 'ALREADY_USED')

    def test_revocation_flow_hw_018(self):
        """Lost card revocation marks card REVOKED and fails subsequent scans (HW-018)."""
        card = issue_credential(
            foundation_id=self.foundation.id,
            student=self.student_a,
            type=CredentialType.RFID,
            uid="LOST_CARD_1234",
            user=self.user_admin,
        )

        # Revoke card
        revoke_credential(
            credential=card,
            reason="Kartu hilang saat jam istirahat",
            post_replacement_fee=True,
            user=self.user_admin,
        )

        card.refresh_from_db()
        self.assertEqual(card.status, CredentialStatus.REVOKED)
        self.assertTrue(card.replacement_fee_posted)

        # Verification must fail with REVOKED
        res = verify_credential(self.foundation.id, "LOST_CARD_1234")
        self.assertFalse(res['valid'])
        self.assertEqual(res['status'], 'REVOKED')

    def test_api_issue_credential(self):
        """POST /api/v1/credentials/ successfully issues credential."""
        self.client.force_authenticate(user=self.user_admin)

        payload = {
            'student_id': self.student_a.id,
            'type': 'RFID',
            'uid': 'API_CARD_5555',
            'card_number': '2026-SD-5555',
        }
        resp = self.client.post('/api/v1/credentials/', data=payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['uid'], 'API_CARD_5555')
        self.assertEqual(resp.data['holder_name'], 'Ahmad Dahlan')

    def test_api_revoke_credential(self):
        """POST /api/v1/credentials/{id}/revoke/ revokes card."""
        card = issue_credential(
            foundation_id=self.foundation.id,
            student=self.student_a,
            type=CredentialType.RFID,
            uid="REVOKE_ME_7777",
            user=self.user_admin,
        )

        self.client.force_authenticate(user=self.user_admin)
        resp = self.client.post(
            f'/api/v1/credentials/{card.id}/revoke/',
            data={'reason': 'Rusak patah', 'post_replacement_fee': True},
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], 'REVOKED')

        card.refresh_from_db()
        self.assertEqual(card.status, CredentialStatus.REVOKED)

    def test_api_verify_credential(self):
        """POST /api/v1/credentials/verify/ returns validation details for edge agents."""
        card = issue_credential(
            foundation_id=self.foundation.id,
            student=self.student_a,
            type=CredentialType.RFID,
            uid="VERIFY_ME_8888",
            user=self.user_admin,
        )

        self.client.force_authenticate(user=self.user_admin)
        resp = self.client.post(
            '/api/v1/credentials/verify/',
            data={'uid': 'VERIFY_ME_8888'},
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['valid'])
        self.assertEqual(resp.data['holder_type'], 'student')
        self.assertEqual(resp.data['student']['nis'], '2026-SD-001')

    def test_school_admin_cross_school_credential_isolation_404(self):
        """School A admin receives 404 when querying or revoking School B credential."""
        card_b = issue_credential(
            foundation_id=self.foundation.id,
            student=self.student_b,
            type=CredentialType.RFID,
            uid="CARD_SMP_9999",
            user=self.user_admin,
        )

        self.client.force_authenticate(user=self.user_school_a)

        # GET detail of School B credential -> 404
        resp = self.client.get(f'/api/v1/credentials/{card_b.id}/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

        # Revoke School B credential -> 404
        resp_revoke = self.client.post(
            f'/api/v1/credentials/{card_b.id}/revoke/',
            data={'reason': 'Illegal attempt'},
            format='json'
        )
        self.assertEqual(resp_revoke.status_code, status.HTTP_404_NOT_FOUND)

    def test_cross_tenant_credential_isolation_404(self):
        """Foundation 1 admin receives 404 for Foundation 2 student credential."""
        # Create student in Foundation 2
        person_f2 = Person.objects.create(
            foundation_id=self.foundation_2.id,
            full_name="Santri F2",
        )
        student_f2 = Student.objects.create(
            foundation_id=self.foundation_2.id,
            school=self.school_f2,
            person=person_f2,
            nisn="0022222222",
            nis="SMA-F2-001",
            status=Student.STATUS_ACTIVE,
        )
        card_f2 = issue_credential(
            foundation_id=self.foundation_2.id,
            student=student_f2,
            type=CredentialType.RFID,
            uid="CARD_F2_1111",
        )

        self.client.force_authenticate(user=self.user_admin)
        resp = self.client.get(f'/api/v1/credentials/{card_f2.id}/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
