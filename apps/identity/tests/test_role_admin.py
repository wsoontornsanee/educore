"""Granting/revoking staff roles: every privilege-escalation path."""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.identity.models import Foundation, Person, RoleAssignment, School, Staff, User
from apps.identity.role_admin import RoleChangeError, grant_role, revoke_role_assignment

F, S = RoleAssignment.SCOPE_FOUNDATION, RoleAssignment.SCOPE_SCHOOL


class _Base(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        fid = self.foundation.id
        self.school_a = School.objects.create(foundation_id=fid, name='A', npsn='11111111', level=School.LEVEL_SMA)
        self.school_b = School.objects.create(foundation_id=fid, name='B', npsn='22222222', level=School.LEVEL_SMA)
        self._n = 0
        self.fadmin = self.make_user('Chair', RoleAssignment.ROLE_FOUNDATION_ADMIN, F, fid, None)
        self.fadmin2 = self.make_user('Chair 2', RoleAssignment.ROLE_FOUNDATION_ADMIN, F, fid, None)
        self.head_a = self.make_user('Head A', RoleAssignment.ROLE_SCHOOL_ADMIN, S, self.school_a.id, self.school_a)
        self.teacher_a = self.make_user('Teacher A', RoleAssignment.ROLE_TEACHER, S, self.school_a.id, self.school_a)
        self.teacher_b = self.make_user('Teacher B', RoleAssignment.ROLE_TEACHER, S, self.school_b.id, self.school_b)

    def make_user(self, name, role, scope_type, scope_id, school, with_staff=True):
        self._n += 1
        user = User.objects.create(phone_e164=f'+62813000070{self._n:02d}', full_name=name, foundation_id=self.foundation.id)
        if role:
            RoleAssignment.objects.create(
                foundation_id=self.foundation.id, user=user, role=role, scope_type=scope_type, scope_id=scope_id,
            )
        if with_staff:
            person = Person.objects.create(foundation_id=self.foundation.id, full_name=name)
            Staff.objects.create(
                foundation_id=self.foundation.id, person=person, user=user, school=school, join_date=timezone.localdate(),
            )
        return user

    def grant(self, actor, target, role, scope_type, scope_id):
        return grant_role(
            foundation_id=self.foundation.id, actor=actor, target_user_id=target.id,
            role=role, scope_type=scope_type, scope_id=scope_id,
        )

    def active(self, user, role, scope_type, scope_id):
        return RoleAssignment.all_tenants.filter(
            foundation_id=self.foundation.id, user=user, role=role, scope_type=scope_type,
            scope_id=scope_id, deleted_at__isnull=True,
        ).exists()


class GrantRoleTests(_Base):
    def test_school_admin_grants_teacher_in_own_school_and_it_is_audited(self):
        assignment, created = self.grant(self.head_a, self.teacher_a, RoleAssignment.ROLE_COUNSELLOR, S, self.school_a.id)
        self.assertTrue(created)
        self.assertTrue(self.active(self.teacher_a, RoleAssignment.ROLE_COUNSELLOR, S, self.school_a.id))
        event = AuditEvent.objects.get(action='identity.role.granted')
        self.assertEqual(event.actor_id, str(self.head_a.id))
        self.assertEqual(event.school_id, self.school_a.id)
        self.assertEqual(event.diff['role'], RoleAssignment.ROLE_COUNSELLOR)

    def test_repeat_grant_is_a_noop_without_a_second_audit(self):
        self.grant(self.head_a, self.teacher_a, RoleAssignment.ROLE_COUNSELLOR, S, self.school_a.id)
        _a, created = self.grant(self.head_a, self.teacher_a, RoleAssignment.ROLE_COUNSELLOR, S, self.school_a.id)
        self.assertFalse(created)
        self.assertEqual(AuditEvent.objects.filter(action='identity.role.granted').count(), 1)

    def test_regrant_after_revoke_reactivates_without_unique_violation(self):
        a, _c = self.grant(self.head_a, self.teacher_a, RoleAssignment.ROLE_COUNSELLOR, S, self.school_a.id)
        revoke_role_assignment(foundation_id=self.foundation.id, actor=self.head_a, assignment_id=a.id)
        self.grant(self.head_a, self.teacher_a, RoleAssignment.ROLE_COUNSELLOR, S, self.school_a.id)
        self.assertTrue(self.active(self.teacher_a, RoleAssignment.ROLE_COUNSELLOR, S, self.school_a.id))

    def test_cannot_edit_own_roles(self):
        with self.assertRaises(RoleChangeError):
            self.grant(self.head_a, self.head_a, RoleAssignment.ROLE_TEACHER, S, self.school_a.id)
        with self.assertRaises(RoleChangeError):
            self.grant(self.fadmin, self.fadmin, RoleAssignment.ROLE_TEACHER, S, self.school_a.id)

    def test_school_admin_cannot_escalate_to_foundation_admin_or_foundation_scope(self):
        for role, scope_type, scope_id in [
            (RoleAssignment.ROLE_FOUNDATION_ADMIN, F, self.foundation.id),
            (RoleAssignment.ROLE_FOUNDATION_ADMIN, S, self.school_a.id),
            (RoleAssignment.ROLE_TEACHER, F, self.foundation.id),
        ]:
            with self.assertRaises(RoleChangeError, msg=(role, scope_type)):
                self.grant(self.head_a, self.teacher_a, role, scope_type, scope_id)

    def test_school_admin_cannot_grant_roles_carrying_permissions_they_lack(self):
        # finance_officer holds finance/payroll write keys school_admin does not have
        with self.assertRaises(RoleChangeError):
            self.grant(self.head_a, self.teacher_a, RoleAssignment.ROLE_FINANCE_OFFICER, S, self.school_a.id)
        self.assertFalse(self.active(self.teacher_a, RoleAssignment.ROLE_FINANCE_OFFICER, S, self.school_a.id))

    def test_school_admin_cannot_grant_in_or_to_another_school(self):
        with self.assertRaises(RoleChangeError):  # scope outside ceiling
            self.grant(self.head_a, self.teacher_a, RoleAssignment.ROLE_COUNSELLOR, S, self.school_b.id)
        with self.assertRaises(RoleChangeError):  # target outside ceiling
            self.grant(self.head_a, self.teacher_b, RoleAssignment.ROLE_COUNSELLOR, S, self.school_a.id)

    def test_school_admin_cannot_touch_foundation_level_staff(self):
        floating = self.make_user('Floating', None, F, self.foundation.id, None)
        with self.assertRaises(RoleChangeError):
            self.grant(self.head_a, floating, RoleAssignment.ROLE_TEACHER, S, self.school_a.id)

    def test_foundation_admin_can_grant_any_grantable_role_at_any_scope(self):
        self.grant(self.fadmin, self.teacher_b, RoleAssignment.ROLE_FINANCE_OFFICER, S, self.school_b.id)
        self.grant(self.fadmin, self.teacher_a, RoleAssignment.ROLE_FOUNDATION_ADMIN, F, self.foundation.id)
        self.assertTrue(self.active(self.teacher_a, RoleAssignment.ROLE_FOUNDATION_ADMIN, F, self.foundation.id))

    def test_parent_role_and_unknown_role_not_grantable(self):
        for role in (RoleAssignment.ROLE_PARENT, 'superhero'):
            with self.assertRaises(RoleChangeError):
                self.grant(self.fadmin, self.teacher_a, role, S, self.school_a.id)

    def test_target_must_be_active_staff_of_this_foundation(self):
        non_staff = self.make_user('Wali', None, F, self.foundation.id, None, with_staff=False)
        with self.assertRaises(RoleChangeError):
            self.grant(self.fadmin, non_staff, RoleAssignment.ROLE_TEACHER, S, self.school_a.id)
        Staff.all_tenants.filter(user=self.teacher_a).update(status=Staff.STATUS_OFFBOARDED)
        with self.assertRaises(RoleChangeError):
            self.grant(self.fadmin, self.teacher_a, RoleAssignment.ROLE_COUNSELLOR, S, self.school_a.id)

    def test_other_foundation_target_and_school_are_refused(self):
        other = Foundation.objects.create(legal_name='O', brand_name='O')
        foreign_school = School.objects.create(foundation_id=other.id, name='X', npsn='33333333', level=School.LEVEL_SMA)
        with self.assertRaises(RoleChangeError):
            self.grant(self.fadmin, self.teacher_a, RoleAssignment.ROLE_TEACHER, S, foreign_school.id)
        foreign_user = User.objects.create(phone_e164='+6281399999999', full_name='Foreign', foundation_id=other.id)
        with self.assertRaises(RoleChangeError):
            self.grant(self.fadmin, foreign_user, RoleAssignment.ROLE_TEACHER, S, self.school_a.id)


class RevokeRoleTests(_Base):
    def _assignment(self, user, role, scope_type, scope_id):
        return RoleAssignment.all_tenants.get(
            foundation_id=self.foundation.id, user=user, role=role, scope_type=scope_type,
            scope_id=scope_id, deleted_at__isnull=True,
        )

    def revoke(self, actor, assignment):
        return revoke_role_assignment(foundation_id=self.foundation.id, actor=actor, assignment_id=assignment.id)

    def test_school_admin_revokes_teacher_role_in_own_school_with_audit(self):
        a = self._assignment(self.teacher_a, RoleAssignment.ROLE_TEACHER, S, self.school_a.id)
        self.revoke(self.head_a, a)
        self.assertFalse(self.active(self.teacher_a, RoleAssignment.ROLE_TEACHER, S, self.school_a.id))
        self.assertTrue(RoleAssignment.all_tenants.with_deleted().filter(id=a.id, deleted_at__isnull=False).exists())  # soft delete
        self.assertEqual(AuditEvent.objects.get(action='identity.role.revoked').diff['role'], RoleAssignment.ROLE_TEACHER)

    def test_school_admin_cannot_revoke_foundation_admin_or_other_school_roles(self):
        fa = self._assignment(self.fadmin, RoleAssignment.ROLE_FOUNDATION_ADMIN, F, self.foundation.id)
        with self.assertRaises(RoleChangeError):
            self.revoke(self.head_a, fa)
        tb = self._assignment(self.teacher_b, RoleAssignment.ROLE_TEACHER, S, self.school_b.id)
        with self.assertRaises(RoleChangeError):
            self.revoke(self.head_a, tb)
        self.assertTrue(self.active(self.teacher_b, RoleAssignment.ROLE_TEACHER, S, self.school_b.id))

    def test_cannot_revoke_own_role(self):
        own = self._assignment(self.head_a, RoleAssignment.ROLE_SCHOOL_ADMIN, S, self.school_a.id)
        with self.assertRaises(RoleChangeError):
            self.revoke(self.head_a, own)

    def test_last_foundation_admin_cannot_be_removed_but_one_of_two_can(self):
        second = self._assignment(self.fadmin2, RoleAssignment.ROLE_FOUNDATION_ADMIN, F, self.foundation.id)
        self.revoke(self.fadmin, second)  # 2 -> 1
        remaining = self._assignment(self.fadmin, RoleAssignment.ROLE_FOUNDATION_ADMIN, F, self.foundation.id)
        # a third party (a fresh foundation admin) tries to remove the last one: refused
        third = self.make_user('Chair 3', RoleAssignment.ROLE_FOUNDATION_ADMIN, F, self.foundation.id, None)
        self.revoke(self.fadmin, self._assignment(third, RoleAssignment.ROLE_FOUNDATION_ADMIN, F, self.foundation.id))
        # now only `fadmin` remains; someone else who is a foundation admin would be needed to try — use superuser path:
        superuser = self.make_user('Root', None, F, self.foundation.id, None)
        User.all_tenants.filter(id=superuser.id).update(is_superuser=True)
        superuser.refresh_from_db()
        with self.assertRaises(RoleChangeError) as ctx:
            self.revoke(superuser, remaining)
        self.assertIn('setidaknya satu administrator yayasan', str(ctx.exception))
        self.assertTrue(self.active(self.fadmin, RoleAssignment.ROLE_FOUNDATION_ADMIN, F, self.foundation.id))

    def test_other_foundation_assignment_is_not_found(self):
        other = Foundation.objects.create(legal_name='O', brand_name='O')
        foreign_user = User.objects.create(phone_e164='+6281399999998', full_name='Foreign', foundation_id=other.id)
        foreign = RoleAssignment.objects.create(
            foundation_id=other.id, user=foreign_user, role=RoleAssignment.ROLE_TEACHER, scope_type=F, scope_id=other.id,
        )
        with self.assertRaises(RoleChangeError):
            self.revoke(self.fadmin, foreign)
        self.assertTrue(RoleAssignment.all_tenants.filter(id=foreign.id, deleted_at__isnull=True).exists())


class RoleWebTests(_Base):
    def _roles_url(self, staff_user):
        staff = Staff.all_tenants.get(user=staff_user)
        return reverse('admin-staff-roles', kwargs={'staff_id': staff.id}), staff

    def test_anonymous_and_read_only_users_bounce(self):
        url, _staff = self._roles_url(self.teacher_a)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.teacher_b)  # no school_config.write
        self.assertRedirects(self.client.get(url), reverse('web-console-home'), fetch_redirect_response=False)

    def test_page_lists_roles_and_offers_only_permitted_scopes(self):
        url, _staff = self._roles_url(self.teacher_a)
        self.client.force_login(self.head_a)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([s[0] for s in response.context['grant_scopes']], [f'school:{self.school_a.id}'])
        self.client.force_login(self.fadmin)
        scopes = [s[0] for s in self.client.get(url).context['grant_scopes']]
        self.assertEqual(scopes[0], 'foundation')
        self.assertIn(f'school:{self.school_b.id}', scopes)

    def test_out_of_ceiling_staff_is_404(self):
        url, _staff = self._roles_url(self.teacher_b)
        self.client.force_login(self.head_a)
        self.assertEqual(self.client.get(url).status_code, 404)
        grant = reverse('admin-staff-role-grant', kwargs={'staff_id': Staff.all_tenants.get(user=self.teacher_b).id})
        self.assertEqual(self.client.post(grant, {'role': 'teacher', 'scope': f'school:{self.school_a.id}'}).status_code, 404)

    def test_grant_and_revoke_through_the_views(self):
        url, staff = self._roles_url(self.teacher_a)
        grant = reverse('admin-staff-role-grant', kwargs={'staff_id': staff.id})
        self.client.force_login(self.head_a)
        response = self.client.post(grant, {'role': 'counsellor', 'scope': f'school:{self.school_a.id}'})
        self.assertRedirects(response, url, fetch_redirect_response=False)
        assignment = RoleAssignment.all_tenants.get(user=self.teacher_a, role='counsellor', deleted_at__isnull=True)
        revoke = reverse('admin-staff-role-revoke', kwargs={'staff_id': staff.id, 'assignment_id': assignment.id})
        self.client.post(revoke)
        self.assertFalse(RoleAssignment.all_tenants.filter(id=assignment.id, deleted_at__isnull=True).exists())

    def test_view_relays_escalation_refusal_as_message_and_changes_nothing(self):
        url, staff = self._roles_url(self.teacher_a)
        grant = reverse('admin-staff-role-grant', kwargs={'staff_id': staff.id})
        self.client.force_login(self.head_a)
        for payload in (
            {'role': 'foundation_admin', 'scope': 'foundation'},
            {'role': 'finance_officer', 'scope': f'school:{self.school_a.id}'},
            {'role': 'teacher', 'scope': 'bogus'},
        ):
            response = self.client.post(grant, payload, follow=True)
            self.assertTrue(any(m.level_tag == 'error' for m in response.context['messages']), payload)
        self.assertFalse(RoleAssignment.all_tenants.filter(user=self.teacher_a, role__in=['foundation_admin', 'finance_officer']).exists())

    def test_revoke_url_cannot_target_another_staffs_assignment(self):
        _url, staff_a = self._roles_url(self.teacher_a)
        b_assignment = RoleAssignment.all_tenants.get(user=self.teacher_b, role='teacher', deleted_at__isnull=True)
        revoke = reverse('admin-staff-role-revoke', kwargs={'staff_id': staff_a.id, 'assignment_id': b_assignment.id})
        self.client.force_login(self.fadmin)
        self.assertEqual(self.client.post(revoke).status_code, 404)
        self.assertTrue(RoleAssignment.all_tenants.filter(id=b_assignment.id, deleted_at__isnull=True).exists())

    def test_own_roles_page_hides_grant_form(self):
        _url, own_staff = self._roles_url(self.head_a)
        self.client.force_login(self.head_a)
        response = self.client.get(reverse('admin-staff-roles', kwargs={'staff_id': own_staff.id}))
        self.assertTrue(response.context['self_edit'])
        self.assertNotContains(response, 'grant-role')

    def test_directory_shows_roles_link_only_where_editable(self):
        self.client.force_login(self.head_a)
        response = self.client.get(reverse('admin-staff'))
        editable = {row.person.full_name: row.can_edit_roles for row in response.context['staff_rows']}
        self.assertEqual(editable, {'Head A': False, 'Teacher A': True})
