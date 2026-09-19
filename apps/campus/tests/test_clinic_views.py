import datetime as _dt

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.academic.models import ClassEnrollment
from apps.academic.tests.base import build_academic_fixture
from apps.attendance.models import Credential, CredentialStatus, CredentialType
from apps.campus.models import ClinicOutcome, ClinicVisit, HealthProfile, MedicationStock
from apps.core.models import AuditEvent
from apps.identity.models import Guardian, GuardianLink, Person, RoleAssignment, School, Staff, Student, User
from apps.identity.rbac import ROLE_CLINIC_OFFICER, ROLE_PARENT, ROLE_SCHOOL_ADMIN, ROLE_TEACHER
from educore.middleware.tenancy import set_current_foundation_id


def assign(fx, user, role, scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=None):
    RoleAssignment.all_tenants.create(
        foundation_id=fx['foundation'].id,
        user=user,
        role=role,
        scope_type=scope_type,
        scope_id=scope_id if scope_id is not None else fx['school'].id,
    )


class ClinicVisitViewSetTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        set_current_foundation_id(self.fx['foundation'].id)
        assign(self.fx, self.fx['teacher_user'], ROLE_CLINIC_OFFICER)
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['teacher_user'])

    def test_create_clinic_visit_as_clinic_officer(self):
        resp = self.client.post('/api/v1/campus/clinic-visits/', {
            'student_id': self.fx['student'].id,
            'complaint': 'Demam tinggi',
            'outcome': ClinicOutcome.RETURNED_TO_CLASS,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        self.assertEqual(resp.data['complaint'], 'Demam tinggi')
        # TenancyMiddleware clears the thread-local foundation context after each
        # request finishes, so re-establish it before querying the tenant-scoped
        # manager directly (matches the pattern used elsewhere in this test suite).
        set_current_foundation_id(self.fx['foundation'].id)
        self.assertTrue(ClinicVisit.objects.filter(student=self.fx['student']).exists())

    def test_create_clinic_visit_rejects_bad_outcome(self):
        resp = self.client.post('/api/v1/campus/clinic-visits/', {
            'student_id': self.fx['student'].id,
            'complaint': 'Demam tinggi',
            'outcome': 'RESTING_IN_UKS',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_teacher_without_clinic_write_cannot_create(self):
        other_person_user = self.fx['teacher_user']
        RoleAssignment.all_tenants.filter(user=other_person_user, role=ROLE_CLINIC_OFFICER).delete()
        assign(self.fx, other_person_user, ROLE_TEACHER)
        resp = self.client.post('/api/v1/campus/clinic-visits/', {
            'student_id': self.fx['student'].id,
            'complaint': 'Demam tinggi',
            'outcome': ClinicOutcome.RETURNED_TO_CLASS,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class StudentMedicalAlertViewTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        set_current_foundation_id(self.fx['foundation'].id)
        ClassEnrollment.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.fx['student'],
            class_group=self.fx['class_group'], enrolled_at=_dt.date(2026, 7, 1), is_active=True,
        )
        HealthProfile.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.fx['student'],
            allergies=['Penisilin'],
        )
        assign(self.fx, self.fx['teacher_user'], ROLE_TEACHER)
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['teacher_user'])

    def test_teacher_sees_alert_flag_and_allergies_when_policy_enabled(self):
        resp = self.client.get(f"/api/v1/campus/students/{self.fx['student'].id}/medical-alert/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['has_medical_alert'])
        self.assertEqual(resp.data['allergies'], ['Penisilin'])

    def test_teacher_sees_flag_only_when_policy_disabled(self):
        from apps.campus.services_clinic import get_or_create_clinic_policy
        policy = get_or_create_clinic_policy(self.fx['school'])
        policy.teacher_sees_allergies = False
        policy.save(update_fields=['teacher_sees_allergies'])

        resp = self.client.get(f"/api/v1/campus/students/{self.fx['student'].id}/medical-alert/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['has_medical_alert'])
        self.assertEqual(resp.data.get('allergies', []), [])

    def test_teacher_at_school_a_cannot_see_medical_alert_of_student_at_school_b(self):
        # Same foundation, different school: fx['teacher_user'] is scoped to fx['school']
        # (School A) only, and has no guardian relationship to a School B student.
        school_b = School.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            name="SMP Cendekia Mandiri B",
            npsn=f"219{str(abs(hash(self.fx['school'].npsn)) % 100000).zfill(5)}",
            level=School.LEVEL_SMP,
        )
        student_b_person = Person.all_tenants.create(
            foundation_id=self.fx['foundation'].id, nik="3471010101010303", full_name="Budi Santoso"
        )
        student_b = Student.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            school=school_b,
            person=student_b_person,
            nisn="1122334466",
            nis="B-001",
            status=Student.STATUS_ACTIVE,
        )
        HealthProfile.objects.create(
            foundation_id=self.fx['foundation'].id, student=student_b,
            allergies=['Kacang'],
        )

        resp = self.client.get(f"/api/v1/campus/students/{student_b.id}/medical-alert/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class StudentHealthProfileViewCrossSchoolTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        set_current_foundation_id(self.fx['foundation'].id)
        assign(self.fx, self.fx['teacher_user'], ROLE_CLINIC_OFFICER)
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['teacher_user'])

    def test_clinic_officer_at_school_a_cannot_put_health_profile_of_student_at_school_b(self):
        # Same foundation, different school: fx['teacher_user'] holds clinic.write via a
        # school-scoped ROLE_CLINIC_OFFICER assignment on fx['school'] (School A) only, and
        # has no guardian relationship to a School B student.
        school_b = School.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            name="SMP Cendekia Mandiri B",
            npsn=f"229{str(abs(hash(self.fx['school'].npsn)) % 100000).zfill(5)}",
            level=School.LEVEL_SMP,
        )
        student_b_person = Person.all_tenants.create(
            foundation_id=self.fx['foundation'].id, nik="3471010101010404", full_name="Citra Lestari"
        )
        student_b = Student.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            school=school_b,
            person=student_b_person,
            nisn="1122334477",
            nis="B-002",
            status=Student.STATUS_ACTIVE,
        )

        resp = self.client.put(
            f"/api/v1/campus/students/{student_b.id}/health-profile/",
            {'allergies': ['Debu']},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_clinic_officer_at_same_school_can_get_and_put_health_profile(self):
        # Regression test: a ROLE_CLINIC_OFFICER user scoped to the student's own school
        # must be able to GET and PUT that student's health profile. can_guardian_access_student
        # previously 404'd this legitimate same-school clinic.write/clinic.read use case because
        # STAFF_ROLES (apps/identity/guardian_access.py) did not include ROLE_CLINIC_OFFICER.
        HealthProfile.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.fx['student'],
            allergies=['Debu'],
        )

        get_resp = self.client.get(
            f"/api/v1/campus/students/{self.fx['student'].id}/health-profile/"
        )
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK, get_resp.content)
        self.assertEqual(get_resp.data['allergies'], ['Debu'])

        put_resp = self.client.put(
            f"/api/v1/campus/students/{self.fx['student'].id}/health-profile/",
            {'allergies': ['Debu', 'Kacang']},
            format='json',
        )
        self.assertEqual(put_resp.status_code, status.HTTP_200_OK, put_resp.content)
        self.assertEqual(put_resp.data['allergies'], ['Debu', 'Kacang'])


class ClinicMobileContractTests(TestCase):
    """What the clinic officer's mobile app relies on: student search scoped to the officer's school, and the
    shape of a refused visit (mobile/src/services/clinicStaff.ts)."""

    def setUp(self):
        self.fx = build_academic_fixture()
        set_current_foundation_id(self.fx['foundation'].id)
        assign(self.fx, self.fx['teacher_user'], ROLE_CLINIC_OFFICER)
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['teacher_user'])

    def test_student_search_finds_own_school_only_and_serves_what_the_lookup_reads(self):
        school_b = School.all_tenants.create(
            foundation_id=self.fx['foundation'].id, name="SMP B", npsn="22900009", level=School.LEVEL_SMP,
        )
        Student.all_tenants.create(
            foundation_id=self.fx['foundation'].id, school=school_b, nisn="1122334488", nis="B-777",
            status=Student.STATUS_ACTIVE,
            person=Person.all_tenants.create(
                foundation_id=self.fx['foundation'].id, nik="3471010101010505", full_name="Dewi Lain Sekolah",
            ),
        )
        own = self.fx['student']

        resp = self.client.get('/api/v1/students/', {'q': own.nis, 'status': 'ACTIVE'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        results = resp.data['results'] if isinstance(resp.data, dict) else resp.data
        self.assertEqual([r['id'] for r in results], [own.id])
        row = results[0]
        self.assertEqual(row['nis'], own.nis)
        self.assertEqual(row['school'], own.school_id)
        self.assertTrue(row['school_name'])
        self.assertTrue(row['person']['full_name'])

        other = self.client.get('/api/v1/students/', {'q': 'B-777', 'status': 'ACTIVE'})
        other_results = other.data['results'] if isinstance(other.data, dict) else other.data
        self.assertEqual(other_results, [])

    def test_medication_without_guardian_consent_is_refused_with_a_message_list(self):
        stock = MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], name='Paracetamol', unit='tablet',
            quantity=10, expiry_date=_dt.date(2030, 1, 1), reorder_level=2,
        )
        resp = self.client.post('/api/v1/campus/clinic-visits/', {
            'student_id': self.fx['student'].id, 'complaint': 'Demam', 'outcome': ClinicOutcome.RETURNED_TO_CLASS,
            'medication_id': stock.id, 'medication_quantity': 1,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIsInstance(resp.data, list)
        self.assertIn('persetujuan wali', ' '.join(str(m) for m in resp.data))
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, 10)


class ClinicCrossTenantIsolationTests(TestCase):
    def setUp(self):
        self.fx_a = build_academic_fixture(foundation_name="Yayasan A")
        self.fx_b = build_academic_fixture(foundation_name="Yayasan B")
        set_current_foundation_id(self.fx_a['foundation'].id)
        assign(self.fx_a, self.fx_a['teacher_user'], ROLE_CLINIC_OFFICER)

        self.visit_b = ClinicVisit.objects.create(
            foundation_id=self.fx_b['foundation'].id,
            school=self.fx_b['school'],
            student=self.fx_b['student'],
            complaint_encrypted='x',
            outcome=ClinicOutcome.RETURNED_TO_CLASS,
            handled_by=self.fx_b['teacher'],
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.fx_a['teacher_user'])

    def test_cannot_read_other_foundation_clinic_visit(self):
        set_current_foundation_id(self.fx_a['foundation'].id)
        resp = self.client.get(f'/api/v1/campus/clinic-visits/{self.visit_b.id}/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_medication_stock_alerts_scoped_to_own_school(self):
        MedicationStock.objects.create(
            foundation_id=self.fx_b['foundation'].id, school=self.fx_b['school'],
            name='Obat B', unit='tablet', quantity=1, reorder_level=100,
            expiry_date=_dt.date(2030, 1, 1),
        )
        set_current_foundation_id(self.fx_a['foundation'].id)
        resp = self.client.get(f"/api/v1/campus/schools/{self.fx_a['school'].id}/medication-stock/alerts/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, [])


class ClinicHealthPiiAuditTests(TestCase):
    """Finding 3: health-PII mutation endpoints must write an audit event."""

    def setUp(self):
        self.fx = build_academic_fixture()
        set_current_foundation_id(self.fx['foundation'].id)
        assign(self.fx, self.fx['teacher_user'], ROLE_CLINIC_OFFICER)
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['teacher_user'])

    def test_clinic_policy_put_writes_audit_event(self):
        resp = self.client.put(
            f"/api/v1/campus/schools/{self.fx['school'].id}/clinic-policy/",
            {'teacher_sees_allergies': False},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        set_current_foundation_id(self.fx['foundation'].id)
        self.assertTrue(
            AuditEvent.objects.filter(
                action='campus.clinic_policy.updated',
                entity_type='ClinicPolicy',
            ).exists()
        )

    def test_health_profile_put_writes_audit_event_without_pii_values(self):
        resp = self.client.put(
            f"/api/v1/campus/students/{self.fx['student'].id}/health-profile/",
            {'allergies': ['Kacang', 'Debu']},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        set_current_foundation_id(self.fx['foundation'].id)
        event = AuditEvent.objects.filter(
            action='campus.health_profile.updated',
            entity_type='HealthProfile',
        ).first()
        self.assertIsNotNone(event)
        self.assertIn('allergies', event.diff.get('changed_fields', []))
        self.assertNotIn('Kacang', str(event.diff))
        self.assertNotIn('Debu', str(event.diff))

    def test_medication_stock_create_update_destroy_write_audit_events(self):
        create_resp = self.client.post(
            '/api/v1/campus/medication-stock/',
            {
                'school': self.fx['school'].id,
                'name': 'Paracetamol',
                'unit': 'tablet',
                'quantity': 50,
                'reorder_level': 10,
                'expiry_date': '2030-01-01',
            },
            format='json',
        )
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED, create_resp.content)
        stock_id = create_resp.data['id']
        set_current_foundation_id(self.fx['foundation'].id)
        self.assertTrue(
            AuditEvent.objects.filter(action='campus.medication_stock.created', entity_id=str(stock_id)).exists()
        )

        update_resp = self.client.patch(
            f'/api/v1/campus/medication-stock/{stock_id}/',
            {'quantity': 40},
            format='json',
        )
        self.assertEqual(update_resp.status_code, status.HTTP_200_OK, update_resp.content)
        set_current_foundation_id(self.fx['foundation'].id)
        self.assertTrue(
            AuditEvent.objects.filter(action='campus.medication_stock.updated', entity_id=str(stock_id)).exists()
        )

        destroy_resp = self.client.delete(f'/api/v1/campus/medication-stock/{stock_id}/')
        self.assertEqual(destroy_resp.status_code, status.HTTP_204_NO_CONTENT, destroy_resp.content)
        set_current_foundation_id(self.fx['foundation'].id)
        self.assertTrue(
            AuditEvent.objects.filter(action='campus.medication_stock.deleted', entity_id=str(stock_id)).exists()
        )


class ClinicVisitCardTapLookupTests(TestCase):
    """LIF-001: card-tap student lookup at clinic intake (Notion follow-up to PR #147)."""

    def setUp(self):
        self.fx = build_academic_fixture()
        set_current_foundation_id(self.fx['foundation'].id)
        assign(self.fx, self.fx['teacher_user'], ROLE_CLINIC_OFFICER)
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['teacher_user'])

        self.card = Credential.objects.create(
            foundation_id=self.fx['foundation'].id,
            student=self.fx['student'],
            type=CredentialType.RFID,
            uid='AA11BB22',
            status=CredentialStatus.ACTIVE,
        )

    def test_create_clinic_visit_via_card_uid(self):
        resp = self.client.post('/api/v1/campus/clinic-visits/', {
            'card_uid': 'aa11bb22',
            'complaint': 'Pusing',
            'outcome': ClinicOutcome.RETURNED_TO_CLASS,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        self.assertEqual(resp.data['student'], self.fx['student'].id)
        set_current_foundation_id(self.fx['foundation'].id)
        self.assertTrue(ClinicVisit.objects.filter(student=self.fx['student']).exists())

    def test_create_clinic_visit_rejects_unknown_card_uid(self):
        resp = self.client.post('/api/v1/campus/clinic-visits/', {
            'card_uid': 'DOES-NOT-EXIST',
            'complaint': 'Pusing',
            'outcome': ClinicOutcome.RETURNED_TO_CLASS,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_clinic_visit_rejects_revoked_card_uid(self):
        self.card.status = CredentialStatus.REVOKED
        self.card.revoked_reason = 'Hilang'
        self.card.save(update_fields=['status', 'revoked_reason'])
        resp = self.client.post('/api/v1/campus/clinic-visits/', {
            'card_uid': 'AA11BB22',
            'complaint': 'Pusing',
            'outcome': ClinicOutcome.RETURNED_TO_CLASS,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_clinic_visit_rejects_staff_only_card_uid(self):
        staff_person = Person.all_tenants.create(
            foundation_id=self.fx['foundation'].id, nik='3471010101019876', full_name='Pak Guru Piket',
        )
        staff_user = User.objects.create(
            foundation_id=self.fx['foundation'].id, phone_e164='+62811999999', email='piket@sch.id', full_name='Pak Guru Piket',
        )
        staff = Staff.all_tenants.create(
            foundation_id=self.fx['foundation'].id, person=staff_person, user=staff_user, school=self.fx['school'],
            nip='199001012020011001', employment_type=Staff.TYPE_PERMANENT, join_date=_dt.date(2020, 1, 1), status=Staff.STATUS_ACTIVE,
        )
        Credential.objects.create(
            foundation_id=self.fx['foundation'].id, staff=staff, type=CredentialType.RFID,
            uid='STAFFCARD01', status=CredentialStatus.ACTIVE,
        )
        resp = self.client.post('/api/v1/campus/clinic-visits/', {
            'card_uid': 'STAFFCARD01',
            'complaint': 'Pusing',
            'outcome': ClinicOutcome.RETURNED_TO_CLASS,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_clinic_visit_rejects_both_student_id_and_card_uid(self):
        resp = self.client.post('/api/v1/campus/clinic-visits/', {
            'student_id': self.fx['student'].id,
            'card_uid': 'AA11BB22',
            'complaint': 'Pusing',
            'outcome': ClinicOutcome.RETURNED_TO_CLASS,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_clinic_visit_rejects_neither_student_id_nor_card_uid(self):
        resp = self.client.post('/api/v1/campus/clinic-visits/', {
            'complaint': 'Pusing',
            'outcome': ClinicOutcome.RETURNED_TO_CLASS,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class MedicationStockSchoolScopingTests(TestCase):
    """Medication stock listing must be school-scoped for staff, not foundation-wide by default."""

    def setUp(self):
        self.fx = build_academic_fixture()
        set_current_foundation_id(self.fx['foundation'].id)
        self.school_b = School.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            name="SMP Cendekia Mandiri C",
            npsn=f"239{str(abs(hash(self.fx['school'].npsn)) % 100000).zfill(5)}",
            level=School.LEVEL_SMP,
        )
        self.stock_a = MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            name='Paracetamol', unit='tablet', quantity=50, reorder_level=10,
            expiry_date=_dt.date(2030, 1, 1),
        )
        self.stock_b = MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.school_b,
            name='Antiseptik', unit='botol', quantity=20, reorder_level=5,
            expiry_date=_dt.date(2030, 1, 1),
        )
        self.client = APIClient()

    def test_clinic_officer_at_school_a_only_sees_school_a_stock(self):
        assign(self.fx, self.fx['teacher_user'], ROLE_CLINIC_OFFICER, scope_id=self.fx['school'].id)
        self.client.force_authenticate(user=self.fx['teacher_user'])
        resp = self.client.get('/api/v1/campus/medication-stock/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = {row['name'] for row in resp.data['results']}
        self.assertIn('Paracetamol', names)
        self.assertNotIn('Antiseptik', names)

    def test_foundation_admin_sees_stock_across_all_schools(self):
        from apps.identity.rbac import ROLE_FOUNDATION_ADMIN
        assign(self.fx, self.fx['teacher_user'], ROLE_FOUNDATION_ADMIN, scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.fx['foundation'].id)
        self.client.force_authenticate(user=self.fx['teacher_user'])
        resp = self.client.get('/api/v1/campus/medication-stock/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = {row['name'] for row in resp.data['results']}
        self.assertIn('Paracetamol', names)
        self.assertIn('Antiseptik', names)

    def test_guardian_with_clinic_read_but_no_staff_role_sees_no_stock(self):
        from apps.identity.rbac import ROLE_PARENT

        parent_person = Person.all_tenants.create(
            foundation_id=self.fx['foundation'].id, nik='3471010101019991', full_name='Bu Wali',
        )
        parent_user = User.objects.create(
            foundation_id=self.fx['foundation'].id, phone_e164='+62817000001', email='wali@sch.id', full_name='Bu Wali',
        )
        # ROLE_PARENT holds clinic.read (for the parent-app follow-up item), but medication
        # stock is operational inventory data, not guardian-facing — a parent role alone
        # (foundation-scoped, no staff assignment) must see zero rows, not 403 (they do hold
        # the permission) and not every school's stock (they hold no staff scope).
        assign(self.fx, parent_user, ROLE_PARENT, scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.fx['foundation'].id)
        self.client.force_authenticate(user=parent_user)
        resp = self.client.get('/api/v1/campus/medication-stock/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['results'], [])


class GuardianClinicReadScopingTests(TestCase):
    """[Open Item] Parent App: Guardian Read Scoping for Clinic Visit History.

    No parent client exists anywhere in this repo (same recurring precedent as
    report cards/homework/wallet refund), so the item's own client-UI scope stays
    deferred. But the guardian-scoping mechanism it depends on
    (get_guardian_student_ids / can_guardian_access_student) was already wired
    into these endpoints during PR #147's task review and had zero test coverage
    calling them as a ROLE_PARENT user — this closes that verification gap directly
    against the real endpoints, exactly as any client (parent app included) would.
    """

    def setUp(self):
        self.fx = build_academic_fixture()
        set_current_foundation_id(self.fx['foundation'].id)

        self.other_person = Person.all_tenants.create(
            foundation_id=self.fx['foundation'].id, nik='3471010101019992', full_name='Andi Wijaya',
        )
        self.other_student = Student.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            person=self.other_person,
            nisn='1122334488',
            nis='C-001',
            status=Student.STATUS_ACTIVE,
        )

        parent_person = Person.all_tenants.create(
            foundation_id=self.fx['foundation'].id, nik='3471010101019993', full_name='Bu Sari',
        )
        self.parent_user = User.objects.create(
            foundation_id=self.fx['foundation'].id, phone_e164='+62817000002', email='sari@wali.sch.id', full_name='Bu Sari',
        )
        guardian = Guardian.all_tenants.create(
            foundation_id=self.fx['foundation'].id, person=parent_person, user=self.parent_user,
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.fx['foundation'].id, guardian=guardian, student=self.fx['student'],
            relation=GuardianLink.RELATION_MOTHER, financial_responsible=True,
        )
        assign(self.fx, self.parent_user, ROLE_PARENT, scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.fx['foundation'].id)

        from apps.campus.crypto import encrypt_note

        self.own_visit = ClinicVisit.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], student=self.fx['student'],
            complaint_encrypted=encrypt_note('Demam'), outcome=ClinicOutcome.RETURNED_TO_CLASS, handled_by=self.fx['teacher'],
        )
        self.other_visit = ClinicVisit.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], student=self.other_student,
            complaint_encrypted=encrypt_note('Pusing'), outcome=ClinicOutcome.RETURNED_TO_CLASS, handled_by=self.fx['teacher'],
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.parent_user)

    def test_guardian_lists_own_childs_clinic_visits_only(self):
        resp = self.client.get('/api/v1/campus/clinic-visits/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        visit_ids = {row['id'] for row in resp.data['results']}
        self.assertIn(self.own_visit.id, visit_ids)
        self.assertNotIn(self.other_visit.id, visit_ids)

    def test_guardian_cannot_retrieve_other_students_clinic_visit_by_id(self):
        resp = self.client.get(f'/api/v1/campus/clinic-visits/{self.other_visit.id}/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_guardian_can_retrieve_own_childs_clinic_visit_by_id(self):
        resp = self.client.get(f'/api/v1/campus/clinic-visits/{self.own_visit.id}/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)

    def test_guardian_can_get_own_childs_health_profile(self):
        HealthProfile.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.fx['student'], allergies=['Debu'],
        )
        resp = self.client.get(f"/api/v1/campus/students/{self.fx['student'].id}/health-profile/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertEqual(resp.data['allergies'], ['Debu'])

    def test_guardian_cannot_get_other_students_health_profile(self):
        HealthProfile.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.other_student, allergies=['Kacang'],
        )
        resp = self.client.get(f"/api/v1/campus/students/{self.other_student.id}/health-profile/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
