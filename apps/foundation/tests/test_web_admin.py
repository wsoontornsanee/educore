import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.identity.models import Foundation, RoleAssignment, School, User


def _make_user(foundation, phone, name, role, scope_type, scope_id):
    user = User.objects.create(phone_e164=phone, full_name=name, foundation_id=foundation.id)
    RoleAssignment.objects.create(
        foundation_id=foundation.id, user=user, role=role, scope_type=scope_type, scope_id=scope_id,
    )
    return user


class _AdminConsoleFixture(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='Yayasan A', brand_name='A')
        self.school_a = School.objects.create(foundation_id=self.foundation.id, name='SMA A', npsn='11111111', level=School.LEVEL_SMA)
        self.school_b = School.objects.create(foundation_id=self.foundation.id, name='SMA B', npsn='22222222', level=School.LEVEL_SMA)
        self.admin = _make_user(
            self.foundation, '+6281300002001', 'Chair', RoleAssignment.ROLE_FOUNDATION_ADMIN,
            RoleAssignment.SCOPE_FOUNDATION, self.foundation.id,
        )
        self.school_admin = _make_user(
            self.foundation, '+6281300002002', 'Head A', RoleAssignment.ROLE_SCHOOL_ADMIN,
            RoleAssignment.SCOPE_SCHOOL, self.school_a.id,
        )
        self.teacher = _make_user(
            self.foundation, '+6281300002003', 'Teacher', RoleAssignment.ROLE_TEACHER,
            RoleAssignment.SCOPE_SCHOOL, self.school_a.id,
        )


class AuditLogViewTests(_AdminConsoleFixture):
    def setUp(self):
        super().setUp()
        self.url = reverse('admin-audit')
        self.e_school_a = AuditEvent.objects.create(
            foundation_id=self.foundation.id, school_id=self.school_a.id, actor_id=str(self.admin.id),
            action='finance.invoice.issue', entity_type='Invoice', entity_id='1', diff={'status': 'ISSUED'},
        )
        self.e_school_b = AuditEvent.objects.create(
            foundation_id=self.foundation.id, school_id=self.school_b.id, actor_id='system',
            action='academic.grade.update', entity_type='Grade', entity_id='2',
        )
        self.e_foundation = AuditEvent.objects.create(
            foundation_id=self.foundation.id, school_id=None, actor_id=str(self.admin.id),
            action='integration.key.issued', entity_type='PartnerApiKey', entity_id='ak_1',
        )
        other = Foundation.objects.create(legal_name='B', brand_name='B')
        self.e_other = AuditEvent.objects.create(
            foundation_id=other.id, action='finance.invoice.issue', entity_type='Invoice', entity_id='9',
        )

    def _ids(self, response):
        return {event.id for event in response.context['events']}

    def test_anonymous_redirected_to_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_teacher_without_audit_log_read_redirected_home(self):
        self.client.force_login(self.teacher)
        self.assertRedirects(self.client.get(self.url), reverse('web-console-home'), fetch_redirect_response=False)

    def test_foundation_admin_sees_own_foundation_only_with_actor_names(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {self.e_school_a.id, self.e_school_b.id, self.e_foundation.id})
        by_id = {event.id: event for event in response.context['events']}
        self.assertEqual(by_id[self.e_school_a.id].actor_name, 'Chair')
        self.assertEqual(by_id[self.e_school_b.id].actor_name, 'system')
        self.assertIn('ISSUED', by_id[self.e_school_a.id].diff_text)

    def test_school_scoped_viewer_sees_only_own_school_events(self):
        self.client.force_login(self.school_admin)
        response = self.client.get(self.url)
        self.assertEqual(self._ids(response), {self.e_school_a.id})

    def test_school_scoped_viewer_cannot_widen_via_school_filter(self):
        self.client.force_login(self.school_admin)
        response = self.client.get(self.url, {'school': self.school_b.id})
        self.assertEqual(self._ids(response), set())

    def test_module_and_action_filters(self):
        self.client.force_login(self.admin)
        self.assertEqual(self._ids(self.client.get(self.url, {'module': 'finance'})), {self.e_school_a.id})
        self.assertEqual(
            self._ids(self.client.get(self.url, {'action': 'academic.grade.update'})), {self.e_school_b.id},
        )

    def test_date_filter_and_invalid_date_flagged(self):
        AuditEvent.objects.filter(id=self.e_school_b.id).update(
            timestamp=timezone.now() - datetime.timedelta(days=30),
        )
        self.client.force_login(self.admin)
        today = timezone.localdate().isoformat()
        response = self.client.get(self.url, {'from': today})
        self.assertNotIn(self.e_school_b.id, self._ids(response))
        response = self.client.get(self.url, {'from': 'not-a-date'})
        self.assertEqual(response.context['invalid_dates'], ['from'])
        self.assertEqual(len(self._ids(response)), 3)

    def test_paginates_at_fifty(self):
        AuditEvent.objects.bulk_create([
            AuditEvent(foundation_id=self.foundation.id, action='x.y', entity_type='T', entity_id=str(i))
            for i in range(60)
        ])
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(len(response.context['events']), 50)
        self.assertEqual(response.context['page_obj'].paginator.num_pages, 2)


class SettingsViewTests(_AdminConsoleFixture):
    def setUp(self):
        super().setUp()
        self.url = reverse('admin-settings')
        self.foundation_url = reverse('admin-settings-foundation')

    def _foundation_payload(self, **overrides):
        payload = {
            'legal_name': 'Yayasan A Baru', 'brand_name': 'A', 'npwp': '', 'address': '',
            'timezone': 'Asia/Jakarta', 'reporting_currency': 'IDR', 'approval_threshold': '2500000.00',
        }
        payload.update(overrides)
        return payload

    def test_teacher_redirected_home(self):
        self.client.force_login(self.teacher)
        self.assertRedirects(self.client.get(self.url), reverse('web-console-home'), fetch_redirect_response=False)

    def test_foundation_admin_sees_foundation_form_and_all_schools(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context['foundation_form'])
        self.assertEqual([s.name for s in response.context['schools']], ['SMA A', 'SMA B'])

    def test_school_admin_sees_no_foundation_form_and_only_own_school(self):
        self.client.force_login(self.school_admin)
        response = self.client.get(self.url)
        self.assertIsNone(response.context['foundation_form'])
        self.assertEqual([s.name for s in response.context['schools']], ['SMA A'])

    def test_foundation_update_saves_and_audits_diff(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.foundation_url, self._foundation_payload())
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.foundation.refresh_from_db()
        self.assertEqual(self.foundation.legal_name, 'Yayasan A Baru')
        event = AuditEvent.objects.get(action='foundation.settings.updated')
        self.assertEqual(event.foundation_id, self.foundation.id)
        self.assertEqual(event.actor_id, str(self.admin.id))
        self.assertEqual(event.diff['legal_name'], {'before': 'Yayasan A', 'after': 'Yayasan A Baru'})
        self.assertEqual(event.diff['approval_threshold']['after'], '2500000.00')

    def test_foundation_update_no_change_writes_no_audit(self):
        self.client.force_login(self.admin)
        self.client.post(self.foundation_url, self._foundation_payload(legal_name='Yayasan A', approval_threshold='1000000.00'))
        self.assertFalse(AuditEvent.objects.filter(action='foundation.settings.updated').exists())

    def test_foundation_update_invalid_rerenders_with_errors_and_saves_nothing(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.foundation_url, self._foundation_payload(reporting_currency='rp', approval_threshold='-5'))
        self.assertEqual(response.status_code, 400)
        form = response.context['foundation_form']
        self.assertIn('reporting_currency', form.errors)
        self.assertIn('approval_threshold', form.errors)
        self.foundation.refresh_from_db()
        self.assertEqual(self.foundation.legal_name, 'Yayasan A')
        self.assertFalse(AuditEvent.objects.filter(action='foundation.settings.updated').exists())

    def test_school_admin_cannot_update_foundation(self):
        self.client.force_login(self.school_admin)
        response = self.client.post(self.foundation_url, self._foundation_payload())
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.foundation.refresh_from_db()
        self.assertEqual(self.foundation.legal_name, 'Yayasan A')

    def _school_payload(self, school, **overrides):
        payload = {
            'name': school.name, 'level': school.level, 'curriculum': school.curriculum,
            'timezone': 'Asia/Makassar', 'base_currency': 'IDR', 'ownership_status': '',
            'accreditation': '', 'nss': '', 'nsm': '', 'establishment_date': '', 'street_address': '',
            'kelurahan': '', 'kecamatan': '', 'kabupaten_kota': '', 'provinsi': '', 'postal_code': '',
        }
        payload.update(overrides)
        return payload

    def test_school_edit_page_renders_for_school_admin(self):
        self.client.force_login(self.school_admin)
        response = self.client.get(reverse('admin-settings-school', args=[self.school_a.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SMA A')

    def test_school_update_saves_audits_and_leaves_npsn_alone(self):
        self.client.force_login(self.school_admin)
        url = reverse('admin-settings-school', args=[self.school_a.id])
        payload = self._school_payload(self.school_a, name='SMA A Unggul', npsn='99999999')
        response = self.client.post(url, payload)
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        school = School.all_tenants.get(id=self.school_a.id)
        self.assertEqual(school.name, 'SMA A Unggul')
        self.assertEqual(school.timezone, 'Asia/Makassar')
        self.assertEqual(school.npsn, '11111111')
        self.assertEqual(school.updated_by, str(self.school_admin.id))
        event = AuditEvent.objects.get(action='identity.school.updated')
        self.assertEqual(event.school_id, self.school_a.id)
        self.assertEqual(event.diff['name'], {'before': 'SMA A', 'after': 'SMA A Unggul'})

    def test_school_admin_cannot_open_or_edit_another_school(self):
        self.client.force_login(self.school_admin)
        url = reverse('admin-settings-school', args=[self.school_b.id])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, self._school_payload(self.school_b, name='Hacked')).status_code, 404)
        self.assertEqual(School.all_tenants.get(id=self.school_b.id).name, 'SMA B')

    def test_other_foundation_school_is_404_even_for_foundation_admin(self):
        other = Foundation.objects.create(legal_name='B', brand_name='B')
        foreign = School.objects.create(foundation_id=other.id, name='Foreign', npsn='44444444', level=School.LEVEL_SMA)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('admin-settings-school', args=[foreign.id])).status_code, 404)

    def test_school_update_invalid_currency_rerenders(self):
        self.client.force_login(self.school_admin)
        url = reverse('admin-settings-school', args=[self.school_a.id])
        response = self.client.post(url, self._school_payload(self.school_a, base_currency='idr'))
        self.assertEqual(response.status_code, 400)
        self.assertIn('base_currency', response.context['form'].errors)


class AdministrasiEnglishTranslationTests(_AdminConsoleFixture):
    """id-ID is the source language; every Administrasi page must render its
    chrome, form labels and nav entries in English when the site language is
    English (django_language cookie, same mechanism as the language switcher)."""

    def setUp(self):
        super().setUp()
        from django.conf import settings
        from django.utils import translation
        # LocaleMiddleware leaves the request's language active on the thread;
        # reset it so later test modules render in the default language.
        self.addCleanup(translation.deactivate)
        self.client.force_login(self.admin)
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'en'

    def test_pages_render_in_english(self):
        expectations = {
            'admin-audit': 'A permanent record of every data change',
            'admin-settings': 'Foundation profile',
            'admin-staff': 'Staff directory and the access roles',
            'admin-partners': 'Create a new key',
        }
        for url_name, text in expectations.items():
            response = self.client.get(reverse(url_name))
            self.assertContains(response, text, msg_prefix=url_name)

    def test_form_labels_and_nav_render_in_english(self):
        response = self.client.get(reverse('admin-settings'))
        self.assertContains(response, 'Reporting currency')
        self.assertContains(response, 'Audit trail')  # nav label for admin-audit
        self.assertNotContains(response, 'Mata uang pelaporan')
