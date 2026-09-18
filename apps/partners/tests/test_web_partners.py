from django.test import TestCase
from django.urls import reverse

from apps.core.models import AuditEvent
from apps.identity.models import Foundation, RoleAssignment, School, User
from apps.partners import services
from apps.partners.crypto import decrypt_secret
from apps.partners.models import PartnerApiKey


def _make_user(foundation, phone, name, role, scope_type, scope_id):
    user = User.objects.create(phone_e164=phone, full_name=name, foundation_id=foundation.id)
    RoleAssignment.objects.create(
        foundation_id=foundation.id, user=user, role=role, scope_type=scope_type, scope_id=scope_id,
    )
    return user


class PartnerConsoleTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school = School.objects.create(foundation_id=self.foundation.id, name='S', npsn='12345678', level=School.LEVEL_SMA)
        self.admin = _make_user(
            self.foundation, '+6281300003001', 'Chair', RoleAssignment.ROLE_FOUNDATION_ADMIN,
            RoleAssignment.SCOPE_FOUNDATION, self.foundation.id,
        )
        self.school_admin = _make_user(
            self.foundation, '+6281300003002', 'Head', RoleAssignment.ROLE_SCHOOL_ADMIN,
            RoleAssignment.SCOPE_SCHOOL, self.school.id,
        )
        self.url = reverse('admin-partners')
        self.client.force_login(self.admin)

    def _keys(self):
        return PartnerApiKey.all_tenants.filter(foundation_id=self.foundation.id)

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_school_admin_redirected_home_and_cannot_issue(self):
        self.client.force_login(self.school_admin)
        self.assertRedirects(self.client.get(self.url), reverse('web-console-home'), fetch_redirect_response=False)
        response = self.client.post(self.url, {'label': 'Evil', 'scopes': ['roster.read']})
        self.assertRedirects(response, reverse('web-console-home'), fetch_redirect_response=False)
        self.assertEqual(self._keys().count(), 0)

    def test_listing_shows_only_own_foundation_keys(self):
        services.issue_api_key(self.foundation.id, 'Mine', ['roster.read'])
        other = Foundation.objects.create(legal_name='O', brand_name='O')
        services.issue_api_key(other.id, 'Theirs', ['roster.read'])
        response = self.client.get(self.url)
        self.assertEqual([key.label for key in response.context['keys']], ['Mine'])
        self.assertNotContains(response, 'Theirs')

    def test_issue_shows_secret_once_and_audits(self):
        response = self.client.post(self.url, {
            'label': 'Payroll Vendor', 'scopes': ['roster.read', 'finance.read'],
            'school_ids': [str(self.school.id)],
        })
        self.assertEqual(response.status_code, 200)
        key = self._keys().get()
        secret = response.context['new_secret']
        self.assertEqual(decrypt_secret(key.secret_encrypted), secret)
        self.assertContains(response, secret)
        self.assertEqual(key.scopes, ['roster.read', 'finance.read'])
        self.assertEqual(key.school_ids, [self.school.id])
        event = AuditEvent.objects.get(action='integration.key.issued')
        self.assertEqual(event.entity_id, key.key_id)
        self.assertEqual(event.actor_id, str(self.admin.id))
        self.assertNotIn(secret, str(event.diff))
        # A later GET never shows the secret again.
        self.assertNotContains(self.client.get(self.url), secret)

    def test_issue_validation_errors_create_nothing(self):
        cases = [
            {'label': '', 'scopes': ['roster.read']},
            {'label': 'X', 'scopes': []},
            {'label': 'X', 'scopes': ['bogus.scope']},
            {'label': 'X', 'scopes': ['payroll.write']},
            {'label': 'X', 'scopes': ['roster.read'], 'ip_allowlist': 'not-an-ip'},
            {'label': 'X', 'scopes': ['roster.read'], 'school_ids': ['99999']},
        ]
        for payload in cases:
            response = self.client.post(self.url, payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertTrue(response.context['form_error'], payload)
            self.assertIsNone(response.context.get('new_secret'), payload)
        self.assertEqual(self._keys().count(), 0)

    def test_payroll_write_accepted_with_ip_allowlist(self):
        response = self.client.post(self.url, {
            'label': 'Payroll', 'scopes': ['payroll.write'], 'ip_allowlist': '203.0.113.0/24\n198.51.100.7',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._keys().get().ip_allowlist, ['203.0.113.0/24', '198.51.100.7'])

    def test_key_limit_reported_not_500(self):
        services.issue_api_key(self.foundation.id, 'A', ['roster.read'])
        services.issue_api_key(self.foundation.id, 'B', ['roster.read'])
        response = self.client.post(self.url, {'label': 'C', 'scopes': ['roster.read']})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._keys().count(), 2)

    def test_rotate_shows_new_secret_and_starts_countdown(self):
        old, _ = services.issue_api_key(self.foundation.id, 'A', ['roster.read'])
        response = self.client.post(reverse('admin-partner-key-rotate', args=[old.key_id]))
        self.assertEqual(response.status_code, 200)
        new_key = self._keys().exclude(pk=old.pk).get()
        self.assertEqual(response.context['new_key_id'], new_key.key_id)
        self.assertEqual(decrypt_secret(new_key.secret_encrypted), response.context['new_secret'])
        old.refresh_from_db()
        self.assertIsNotNone(old.expires_at)
        self.assertTrue(AuditEvent.objects.filter(action='integration.key.rotated', entity_id=old.key_id).exists())

    def test_already_rotated_key_cannot_rotate_again(self):
        old, _ = services.issue_api_key(self.foundation.id, 'A', ['roster.read'])
        services.rotate_api_key(old)
        response = self.client.post(reverse('admin-partner-key-rotate', args=[old.key_id]))
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(self._keys().count(), 2)

    def test_rotate_at_key_limit_redirects_with_error(self):
        a, _ = services.issue_api_key(self.foundation.id, 'A', ['roster.read'])
        services.issue_api_key(self.foundation.id, 'B', ['roster.read'])
        response = self.client.post(reverse('admin-partner-key-rotate', args=[a.key_id]), follow=True)
        self.assertEqual(self._keys().count(), 2)
        self.assertTrue(any('Batas kunci' in str(m) for m in response.context['messages']))

    def test_revoke_soft_revokes_and_audits(self):
        key, _ = services.issue_api_key(self.foundation.id, 'A', ['roster.read'])
        response = self.client.post(reverse('admin-partner-key-revoke', args=[key.key_id]))
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        key.refresh_from_db()
        self.assertEqual(key.status, PartnerApiKey.STATUS_REVOKED)
        self.assertIsNone(key.deleted_at)
        self.assertTrue(AuditEvent.objects.filter(action='integration.key.revoked', entity_id=key.key_id).exists())

    def test_other_foundation_key_cannot_be_rotated_or_revoked(self):
        other = Foundation.objects.create(legal_name='O', brand_name='O')
        foreign, _ = services.issue_api_key(other.id, 'Theirs', ['roster.read'])
        self.client.post(reverse('admin-partner-key-revoke', args=[foreign.key_id]))
        self.client.post(reverse('admin-partner-key-rotate', args=[foreign.key_id]))
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, PartnerApiKey.STATUS_ACTIVE)
        self.assertIsNone(foreign.expires_at)
        self.assertEqual(PartnerApiKey.all_tenants.filter(foundation_id=other.id).count(), 1)

    def test_get_not_allowed_on_action_endpoints(self):
        key, _ = services.issue_api_key(self.foundation.id, 'A', ['roster.read'])
        self.assertEqual(self.client.get(reverse('admin-partner-key-revoke', args=[key.key_id])).status_code, 405)
        self.assertEqual(self.client.get(reverse('admin-partner-key-rotate', args=[key.key_id])).status_code, 405)


class JsonAdminIssuanceStillAuditedTests(TestCase):
    """issue_api_key_audited now owns the `integration.key.issued` event the
    JSON admin API used to write itself — guard against the API losing it."""

    def test_json_admin_issue_writes_audit_event(self):
        foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        admin = _make_user(
            foundation, '+6281300003010', 'Chair', RoleAssignment.ROLE_FOUNDATION_ADMIN,
            RoleAssignment.SCOPE_FOUNDATION, foundation.id,
        )
        self.client.force_login(admin)
        response = self.client.post(
            '/api/v1/partner-admin/keys', {'label': 'API', 'scopes': ['roster.read']}, content_type='application/json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(AuditEvent.objects.filter(action='integration.key.issued', foundation_id=foundation.id).exists())
