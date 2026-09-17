import datetime
from django.test import TestCase
from rest_framework.test import APIClient

from apps.academic.models import (
    DayOfWeek,
    SubstitutionStatus,
    TimetableSlot,
    TimetableSubstitution,
)
from apps.academic.services import (
    SubstitutionDeclineReasonRequiredError,
    accept_substitution,
    assign_substitution,
    create_timetable_slot,
    decline_substitution,
    get_effective_teacher_for_slot,
)
from apps.academic.tests.base import build_academic_fixture
from apps.attendance.services import get_teacher_agenda
from apps.identity.models import Person, RoleAssignment, Staff, User
from apps.notifications.models import NotificationCategory, NotificationIntent


import uuid


def make_substitute_teacher(fx, phone=None, name='Pak Joko Sub'):
    suffix = uuid.uuid4().hex[:6]
    unique_phone = phone or f'+628199{suffix[:6]}'
    person = Person.all_tenants.create(
        foundation_id=fx['foundation'].id,
        nik=f'3471010101{suffix[:6]}',
        full_name=name,
    )
    user = User.objects.create(
        foundation_id=fx['foundation'].id,
        phone_e164=unique_phone,
        email=f'sub_{suffix}@cendekia.sch.id',
        full_name=name,
    )
    staff = Staff.all_tenants.create(
        foundation_id=fx['foundation'].id,
        person=person,
        user=user,
        employment_type=Staff.TYPE_PERMANENT,
        join_date=datetime.date(2026, 1, 1),
    )
    RoleAssignment.all_tenants.create(
        foundation_id=fx['foundation'].id,
        user=user,
        role='teacher',
        scope_type=RoleAssignment.SCOPE_SCHOOL,
        scope_id=fx['school'].id,
    )
    return staff


class SubstitutionWorkflowServiceTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.monday = datetime.date(2026, 8, 3) # A Monday
        self.slot = create_timetable_slot(
            class_subject=self.fx['class_subject'],
            day_of_week=DayOfWeek.MONDAY,
            period_no=1,
            start_time=datetime.time(7, 0),
            end_time=datetime.time(7, 40),
            room='R1',
        )
        self.substitute = make_substitute_teacher(self.fx)

    def test_assign_substitution_initial_state_pending(self):
        sub = assign_substitution(
            slot=self.slot,
            date=self.monday,
            substitute_teacher=self.substitute,
            reason="Izin Dinas",
        )
        self.assertEqual(sub.status, SubstitutionStatus.PENDING)
        self.assertEqual(sub.decline_reason, "")
        self.assertIsNone(sub.responded_at)

    def test_accept_substitution_service(self):
        sub = assign_substitution(
            slot=self.slot,
            date=self.monday,
            substitute_teacher=self.substitute,
            reason="Izin Dinas",
        )
        accepted_sub = accept_substitution(sub)
        self.assertEqual(accepted_sub.status, SubstitutionStatus.ACCEPTED)
        self.assertIsNotNone(accepted_sub.responded_at)

        # Refresh from DB
        sub.refresh_from_db()
        self.assertEqual(sub.status, SubstitutionStatus.ACCEPTED)

    def test_decline_substitution_service_success_and_notification(self):
        sub = assign_substitution(
            slot=self.slot,
            date=self.monday,
            substitute_teacher=self.substitute,
            reason="Izin Dinas",
        )
        declined_sub = decline_substitution(sub, reason="Sedang mengajar di kelas lain")
        self.assertEqual(declined_sub.status, SubstitutionStatus.DECLINED)
        self.assertEqual(declined_sub.decline_reason, "Sedang mengajar di kelas lain")
        self.assertIsNotNone(declined_sub.responded_at)

        # Verify notification intent dispatched
        intent = NotificationIntent.objects.filter(
            foundation_id=self.fx['foundation'].id,
            category=NotificationCategory.SUBSTITUTE_DECLINED,
            dedupe_key=f"substitution_declined:{sub.id}",
        ).first()
        self.assertIsNotNone(intent)
        self.assertEqual(intent.recipient_user, self.fx['teacher_user'])
        self.assertIn("Sedang mengajar di kelas lain", intent.payload['reason'])

    def test_decline_substitution_requires_non_empty_reason(self):
        sub = assign_substitution(
            slot=self.slot,
            date=self.monday,
            substitute_teacher=self.substitute,
            reason="Izin Dinas",
        )
        with self.assertRaises(SubstitutionDeclineReasonRequiredError):
            decline_substitution(sub, reason="   ")

    def test_effective_teacher_and_agenda_respects_declined_status(self):
        sub = assign_substitution(
            slot=self.slot,
            date=self.monday,
            substitute_teacher=self.substitute,
            reason="Izin Dinas",
        )
        # When PENDING: effective teacher is substitute
        self.assertEqual(get_effective_teacher_for_slot(self.slot, self.monday), self.substitute)
        # Substitute sees it in their agenda
        agenda_sub = get_teacher_agenda(self.substitute, self.monday)
        self.assertEqual(len(agenda_sub), 1)
        self.assertTrue(agenda_sub[0]['is_substitution'])
        self.assertEqual(agenda_sub[0]['substitution_status'], SubstitutionStatus.PENDING)
        # Original teacher does not see it
        agenda_orig = get_teacher_agenda(self.fx['teacher'], self.monday)
        self.assertEqual(len(agenda_orig), 0)

        # When ACCEPTED: effective teacher is still substitute
        accept_substitution(sub)
        self.assertEqual(get_effective_teacher_for_slot(self.slot, self.monday), self.substitute)
        agenda_sub = get_teacher_agenda(self.substitute, self.monday)
        self.assertEqual(agenda_sub[0]['substitution_status'], SubstitutionStatus.ACCEPTED)

        # When DECLINED: effective teacher falls back to original teacher!
        decline_substitution(sub, reason="Berhalangan hadir")
        self.assertEqual(get_effective_teacher_for_slot(self.slot, self.monday), self.fx['teacher'])
        # Original teacher sees the slot again!
        agenda_orig = get_teacher_agenda(self.fx['teacher'], self.monday)
        self.assertEqual(len(agenda_orig), 1)
        self.assertFalse(agenda_orig[0]['is_substitution'])
        # Substitute teacher no longer sees it in agenda
        agenda_sub = get_teacher_agenda(self.substitute, self.monday)
        self.assertEqual(len(agenda_sub), 0)


class SubstitutionWorkflowApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        self.monday = datetime.date(2026, 8, 3)
        self.slot = create_timetable_slot(
            class_subject=self.fx['class_subject'],
            day_of_week=DayOfWeek.MONDAY,
            period_no=1,
            start_time=datetime.time(7, 0),
            end_time=datetime.time(7, 40),
            room='R1',
        )
        self.substitute = make_substitute_teacher(self.fx)
        self.sub = assign_substitution(
            slot=self.slot,
            date=self.monday,
            substitute_teacher=self.substitute,
            reason="Dinas Luar",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.fx['teacher_user'],
            role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )

    def test_substitute_can_accept_via_api(self):
        self.client.force_authenticate(user=self.substitute.user)
        res = self.client.post(
            f'/api/v1/academic/timetable/substitutions/{self.sub.id}/accept/',
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertEqual(data['status'], SubstitutionStatus.ACCEPTED)
        self.assertIsNotNone(data['responded_at'])

    def test_substitute_can_decline_via_api(self):
        self.client.force_authenticate(user=self.substitute.user)
        res = self.client.post(
            f'/api/v1/academic/timetable/substitutions/{self.sub.id}/decline/',
            {'reason': 'Ada rapat dinas bersamaan'},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertEqual(data['status'], SubstitutionStatus.DECLINED)
        self.assertEqual(data['decline_reason'], 'Ada rapat dinas bersamaan')

    def test_decline_via_api_requires_reason(self):
        self.client.force_authenticate(user=self.substitute.user)
        res = self.client.post(
            f'/api/v1/academic/timetable/substitutions/{self.sub.id}/decline/',
            {'reason': ''},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('Alasan penolakan wajib diisi', res.json().get('error', ''))

    def test_unauthorized_teacher_cannot_accept_or_decline(self):
        third_teacher = make_substitute_teacher(self.fx, phone='+628177777777', name='Guru Lain')
        self.client.force_authenticate(user=third_teacher.user)

        res_accept = self.client.post(
            f'/api/v1/academic/timetable/substitutions/{self.sub.id}/accept/',
            format='json',
        )
        self.assertEqual(res_accept.status_code, 403)

        res_decline = self.client.post(
            f'/api/v1/academic/timetable/substitutions/{self.sub.id}/decline/',
            {'reason': 'Tidak mau'},
            format='json',
        )
        self.assertEqual(res_decline.status_code, 403)

    def test_school_admin_can_accept_or_decline(self):
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.fx['teacher_user'],
            role='school_admin',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post(
            f'/api/v1/academic/timetable/substitutions/{self.sub.id}/accept/',
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['status'], SubstitutionStatus.ACCEPTED)

    def test_cross_tenant_substitution_returns_404(self):
        fx_b = build_academic_fixture(foundation_name="Yayasan Beda")
        sub_b = make_substitute_teacher(fx_b, phone='+628155555555', name='Guru Yayasan Lain')
        self.client.force_authenticate(user=sub_b.user)

        res = self.client.post(
            f'/api/v1/academic/timetable/substitutions/{self.sub.id}/accept/',
            format='json',
        )
        self.assertEqual(res.status_code, 404)

    def test_retrieve_substitution_returns_slot_item_for_substitute(self):
        self.client.force_authenticate(user=self.substitute.user)
        res = self.client.get(f'/api/v1/academic/timetable/substitutions/{self.sub.id}/')
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertEqual(data['id'], self.sub.id)
        slot_item = data['slot_item']
        self.assertIsNotNone(slot_item)
        self.assertEqual(slot_item['id'], self.slot.id)
        self.assertEqual(slot_item['period_no'], self.slot.period_no)
        self.assertEqual(slot_item['start_time'], '07:00')
        self.assertEqual(slot_item['end_time'], '07:40')
        self.assertEqual(slot_item['class_group_name'], self.fx['class_group'].name)
        self.assertEqual(slot_item['subject_name'], self.fx['class_subject'].subject.name)
        self.assertTrue(slot_item['is_substitution'])
        self.assertEqual(slot_item['substitution_id'], self.sub.id)
        self.assertEqual(slot_item['substitution_status'], SubstitutionStatus.PENDING)
        self.assertEqual(slot_item['original_teacher_name'], self.fx['teacher'].person.full_name)

    def test_retrieve_substitution_allowed_for_original_teacher(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(f'/api/v1/academic/timetable/substitutions/{self.sub.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['slot_item']['substitution_id'], self.sub.id)

    def test_retrieve_substitution_forbidden_for_unrelated_teacher(self):
        third_teacher = make_substitute_teacher(self.fx, phone='+628177777778', name='Guru Lain Sekali')
        self.client.force_authenticate(user=third_teacher.user)
        res = self.client.get(f'/api/v1/academic/timetable/substitutions/{self.sub.id}/')
        self.assertEqual(res.status_code, 403)

    def test_retrieve_substitution_cross_tenant_returns_404(self):
        fx_b = build_academic_fixture(foundation_name="Yayasan Lain Sekali")
        sub_b = make_substitute_teacher(fx_b, phone='+628155555556', name='Guru Yayasan B')
        self.client.force_authenticate(user=sub_b.user)
        res = self.client.get(f'/api/v1/academic/timetable/substitutions/{self.sub.id}/')
        self.assertEqual(res.status_code, 404)

    def test_slot_action_returns_slot_item_directly(self):
        self.client.force_authenticate(user=self.substitute.user)
        res = self.client.get(f'/api/v1/academic/timetable/substitutions/{self.sub.id}/slot/')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['substitution_id'], self.sub.id)
        self.assertEqual(data['class_group_name'], self.fx['class_group'].name)

    def test_retrieve_slot_allowed_for_slot_teacher_and_substitute(self):
        # Substitute can retrieve slot
        self.client.force_authenticate(user=self.substitute.user)
        res = self.client.get(f'/api/v1/academic/timetable/slots/{self.slot.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['id'], self.slot.id)
        self.assertEqual(res.json()['slot_item']['class_group_name'], self.fx['class_group'].name)

        # Original teacher can retrieve slot
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(f'/api/v1/academic/timetable/slots/{self.slot.id}/')
        self.assertEqual(res.status_code, 200)

    def test_retrieve_slot_forbidden_for_unrelated_teacher(self):
        third_teacher = make_substitute_teacher(self.fx, phone='+628177777779', name='Guru Unrelated')
        self.client.force_authenticate(user=third_teacher.user)
        res = self.client.get(f'/api/v1/academic/timetable/slots/{self.slot.id}/')
        self.assertEqual(res.status_code, 403)

    def test_retrieve_slot_cross_tenant_returns_404(self):
        fx_b = build_academic_fixture(foundation_name="Yayasan C")
        sub_b = make_substitute_teacher(fx_b, phone='+628155555557', name='Guru Yayasan C')
        self.client.force_authenticate(user=sub_b.user)
        res = self.client.get(f'/api/v1/academic/timetable/slots/{self.slot.id}/')
        self.assertEqual(res.status_code, 404)
