"""Getting a real person to the ops page and the stale-job alerts (ARC-008): grant, check, banner."""
from io import StringIO

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import AuditEvent
from apps.identity.models import Foundation, PlatformRoleAssignment, User
from apps.status.services import classify_platform_operators


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.foundation = Foundation.objects.create(legal_name='Ops Test', brand_name='Ops Test')

    def user(self, phone='+6281200000301', email='ops@example.com', **extra):
        return User.objects.create(
            phone_e164=phone, full_name='Ops Person', email=email, foundation_id=self.foundation.id, **extra,
        )

    def make_operator(self, **kwargs):
        user = self.user(**kwargs)
        PlatformRoleAssignment.objects.create(user=user, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR)
        return user


class ClassifyOperatorsTests(_Base):
    def test_only_active_operators_with_email_are_reachable(self):
        good = self.make_operator()
        self.make_operator(phone='+6281200000302', email=None)
        self.make_operator(phone='+6281200000303', email='off@example.com', is_active=False)
        self.user(phone='+6281200000304', email='plain@example.com')  # not an operator
        reachable, unreachable = classify_platform_operators()
        self.assertEqual([u.id for u in reachable], [good.id])
        self.assertEqual(len(unreachable), 2)


class GrantPlatformOperatorTests(_Base):
    def run_command(self, *args):
        out = StringIO()
        call_command('grant_platform_operator', *args, stdout=out)
        return out.getvalue()

    def test_grants_by_phone_and_email_idempotently(self):
        user = self.user()
        self.run_command('--phone', user.phone_e164)
        self.assertEqual(PlatformRoleAssignment.objects.filter(user=user).count(), 1)
        self.assertIn('already', self.run_command('--email', 'OPS@example.com'))
        self.assertEqual(PlatformRoleAssignment.objects.filter(user=user).count(), 1)

    def test_grant_writes_a_platform_wide_audit_event(self):
        user = self.user()
        self.run_command('--email', user.email)
        event = AuditEvent.objects.get(action='platform_role.granted')
        self.assertEqual(event.entity_id, str(user.id))
        self.assertIsNone(event.foundation_id)

    def test_granted_user_can_open_the_ops_page(self):
        user = self.user()
        self.run_command('--email', user.email)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('status_manage:page')).status_code, 200)

    def test_user_without_email_is_granted_with_a_warning(self):
        user = self.user(email=None)
        self.assertIn('will not reach', self.run_command('--phone', user.phone_e164))
        self.assertTrue(PlatformRoleAssignment.objects.filter(user=user).exists())

    def test_unknown_user_is_an_error_and_creates_nothing(self):
        with self.assertRaisesMessage(CommandError, 'No such user'):
            self.run_command('--email', 'nobody@example.com')
        self.assertEqual(User.all_tenants.count(), 0)

    def test_revoke_removes_only_that_users_role(self):
        keep = self.make_operator(phone='+6281200000305', email='keep@example.com')
        drop = self.make_operator()
        self.run_command('--email', drop.email, '--revoke')
        self.assertFalse(PlatformRoleAssignment.objects.filter(user=drop).exists())
        self.assertTrue(PlatformRoleAssignment.objects.filter(user=keep).exists())
        self.assertTrue(AuditEvent.objects.filter(action='platform_role.revoked', entity_id=str(drop.id)).exists())


class CheckPlatformOperatorsTests(_Base):
    def run_command(self, *args):
        out = StringIO()
        call_command('check_platform_operators', *args, stdout=out)
        return out.getvalue()

    def test_fails_when_there_is_no_operator(self):
        with self.assertRaisesMessage(CommandError, 'No platform operator can receive'):
            self.run_command()

    def test_fails_when_the_only_operator_has_no_email(self):
        self.make_operator(email=None)
        with self.assertRaises(CommandError):
            self.run_command()

    def test_reports_reachable_and_unreachable_operators(self):
        self.make_operator()
        self.make_operator(phone='+6281200000302', email=None)
        output = self.run_command()
        self.assertIn('can receive alerts', output)
        self.assertIn('cannot', output)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_test_emails_each_reachable_operator_directly(self):
        self.make_operator()
        self.run_command('--send-test')
        self.assertEqual([m.to for m in mail.outbox], [['ops@example.com']])

    @override_settings(EMAIL_BACKEND='apps.status.tests.test_platform_operators.BrokenBackend')
    def test_a_broken_mail_setup_fails_the_test_send(self):
        self.make_operator()
        with self.assertRaises(RuntimeError):
            self.run_command('--send-test')


class BrokenBackend:
    def __init__(self, *args, **kwargs):
        pass

    def send_messages(self, messages):
        raise RuntimeError('smtp down')


class OpsPageBannerTests(_Base):
    def page(self, user):
        self.client.force_login(user)
        return self.client.get(reverse('status_manage:page'))

    def test_healthy_recipients_show_no_banner(self):
        operator = self.make_operator()
        self.assertNotContains(self.page(operator), 'Tidak ada operator yang dapat menerima')
        self.assertNotContains(self.page(operator), 'Operator tanpa email')

    def test_banner_when_no_operator_can_be_alerted(self):
        operator = self.make_operator(email=None)
        self.assertContains(self.page(operator), 'Tidak ada operator yang dapat menerima')

    def test_lists_operators_who_cannot_be_alerted_without_their_contact_details(self):
        self.make_operator()
        blocked = self.make_operator(phone='+6281200000302', email=None)
        blocked.full_name = 'Tanpa Email'
        blocked.save()
        response = self.page(User.all_tenants.get(email='ops@example.com'))
        self.assertContains(response, 'Operator tanpa email')
        self.assertContains(response, 'Tanpa Email')
        self.assertNotContains(response, '+6281200000302')
