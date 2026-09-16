import datetime
from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient

from apps.identity.models import (
    Foundation,
    Guardian,
    GuardianLink,
    Person,
    RoleAssignment,
    School,
    Student,
    User,
)
from apps.academic.models import (
    AcademicYear,
    ClassGroup,
    ReportCard,
    ReportCardStatus,
    Term,
)
from apps.attendance.models import AttendanceDay, AttendanceStatus
from apps.campus.models import BehaviourCategory, BehaviourReason, BehaviourRecord
from apps.finance.models import Invoice, InvoiceStatus
from apps.wallet.models import Wallet, WalletStatus
from educore.middleware.tenancy import set_current_foundation_id, clear_current_foundation_id


class GuardianCrossDomainAccessTest(TestCase):
    """
    Comprehensive test suite ensuring parent/guardian mobile authorization
    strictly filters data across Identity, Academic, Attendance, Finance, Campus,
    and Wallet domains per spec/02 IAM-014, spec/08 PAR-010, PAR-017, and UU PDP No. 27/2022.
    """

    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Generasi Gemilang",
            brand_name="YGG",
        )
        set_current_foundation_id(self.foundation.id)

        self.school_a = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Generasi Gemilang",
            npsn="12345678",
            level=School.LEVEL_SMA,
            base_currency="IDR",
        )
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP Generasi Gemilang",
            npsn="87654321",
            level=School.LEVEL_SMP,
            base_currency="IDR",
        )

        # 3 Students:
        # Student 1: Child of Parent 1 at School A (Financial)
        self.p_student_1 = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Anak Satu Pratama",
            nik="3171000000000001",
        )
        self.student_1 = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            person=self.p_student_1,
            nis="1001",
        )

        # Student 2: Other child at School A (Not linked to Parent 1)
        self.p_student_2 = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Anak Dua Santoso",
            nik="3171000000000002",
        )
        self.student_2 = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            person=self.p_student_2,
            nis="1002",
        )

        # Student 3: Child of Parent 1 at School B (Non-financial)
        self.p_student_3 = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Anak Tiga Lestari",
            nik="3171000000000003",
        )
        self.student_3 = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school_b,
            person=self.p_student_3,
            nis="2001",
        )

        # Parent 1 User & Links
        self.user_parent = User.all_tenants.create_user(
            phone_e164="+6281234567801",
            password="password123",
            foundation_id=self.foundation.id,
            full_name="Bapak Hendra (Orang Tua)",
        )
        self.person_parent = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Hendra Gunawan",
            nik="3171000000000099",
        )
        self.guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation.id,
            person=self.person_parent,
            user=self.user_parent,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.user_parent,
            role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

        # Link 1: Student 1 (Financial Responsible = True)
        self.link_1 = GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=self.guardian,
            student=self.student_1,
            relation=GuardianLink.RELATION_FATHER,
            financial_responsible=True,
        )
        # Link 3: Student 3 (Financial Responsible = False per PAR-017)
        self.link_3 = GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=self.guardian,
            student=self.student_3,
            relation=GuardianLink.RELATION_FATHER,
            financial_responsible=False,
        )

        # Teacher / Staff User at School A
        self.user_staff = User.all_tenants.create_user(
            phone_e164="+6281234567802",
            password="password123",
            foundation_id=self.foundation.id,
            full_name="Ibu Guru Ratna",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.user_staff,
            role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school_a.id,
        )

        self.client = APIClient()

    def tearDown(self):
        clear_current_foundation_id()

    # -------------------------------------------------------------------------
    # 1. Identity Domain (Student Records)
    # -------------------------------------------------------------------------
    def test_parent_cannot_see_unlinked_student_in_identity(self):
        self.client.force_authenticate(user=self.user_parent)
        resp = self.client.get('/api/v1/students/')
        self.assertEqual(resp.status_code, 200)
        returned_ids = {s['id'] for s in resp.data['results']}
        self.assertEqual(returned_ids, {self.student_1.id, self.student_3.id})
        self.assertNotIn(self.student_2.id, returned_ids)

        # Unlinked student detail must return 404
        resp_unlinked = self.client.get(f'/api/v1/students/{self.student_2.id}/')
        self.assertEqual(resp_unlinked.status_code, 404)

    # -------------------------------------------------------------------------
    # 2. Finance Domain (Invoices & Financial Responsibility PAR-017)
    # -------------------------------------------------------------------------
    def test_parent_invoice_filtering_by_financial_responsibility(self):
        inv1 = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            student=self.student_1,
            number="INV/2026/001",
            period="2026-08",
            issue_date=datetime.date(2026, 8, 1),
            due_date=datetime.date(2026, 8, 10),
            total=Decimal('500000.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )
        inv2 = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            student=self.student_2,
            number="INV/2026/002",
            period="2026-08",
            issue_date=datetime.date(2026, 8, 1),
            due_date=datetime.date(2026, 8, 10),
            total=Decimal('500000.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )
        inv3 = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_b,
            student=self.student_3,
            number="INV/2026/003",
            period="2026-08",
            issue_date=datetime.date(2026, 8, 1),
            due_date=datetime.date(2026, 8, 10),
            total=Decimal('400000.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

        self.client.force_authenticate(user=self.user_parent)
        resp = self.client.get('/api/v1/finance/invoices/')
        self.assertEqual(resp.status_code, 200)
        returned_ids = {inv['id'] for inv in resp.data.get('results', resp.data)}
        # Parent 1 is financial_responsible for Student 1 only.
        # Student 3 is non-financial (PAR-017), Student 2 is unlinked.
        self.assertEqual(returned_ids, {inv1.id})

        # Detail on own financial child: 200
        resp_inv1 = self.client.get(f'/api/v1/finance/invoices/{inv1.id}/')
        self.assertEqual(resp_inv1.status_code, 200)

        # Detail on own non-financial child (PAR-017): 404
        resp_inv3 = self.client.get(f'/api/v1/finance/invoices/{inv3.id}/')
        self.assertEqual(resp_inv3.status_code, 404)

        # Detail on unlinked child: 404
        resp_inv2 = self.client.get(f'/api/v1/finance/invoices/{inv2.id}/')
        self.assertEqual(resp_inv2.status_code, 404)

    # -------------------------------------------------------------------------
    # 3. Attendance Domain (AttendanceDaily Records)
    # -------------------------------------------------------------------------
    def test_parent_attendance_day_filtering(self):
        att1 = AttendanceDay.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            student=self.student_1,
            date=datetime.date(2026, 8, 1),
            status=AttendanceStatus.HADIR,
        )
        att2 = AttendanceDay.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            student=self.student_2,
            date=datetime.date(2026, 8, 1),
            status=AttendanceStatus.HADIR,
        )
        att3 = AttendanceDay.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school_b,
            student=self.student_3,
            date=datetime.date(2026, 8, 1),
            status=AttendanceStatus.IZIN,
        )

        self.client.force_authenticate(user=self.user_parent)
        resp = self.client.get('/api/v1/attendance/daily/')
        self.assertEqual(resp.status_code, 200)
        returned_ids = {a['id'] for a in resp.data.get('results', resp.data)}
        # Must include student_1 and student_3, never student_2
        self.assertIn(att1.id, returned_ids)
        self.assertIn(att3.id, returned_ids)
        self.assertNotIn(att2.id, returned_ids)

        # Detail on unlinked student attendance day must be 404
        resp_unlinked = self.client.get(f'/api/v1/attendance/daily/{att2.id}/')
        self.assertEqual(resp_unlinked.status_code, 404)

    # -------------------------------------------------------------------------
    # 4. Campus Behaviour Domain (Records & Summary)
    # -------------------------------------------------------------------------
    def test_parent_behaviour_filtering(self):
        reason = BehaviourReason.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            code="TERTIB",
            label="Tertib dan Rajin",
            points=5,
            category=BehaviourCategory.MINOR,
        )
        rec1 = BehaviourRecord.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            student=self.student_1,
            reason=reason,
            points=5,
            note="Siswa rajin",
            recorded_by=self.user_staff,
        )
        rec2 = BehaviourRecord.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            student=self.student_2,
            reason=reason,
            points=5,
            note="Siswa lain",
            recorded_by=self.user_staff,
        )

        self.client.force_authenticate(user=self.user_parent)
        # Behaviour list
        resp = self.client.get('/api/v1/campus/behaviour-records/')
        self.assertEqual(resp.status_code, 200)
        returned_ids = {r['id'] for r in resp.data.get('results', resp.data)}
        self.assertIn(rec1.id, returned_ids)
        self.assertNotIn(rec2.id, returned_ids)

        # Behaviour Summary view (/api/v1/campus/students/:id/behaviour/)
        resp_sum_own = self.client.get(f'/api/v1/campus/students/{self.student_1.id}/behaviour/')
        self.assertEqual(resp_sum_own.status_code, 200)
        self.assertEqual(resp_sum_own.data['lifetime_net_points'], 5)

        # Behaviour Summary for unlinked student: 404
        resp_sum_unlinked = self.client.get(f'/api/v1/campus/students/{self.student_2.id}/behaviour/')
        self.assertEqual(resp_sum_unlinked.status_code, 404)

    # -------------------------------------------------------------------------
    # 5. Academic Domain (Report Cards)
    # -------------------------------------------------------------------------
    def test_parent_report_card_access(self):
        ay = AcademicYear.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            name="2026/2027",
            start_date=datetime.date(2026, 7, 1),
            end_date=datetime.date(2027, 6, 30),
            is_active=True,
        )
        term = Term.all_tenants.create(
            foundation_id=self.foundation.id,
            academic_year=ay,
            name="Ganjil 2026/2027",
            term_no=1,
            start_date=datetime.date(2026, 7, 1),
            end_date=datetime.date(2026, 12, 31),
            is_active=True,
        )
        cg = ClassGroup.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            academic_year=ay,
            grade_level=10,
            name="X-1",
        )
        ReportCard.all_tenants.create(
            foundation_id=self.foundation.id,
            student=self.student_1,
            term=term,
            class_group=cg,
            status=ReportCardStatus.PUBLISHED,
            is_current=True,
        )
        ReportCard.all_tenants.create(
            foundation_id=self.foundation.id,
            student=self.student_2,
            term=term,
            class_group=cg,
            status=ReportCardStatus.PUBLISHED,
            is_current=True,
        )

        self.client.force_authenticate(user=self.user_parent)
        # Own child report card: 200
        resp_rc1 = self.client.get(f'/api/v1/academic/students/{self.student_1.id}/report-cards/?term_id={term.id}')
        self.assertEqual(resp_rc1.status_code, 200)
        self.assertTrue(resp_rc1.data['visible'])

        # Unlinked child report card: 404
        resp_rc2 = self.client.get(f'/api/v1/academic/students/{self.student_2.id}/report-cards/?term_id={term.id}')
        self.assertEqual(resp_rc2.status_code, 404)

    # -------------------------------------------------------------------------
    # 6. Wallet Domain (Student Wallet)
    # -------------------------------------------------------------------------
    def test_parent_wallet_access(self):
        Wallet.all_tenants.create(
            foundation_id=self.foundation.id,
            student=self.student_1,
            balance=Decimal('100000.00'),
            currency='IDR',
            status=WalletStatus.ACTIVE,
        )
        Wallet.all_tenants.create(
            foundation_id=self.foundation.id,
            student=self.student_2,
            balance=Decimal('50000.00'),
            currency='IDR',
            status=WalletStatus.ACTIVE,
        )

        self.client.force_authenticate(user=self.user_parent)
        # Own child wallet: 200
        resp_wal1 = self.client.get(f'/api/v1/wallets/{self.student_1.id}/')
        self.assertEqual(resp_wal1.status_code, 200)
        self.assertEqual(Decimal(str(resp_wal1.data['balance'])), Decimal('100000.00'))

        # Unlinked child wallet: 404
        resp_wal2 = self.client.get(f'/api/v1/wallets/{self.student_2.id}/')
        self.assertEqual(resp_wal2.status_code, 404)
