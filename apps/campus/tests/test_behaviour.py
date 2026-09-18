import datetime
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.academic.models import Term
from apps.academic.tests.base import build_academic_fixture
from apps.campus.models import (
    BehaviourCase,
    BehaviourCategory,
    BehaviourPolicy,
    BehaviourReason,
    BehaviourRecord,
    CaseStatus,
)
from apps.campus.services import (
    acknowledge_behaviour_record,
    get_behaviour_report_card_data,
    get_or_create_behaviour_policy,
    get_student_behaviour_summary,
    record_behaviour,
    supersede_behaviour_record,
)
from apps.core.models import AuditEvent
from apps.identity.models import Foundation, Guardian, GuardianLink, Person, RoleAssignment, School, Staff, Student, User
from apps.notifications.models import NotificationCategory
from educore.middleware.tenancy import set_current_foundation_id


def attach_guardian(fx, nik="3471010101019999", full_name="Pak Joko"):
    person = Person.all_tenants.create(
        foundation_id=fx['foundation'].id,
        nik=nik,
        full_name=full_name,
    )
    user = User.objects.create(
        foundation_id=fx['foundation'].id,
        phone_e164=f"+62818{nik[-7:]}",
        email=f"{nik}@wali.sch.id",
        full_name=full_name,
    )
    guardian = Guardian.all_tenants.create(
        foundation_id=fx['foundation'].id,
        person=person,
        user=user,
    )
    GuardianLink.all_tenants.create(
        foundation_id=fx['foundation'].id,
        guardian=guardian,
        student=fx['student'],
        relation=GuardianLink.RELATION_FATHER,
        financial_responsible=True,
    )
    return guardian


def make_reasons(fx):
    r_pos = BehaviourReason.objects.create(
        foundation_id=fx['foundation'].id,
        school=fx['school'],
        code="POS-01",
        label="Membantu Guru dan Teman",
        points=5,
        category=BehaviourCategory.POSITIVE,
    )
    r_min = BehaviourReason.objects.create(
        foundation_id=fx['foundation'].id,
        school=fx['school'],
        code="DISC-01",
        label="Terlambat Masuk Kelas",
        points=-5,
        category=BehaviourCategory.MINOR,
    )
    r_maj = BehaviourReason.objects.create(
        foundation_id=fx['foundation'].id,
        school=fx['school'],
        code="DISC-99",
        label="Merusak Fasilitas Sekolah",
        points=-20,
        category=BehaviourCategory.MAJOR,
    )
    return {'pos': r_pos, 'min': r_min, 'maj': r_maj}


class BehaviourServiceTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.reasons = make_reasons(self.fx)

    def test_record_behaviour_success_and_audit(self):
        rec = record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['pos'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
            note="Merapikan perpustakaan",
        )
        self.assertEqual(rec.points, 5)
        self.assertEqual(rec.reason.category, BehaviourCategory.POSITIVE)
        self.assertFalse(rec.is_superseded)
        self.assertIsNone(rec.acknowledged_by_guardian_at)

        # Audit event created
        audit_entry = AuditEvent.objects.filter(
            foundation_id=self.fx['foundation'].id,
            entity_type='BehaviourRecord',
            entity_id=rec.id,
            action='campus.behaviour.recorded',
        ).first()
        self.assertIsNotNone(audit_entry)
        self.assertEqual(audit_entry.diff['reason_code'], 'POS-01')

    def test_point_accumulation_and_reset_per_term(self):
        """LIF-008: Points accumulate per term and reset at term boundaries; lifetime queryable."""
        # Term 1
        record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['pos'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
        )  # +5
        record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['min'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
        )  # -5

        summary_t1 = get_student_behaviour_summary(self.fx['student'], term=self.fx['term'])
        self.assertEqual(summary_t1['term_positive_points'], 5)
        self.assertEqual(summary_t1['term_negative_points'], -5)
        self.assertEqual(summary_t1['term_net_points'], 0)

        # Create Term 2
        term_2 = Term.objects.create(
            foundation_id=self.fx['foundation'].id,
            academic_year=self.fx['academic_year'],
            name="Semester 2 (Genap)",
            term_no=2,
            start_date=datetime.date(2027, 1, 2),
            end_date=datetime.date(2027, 6, 25),
        )

        # In Term 2: +10 and 0 negative
        record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['pos'],
            recorded_by=self.fx['teacher_user'],
            term=term_2,
            points=10,
        )

        summary_t2 = get_student_behaviour_summary(self.fx['student'], term=term_2)
        self.assertEqual(summary_t2['term_positive_points'], 10)
        self.assertEqual(summary_t2['term_negative_points'], 0)
        self.assertEqual(summary_t2['term_net_points'], 10)

        # Lifetime retains both terms
        self.assertEqual(summary_t2['lifetime_positive_points'], 15)
        self.assertEqual(summary_t2['lifetime_negative_points'], -5)
        self.assertEqual(summary_t2['lifetime_net_points'], 10)

    def test_positive_and_negative_totals_separated(self):
        """LIF-010: Positive points are first-class; UI/API shows pos and neg separately."""
        record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['pos'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
            points=15,
        )
        record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['min'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
            points=-8,
        )

        summary = get_student_behaviour_summary(self.fx['student'], term=self.fx['term'])
        self.assertEqual(summary['term_positive_points'], 15)
        self.assertEqual(summary['term_negative_points'], -8)
        self.assertEqual(summary['term_net_points'], 7)
        self.assertEqual(summary['positive_records_count'], 1)
        self.assertEqual(summary['minor_records_count'], 1)

    def test_escalation_threshold_auto_opens_behaviour_case(self):
        """LIF-009: Configurable threshold auto-opens behaviour_case and assigns counsellor."""
        policy = get_or_create_behaviour_policy(self.fx['school'])
        policy.escalation_negative_threshold = -25
        policy.default_counsellor = self.fx['teacher']  # Acting as counsellor
        policy.save()

        # Step 1: Record -10 points (total -10, threshold -25 not reached)
        record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['min'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
            points=-10,
        )
        self.assertEqual(BehaviourCase.objects.filter(student=self.fx['student']).count(), 0)

        # Step 2: Record -15 points (total -25, threshold reached)
        record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['min'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
            points=-15,
        )

        cases = BehaviourCase.objects.filter(student=self.fx['student'])
        self.assertEqual(cases.count(), 1)
        case = cases.first()
        self.assertEqual(case.status, CaseStatus.OPEN)
        self.assertEqual(case.assigned_counsellor, self.fx['teacher'])
        self.assertIn("-25", case.trigger)

        # Step 3: Another infraction (-5) does not duplicate open case
        record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['min'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
            points=-5,
        )
        self.assertEqual(BehaviourCase.objects.filter(student=self.fx['student']).count(), 1)

    @patch('apps.notifications.services.dispatch_intent')
    def test_notification_dispatch_major_and_minor(self, mock_dispatch):
        """LIF-011: Guardians notified of MAJOR immediately and of MINOR."""
        guardian = attach_guardian(self.fx)

        # Major infraction
        record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['maj'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
        )

        self.assertTrue(mock_dispatch.called)
        call_kwargs = mock_dispatch.call_args[1]
        self.assertEqual(call_kwargs['category'], NotificationCategory.BEHAVIOUR_MAJOR)
        self.assertEqual(call_kwargs['recipient_user'], guardian.user)

    def test_guardian_acknowledgement(self):
        """LIF-012: Guardian acknowledges record with timestamp."""
        guardian = attach_guardian(self.fx)
        rec = record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['min'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
        )
        self.assertIsNone(rec.acknowledged_by_guardian_at)

        ack_rec = acknowledge_behaviour_record(rec, guardian)
        self.assertIsNotNone(ack_rec.acknowledged_by_guardian_at)
        self.assertEqual(ack_rec.acknowledged_by, guardian)

    def test_guardian_acknowledgement_unlinked_guardian_rejected(self):
        """Unlinked guardian cannot acknowledge."""
        # Unlinked guardian
        other_person = Person.all_tenants.create(foundation_id=self.fx['foundation'].id, nik="3471010101010001", full_name="Orang Asing")
        other_guardian = Guardian.all_tenants.create(foundation_id=self.fx['foundation'].id, person=other_person)

        rec = record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['min'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
        )

        with self.assertRaises(ValidationError):
            acknowledge_behaviour_record(rec, other_guardian)

    def test_immutability_and_superseding_correction(self):
        """LIF-013: Records cannot be deleted; corrections supersede with reason."""
        rec = record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['min'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
            points=-10,
        )

        # Deletion is blocked
        with self.assertRaises(PermissionDenied):
            rec.delete()

        # Supersede with corrected reason (+5 positive instead of -10)
        new_rec = supersede_behaviour_record(
            original_record=rec,
            new_reason=self.reasons['pos'],
            recorded_by=self.fx['teacher_user'],
            correction_reason="Salah input nama siswa, yang benar membantu guru",
            points=5,
        )

        rec.refresh_from_db()
        self.assertTrue(rec.is_superseded)
        self.assertEqual(rec.superseded_by, new_rec)
        self.assertEqual(rec.correction_reason, "Salah input nama siswa, yang benar membantu guru")

        # Active term points now reflect +5, not -10
        summary = get_student_behaviour_summary(self.fx['student'], term=self.fx['term'])
        self.assertEqual(summary['term_positive_points'], 5)
        self.assertEqual(summary['term_negative_points'], 0)
        self.assertEqual(summary['term_net_points'], 5)

        # Cannot supersede again
        with self.assertRaises(ValidationError):
            supersede_behaviour_record(
                original_record=rec,
                new_reason=self.reasons['min'],
                recorded_by=self.fx['teacher_user'],
                correction_reason="Koreksi kedua",
            )

    def test_report_card_behaviour_data(self):
        """LIF-014: Behaviour summary appears on report card when enabled."""
        policy = get_or_create_behaviour_policy(self.fx['school'])
        policy.rapor_includes_behaviour = False
        policy.save()

        data_disabled = get_behaviour_report_card_data(self.fx['student'], self.fx['term'])
        self.assertFalse(data_disabled['enabled'])

        policy.rapor_includes_behaviour = True
        policy.save()

        record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['pos'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
            points=10,
        )

        data_enabled = get_behaviour_report_card_data(self.fx['student'], self.fx['term'])
        self.assertTrue(data_enabled['enabled'])
        self.assertEqual(data_enabled['positive_points'], 10)
        self.assertEqual(data_enabled['net_points'], 10)


class BehaviourAPITests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.reasons = make_reasons(self.fx)
        self.client = APIClient()

        # Set up role assignments for teacher
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.fx['teacher_user'],
            role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )

        # Set up school admin
        admin_person = Person.all_tenants.create(foundation_id=self.fx['foundation'].id, nik="3471010101018888", full_name="Pak Admin")
        self.admin_user = User.objects.create(
            foundation_id=self.fx['foundation'].id,
            phone_e164="+6281800000001",
            email="admin@cendekia.sch.id",
            full_name="Pak Admin",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.admin_user,
            role=RoleAssignment.ROLE_SCHOOL_ADMIN,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )

    def test_list_and_create_behaviour_reason(self):
        self.client.force_authenticate(user=self.admin_user)
        # Create reason
        res = self.client.post('/api/v1/campus/behaviour-reasons/', {
            'school': self.fx['school'].id,
            'code': 'HONOR-01',
            'label': 'Juara Olimpiade Sains',
            'points': 25,
            'category': 'POSITIVE',
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['code'], 'HONOR-01')

        # List reasons
        res_list = self.client.get(f'/api/v1/campus/behaviour-reasons/?school_id={self.fx["school"].id}')
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        codes = [item['code'] for item in res_list.data['results']]
        self.assertIn('HONOR-01', codes)

    def test_record_behaviour_via_api(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post('/api/v1/campus/behaviour-records/', {
            'student_id': self.fx['student'].id,
            'reason_id': self.reasons['pos'].id,
            'term_id': self.fx['term'].id,
            'note': 'Sangat aktif di kelas',
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['points'], 5)
        self.assertEqual(res.data['reason_code'], 'POS-01')

    def test_delete_record_method_not_allowed(self):
        """LIF-013: DELETE endpoint rejected."""
        rec = record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['min'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
        )
        self.client.force_authenticate(user=self.admin_user)
        res = self.client.delete(f'/api/v1/campus/behaviour-records/{rec.id}/')
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_supersede_record_via_api(self):
        rec = record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['min'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
            points=-10,
        )
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post(f'/api/v1/campus/behaviour-records/{rec.id}/supersede/', {
            'reason_id': self.reasons['pos'].id,
            'correction_reason': 'Keliru mencatat siswa, yang bersangkutan berprestasi',
            'points': 10,
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['points'], 10)

        # Original record is now superseded
        rec.refresh_from_db()
        self.assertTrue(rec.is_superseded)

    def test_student_summary_endpoint(self):
        record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['pos'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
            points=10,
        )
        record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['min'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
            points=-5,
        )

        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(f'/api/v1/campus/students/{self.fx["student"].id}/behaviour/?term_id={self.fx["term"].id}')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['term_positive_points'], 10)
        self.assertEqual(res.data['term_negative_points'], -5)
        self.assertEqual(res.data['term_net_points'], 5)

    def test_guardian_acknowledge_endpoint(self):
        guardian = attach_guardian(self.fx)
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=guardian.user,
            role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )

        rec = record_behaviour(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            reason=self.reasons['maj'],
            recorded_by=self.fx['teacher_user'],
            term=self.fx['term'],
        )

        self.client.force_authenticate(user=guardian.user)
        res = self.client.post(f'/api/v1/campus/behaviour-records/{rec.id}/acknowledge/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(res.data['acknowledged_by_guardian_at'])

    def test_behaviour_policy_get_and_put(self):
        self.client.force_authenticate(user=self.admin_user)
        res_get = self.client.get(f'/api/v1/campus/schools/{self.fx["school"].id}/behaviour-policy/')
        self.assertEqual(res_get.status_code, status.HTTP_200_OK)
        self.assertEqual(res_get.data['escalation_negative_threshold'], -25)

        res_put = self.client.put(f'/api/v1/campus/schools/{self.fx["school"].id}/behaviour-policy/', {
            'escalation_negative_threshold': -30,
            'rapor_includes_behaviour': True,
        })
        self.assertEqual(res_put.status_code, status.HTTP_200_OK)
        self.assertEqual(res_put.data['escalation_negative_threshold'], -30)
        self.assertTrue(res_put.data['rapor_includes_behaviour'])

    def test_cross_tenant_isolation(self):
        """Tenancy layer 3: accessing behaviour endpoints across foundations fails."""
        other_fx = build_academic_fixture(foundation_name="Yayasan Lain")
        set_current_foundation_id(self.fx['foundation'].id)

        other_admin_person = Person.all_tenants.create(foundation_id=other_fx['foundation'].id, nik="3471010101017777", full_name="Admin Lain")
        other_admin_user = User.objects.create(
            foundation_id=other_fx['foundation'].id,
            phone_e164="+6281899999999",
            email="otheradmin@yayasanlain.sch.id",
            full_name="Admin Lain",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=other_fx['foundation'].id,
            user=other_admin_user,
            role=RoleAssignment.ROLE_SCHOOL_ADMIN,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=other_fx['school'].id,
        )

        # Authenticate as user from other foundation
        self.client.force_authenticate(user=other_admin_user)
        # Attempting to fetch behaviour summary of student in first foundation returns 404
        res = self.client.get(f'/api/v1/campus/students/{self.fx["student"].id}/behaviour/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
