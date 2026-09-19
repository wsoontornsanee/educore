"""Guardian copy for the auto top-up notice must be seeded and read as Indonesian, not machine output.

The seeded bodies carry the "Rp" prefix, so the payload holds the grouped figure; a raw
``str(Decimal)`` used to render "Rp 100000.00", and with no seeded template the guardian got no copy.
"""
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from apps.notifications.models import ChannelType, NotificationIntent, NotificationTemplate
from apps.notifications.services import render_template_message
from apps.wallet.models import WalletTopupIntent, WalletTopupMethod
from apps.wallet.services import get_or_create_wallet, process_wallet_auto_topups, set_wallet_auto_topup_config, topup_wallet
from apps.wallet.tests.test_reconciliation import attach_financial_guardian
from apps.wallet.tests.test_wallet_core import build_wallet_fixture

TEMPLATE_KEY = 'wallet.auto_topup.triggered'
CHANNELS = (ChannelType.WHATSAPP, ChannelType.PUSH)


class AutoTopupNoticeTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.wallet = get_or_create_wallet(self.fx['student'])
        attach_financial_guardian(self.fx)
        call_command('seed_notification_templates', foundation_id=self.fx['foundation'].id, verbosity=0)
        NotificationIntent.all_tenants.all().delete()
        topup_wallet(self.wallet, Decimal('10000'), 'CASH', 'idem-notice')

    def _trigger(self, method):
        set_wallet_auto_topup_config(
            self.wallet, is_active=True, threshold_amount=Decimal('20000'), topup_amount=Decimal('100000'),
            method=method, bank='BCA',
        )
        self.assertEqual(process_wallet_auto_topups()['triggered'], 1)
        return (
            NotificationIntent.all_tenants.filter(template_key=TEMPLATE_KEY).get(),
            WalletTopupIntent.all_tenants.get(wallet=self.wallet),
        )

    def _texts(self, intent):
        out = {}
        for channel in CHANNELS:
            rendered = render_template_message(TEMPLATE_KEY, channel, self.fx['foundation'].id, intent.payload)
            out[channel] = f"{rendered['subject']} {rendered['body']}"
        return out

    def test_template_is_seeded_for_whatsapp_and_push(self):
        keys = set(
            NotificationTemplate.all_tenants.filter(
                foundation_id=self.fx['foundation'].id, key=TEMPLATE_KEY, locale='id-ID',
            ).values_list('channel', flat=True)
        )
        self.assertEqual(keys, set(CHANNELS))

    def test_va_notice_names_amount_student_and_virtual_account(self):
        intent, topup = self._trigger(WalletTopupMethod.VA)
        self.assertEqual(intent.payload['topup_amount'], '100.000')
        for channel, text in self._texts(intent).items():
            with self.subTest(channel=channel):
                self.assertNotIn('{', text)
                self.assertIn('Rp 100.000', text)
                self.assertNotIn('100000.00', text)
                self.assertIn(self.fx['student'].person.full_name, text)
                self.assertIn(f'Virtual Account BCA {topup.va_number}', text)

    def test_qris_notice_has_no_empty_virtual_account(self):
        intent, topup = self._trigger(WalletTopupMethod.QRIS)
        self.assertEqual(topup.va_number, '')
        for channel, text in self._texts(intent).items():
            with self.subTest(channel=channel):
                self.assertNotIn('{', text)
                self.assertIn('Rp 100.000', text)
                self.assertIn('QRIS', text)
                self.assertNotIn('Virtual Account', text)
