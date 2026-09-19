"""Guardian copy for the reconciliation notices must read as Indonesian, not as machine output.

The seeded bodies already carry the "Rp" prefix, so the payload holds the grouped figure; a raw
``str(Decimal)`` used to render "Rp 5000.00" and ISO dates rendered "2026-09-19".
"""
import datetime
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.notifications.models import ChannelType, NotificationIntent
from apps.notifications.services import render_template_message
from apps.wallet.services import (
    _format_notice_amount,
    _format_notice_date,
    get_wallets_due_for_reminder,
    invoice_reconciliation_case,
    queue_reconciliation_notice,
    queue_reconciliation_reminder,
    resend_reconciliation_notice,
)
from apps.wallet.tests.test_pos_checkout import build_pos_fixture
from apps.wallet.tests.test_reconciliation import attach_financial_guardian
from apps.wallet.tests.test_reconciliation_followup import make_open_case
from apps.wallet.tests.test_wallet_core import build_wallet_fixture


class NoticeFormatHelperTests(TestCase):
    def test_idr_amount_is_grouped_without_decimals(self):
        self.assertEqual(_format_notice_amount(Decimal('20000.00')), '20.000')
        self.assertEqual(_format_notice_amount(Decimal('1500000.00'), 'IDR'), '1.500.000')
        self.assertEqual(_format_notice_amount(Decimal('0.00')), '0')

    def test_other_currencies_keep_two_decimals(self):
        self.assertEqual(_format_notice_amount(Decimal('20000.00'), 'USD'), '20,000.00')

    def test_date_uses_indonesian_month_names(self):
        self.assertEqual(_format_notice_date(datetime.date(2026, 9, 19)), '19 September 2026')
        self.assertEqual(_format_notice_date(datetime.date(2026, 8, 1)), '1 Agustus 2026')


class ReconciliationNoticeRenderingTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        attach_financial_guardian(self.fx)
        self.case = make_open_case(self.fx, self.merchant, self.product, self.terminal)  # shortfall 10.000
        call_command('seed_notification_templates', foundation_id=self.fx['foundation'].id, verbosity=0)
        NotificationIntent.all_tenants.all().delete()

    def _render(self, template_key, channels):
        intent = NotificationIntent.all_tenants.filter(template_key=template_key).order_by('-id').first()
        self.assertIsNotNone(intent, template_key)
        out = {}
        for channel in channels:
            rendered = render_template_message(template_key, channel, self.fx['foundation'].id, intent.payload)
            out[channel] = f"{rendered['subject']} {rendered['body']}"
        return out

    def _assert_reads_as_id_id(self, texts, shortfall_text):
        for channel, text in texts.items():
            with self.subTest(channel=channel):
                self.assertNotIn('{', text)
                self.assertIn(shortfall_text, text)
                self.assertNotRegex(text, r'\d{4}-\d{2}-\d{2}', 'ISO date leaked into guardian copy')
                self.assertNotRegex(text, r'\d\.\d{2}(?!\d)', 'raw Decimal leaked into guardian copy')

    def test_first_notice(self):
        queue_reconciliation_notice(self.case.wallet)
        texts = self._render('wallet.recon.notice', (ChannelType.WHATSAPP, ChannelType.PUSH))
        self._assert_reads_as_id_id(texts, 'Rp 10.000')
        self.assertRegex(texts[ChannelType.WHATSAPP], r'\d{1,2} [A-Z][a-z]+ \d{4}')

    def test_resend_matches_first_notice(self):
        queue_reconciliation_notice(self.case.wallet)
        first = NotificationIntent.all_tenants.get(template_key='wallet.recon.notice').payload
        resend_reconciliation_notice(self.case)
        resent = NotificationIntent.all_tenants.filter(template_key='wallet.recon.notice').order_by('-id').first().payload
        self.assertEqual(resent, first)

    def test_reminder(self):
        self.case.notice_sent_at = timezone.now() - datetime.timedelta(hours=49)
        self.case.save()
        wallet = list(get_wallets_due_for_reminder(foundation_id=self.fx['foundation'].id))[0]
        queue_reconciliation_reminder(wallet)
        texts = self._render('wallet.recon.reminder', (ChannelType.WHATSAPP, ChannelType.PUSH))
        self._assert_reads_as_id_id(texts, 'Rp 10.000')

    def test_invoiced(self):
        self.case.detected_at = timezone.now() - datetime.timedelta(days=8)
        self.case.save()
        invoice_reconciliation_case(self.case)
        texts = self._render('wallet.recon.invoiced', (ChannelType.WHATSAPP,))
        self._assert_reads_as_id_id(texts, 'Rp 10.000')
