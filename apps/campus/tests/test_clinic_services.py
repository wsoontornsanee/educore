import datetime

from django.test import TestCase
from django.utils import timezone

from apps.academic.tests.base import build_academic_fixture
from apps.campus.models import MedicationStock
from apps.campus.services_clinic import (
    get_medication_stock_alerts,
    get_or_create_clinic_policy,
    get_or_create_health_profile,
)


class ClinicPolicyAndAlertServiceTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()

    def test_get_or_create_clinic_policy_is_idempotent(self):
        p1 = get_or_create_clinic_policy(self.fx['school'])
        p2 = get_or_create_clinic_policy(self.fx['school'])
        self.assertEqual(p1.id, p2.id)

    def test_get_or_create_health_profile_is_idempotent(self):
        h1 = get_or_create_health_profile(self.fx['student'])
        h2 = get_or_create_health_profile(self.fx['student'])
        self.assertEqual(h1.id, h2.id)

    def test_medication_stock_alerts_below_reorder_level(self):
        low = MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            name='Oralit', unit='sachet', quantity=2, reorder_level=10,
            expiry_date=datetime.date(2030, 1, 1),
        )
        MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            name='Perban', unit='pcs', quantity=50, reorder_level=10,
            expiry_date=datetime.date(2030, 1, 1),
        )
        alerts = get_medication_stock_alerts(self.fx['school'])
        self.assertIn(low, list(alerts))
        self.assertEqual(alerts.count(), 1)

    def test_medication_stock_alerts_near_expiry(self):
        near_expiry = MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            name='Antiseptik', unit='botol', quantity=100, reorder_level=5,
            expiry_date=(timezone.now().date() + datetime.timedelta(days=10)),
        )
        alerts = get_medication_stock_alerts(self.fx['school'])
        self.assertIn(near_expiry, list(alerts))

    def test_medication_stock_alerts_excludes_healthy_stock(self):
        MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            name='Vitamin C', unit='tablet', quantity=200, reorder_level=10,
            expiry_date=datetime.date(2030, 1, 1),
        )
        alerts = get_medication_stock_alerts(self.fx['school'])
        self.assertEqual(alerts.count(), 0)


import datetime as _dt

from django.core.exceptions import ValidationError

from apps.campus.crypto import decrypt_note
from apps.campus.models import ClinicOutcome, ClinicVisit, MedicationStock
from apps.campus.services_clinic import record_clinic_visit
from apps.compliance.models import ConsentPurpose, DataSubjectRequestSubjectType
from apps.compliance.services import record_consent
from apps.core.models import AuditEvent


class RecordClinicVisitTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.stock = MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            name='Paracetamol', unit='tablet', quantity=50, reorder_level=10,
            expiry_date=_dt.date(2030, 1, 1),
        )

    def test_record_visit_returned_to_class_no_medication(self):
        visit = record_clinic_visit(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            handled_by=self.fx['teacher'],
            complaint='Sakit kepala ringan',
            outcome=ClinicOutcome.RETURNED_TO_CLASS,
        )
        self.assertEqual(visit.outcome, ClinicOutcome.RETURNED_TO_CLASS)
        self.assertEqual(decrypt_note(visit.complaint_encrypted), 'Sakit kepala ringan')
        self.assertIsNone(visit.guardian_notified_at)
        self.assertTrue(AuditEvent.objects.filter(action='campus.clinic_visit.recorded', entity_id=str(visit.id)).exists())

    def test_record_visit_with_medication_requires_consent(self):
        with self.assertRaises(ValidationError):
            record_clinic_visit(
                foundation_id=self.fx['foundation'].id,
                school=self.fx['school'],
                student=self.fx['student'],
                handled_by=self.fx['teacher'],
                complaint='Demam',
                outcome=ClinicOutcome.RETURNED_TO_CLASS,
                medication=self.stock,
                medication_quantity=1,
            )

    def test_record_visit_with_medication_and_standing_consent_decrements_stock(self):
        record_consent(
            subject_type=DataSubjectRequestSubjectType.STUDENT,
            subject_id=self.fx['student'].id,
            foundation_id=self.fx['foundation'].id,
            purpose=ConsentPurpose.HEALTH_DATA,
            granted_by='tester',
        )
        visit = record_clinic_visit(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            handled_by=self.fx['teacher'],
            complaint='Demam',
            outcome=ClinicOutcome.RETURNED_TO_CLASS,
            medication=self.stock,
            medication_quantity=2,
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 48)
        self.assertEqual(visit.medication_quantity_used, 2)

    def test_record_visit_with_medication_and_per_incident_confirmation(self):
        visit = record_clinic_visit(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            handled_by=self.fx['teacher'],
            complaint='Demam',
            outcome=ClinicOutcome.RETURNED_TO_CLASS,
            medication=self.stock,
            medication_quantity=1,
            guardian_consent_confirmed=True,
            guardian_consent_note='Dikonfirmasi via telepon oleh ibu kandung',
        )
        self.assertTrue(visit.guardian_consent_confirmed)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 49)

    def test_record_visit_rejects_insufficient_stock(self):
        with self.assertRaises(ValidationError):
            record_clinic_visit(
                foundation_id=self.fx['foundation'].id,
                school=self.fx['school'],
                student=self.fx['student'],
                handled_by=self.fx['teacher'],
                complaint='Demam',
                outcome=ClinicOutcome.RETURNED_TO_CLASS,
                medication=self.stock,
                medication_quantity=999,
                guardian_consent_confirmed=True,
            )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 50)

    def test_record_visit_rejects_wrong_school_student(self):
        other_fx = build_academic_fixture(foundation_name="Yayasan Lain")
        with self.assertRaises(ValidationError):
            record_clinic_visit(
                foundation_id=self.fx['foundation'].id,
                school=self.fx['school'],
                student=other_fx['student'],
                handled_by=self.fx['teacher'],
                complaint='Demam',
                outcome=ClinicOutcome.RETURNED_TO_CLASS,
            )

    def test_record_visit_decrements_stock_when_thread_local_foundation_absent(self):
        """Regression: the stock lock must scope by the explicit foundation_id
        argument, not by TenantManager's thread-local context. Simulates a
        cron/background-task call path where the thread-local is unset.

        Uses per-incident guardian consent confirmation (rather than standing
        consent) so the assertion is isolated to the stock select_for_update
        lookup, not to has_active_health_consent's own tenancy behavior."""
        from educore.middleware.tenancy import (
            clear_current_foundation_id,
            get_current_foundation_id,
            set_current_foundation_id,
        )

        previous = get_current_foundation_id()
        clear_current_foundation_id()
        try:
            visit = record_clinic_visit(
                foundation_id=self.fx['foundation'].id,
                school=self.fx['school'],
                student=self.fx['student'],
                handled_by=self.fx['teacher'],
                complaint='Demam',
                outcome=ClinicOutcome.RETURNED_TO_CLASS,
                medication=self.stock,
                medication_quantity=3,
                guardian_consent_confirmed=True,
            )
        finally:
            if previous is not None:
                set_current_foundation_id(previous)
            else:
                clear_current_foundation_id()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 47)
        self.assertEqual(visit.medication_quantity_used, 3)

    def test_record_visit_scopes_stock_lock_to_explicit_foundation_when_thread_local_mismatched(self):
        """Regression: if the thread-local foundation context differs from the
        explicit foundation_id argument, the stock lock must still resolve
        using the explicit parameter (matching the wallet locking precedent),
        not the thread-local tenant."""
        from educore.middleware.tenancy import (
            get_current_foundation_id,
            set_current_foundation_id,
        )

        other_fx = build_academic_fixture(foundation_name="Yayasan Lain")
        previous = get_current_foundation_id()
        set_current_foundation_id(other_fx['foundation'].id)
        try:
            visit = record_clinic_visit(
                foundation_id=self.fx['foundation'].id,
                school=self.fx['school'],
                student=self.fx['student'],
                handled_by=self.fx['teacher'],
                complaint='Demam',
                outcome=ClinicOutcome.RETURNED_TO_CLASS,
                medication=self.stock,
                medication_quantity=1,
                guardian_consent_confirmed=True,
            )
        finally:
            if previous is not None:
                set_current_foundation_id(previous)
            else:
                from educore.middleware.tenancy import clear_current_foundation_id
                clear_current_foundation_id()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 49)
        self.assertEqual(visit.medication_quantity_used, 1)


from apps.academic.models import ClassEnrollment
from apps.attendance.models import AttendanceDay, AttendanceStatus
from apps.identity.models import Guardian, GuardianLink, Person, User
from apps.notifications.models import NotificationCategory, NotificationIntent


def enroll_student_in_class(fx):
    return ClassEnrollment.objects.create(
        foundation_id=fx['foundation'].id,
        student=fx['student'],
        class_group=fx['class_group'],
        enrolled_at=_dt.date(2026, 7, 1),
        is_active=True,
    )


def attach_guardian_to(fx, nik="3471010101019999", full_name="Pak Joko"):
    person = Person.all_tenants.create(foundation_id=fx['foundation'].id, nik=nik, full_name=full_name)
    user = User.objects.create(
        foundation_id=fx['foundation'].id,
        phone_e164=f"+62818{nik[-7:]}",
        email=f"{nik}@wali.sch.id",
        full_name=full_name,
    )
    guardian = Guardian.all_tenants.create(foundation_id=fx['foundation'].id, person=person, user=user)
    GuardianLink.all_tenants.create(
        foundation_id=fx['foundation'].id, guardian=guardian, student=fx['student'],
        relation=GuardianLink.RELATION_FATHER, financial_responsible=True,
    )
    return guardian


class ClinicVisitAttendanceAndNotificationTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        enroll_student_in_class(self.fx)
        self.guardian = attach_guardian_to(self.fx)

    def test_returned_to_class_does_not_touch_attendance_or_notify(self):
        visit = record_clinic_visit(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], student=self.fx['student'],
            handled_by=self.fx['teacher'], complaint='Pusing ringan', outcome=ClinicOutcome.RETURNED_TO_CLASS,
        )
        self.assertIsNone(visit.guardian_notified_at)
        self.assertFalse(AttendanceDay.objects.filter(student=self.fx['student']).exists())
        self.assertFalse(NotificationIntent.objects.filter(category=NotificationCategory.CLINIC_INCIDENT).exists())

    def test_sent_home_creates_sakit_override_and_notifies_guardian_and_homeroom(self):
        visit = record_clinic_visit(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], student=self.fx['student'],
            handled_by=self.fx['teacher'], complaint='Demam tinggi', outcome=ClinicOutcome.SENT_HOME,
        )
        visit.refresh_from_db()
        self.assertIsNotNone(visit.guardian_notified_at)

        att_day = AttendanceDay.objects.get(student=self.fx['student'], date=visit.occurred_at.date())
        self.assertEqual(att_day.status, AttendanceStatus.SAKIT)
        self.assertTrue(att_day.is_override)

        intents = NotificationIntent.objects.filter(category=NotificationCategory.CLINIC_INCIDENT)
        recipient_users = set(intents.values_list('recipient_user_id', flat=True))
        self.assertIn(self.guardian.user_id, recipient_users)
        self.assertIn(self.fx['teacher_user'].id, recipient_users)

    def test_referred_also_creates_sakit_override(self):
        visit = record_clinic_visit(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], student=self.fx['student'],
            handled_by=self.fx['teacher'], complaint='Cedera serius', outcome=ClinicOutcome.REFERRED,
        )
        att_day = AttendanceDay.objects.get(student=self.fx['student'], date=visit.occurred_at.date())
        self.assertEqual(att_day.status, AttendanceStatus.SAKIT)

    def test_sent_home_overrides_existing_attendance_day(self):
        AttendanceDay.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], student=self.fx['student'],
            date=timezone.now().date(), status=AttendanceStatus.HADIR,
        )
        record_clinic_visit(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], student=self.fx['student'],
            handled_by=self.fx['teacher'], complaint='Demam', outcome=ClinicOutcome.SENT_HOME,
        )
        att_day = AttendanceDay.objects.get(student=self.fx['student'], date=timezone.now().date())
        self.assertEqual(att_day.status, AttendanceStatus.SAKIT)
        self.assertEqual(att_day.original_status, AttendanceStatus.HADIR)
