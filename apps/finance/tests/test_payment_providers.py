import datetime
from decimal import Decimal
from unittest.mock import Mock, patch
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.finance.models import PaymentIntent, PaymentIntentStatus, PaymentMethod
from apps.finance.services.payment_providers import MidtransPaymentProvider, PaymentGatewayError, XenditPaymentProvider
from apps.identity.models import Foundation, Person, School, Student
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class XenditProviderFixture(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Xendit Test", brand_name="Xendit Test", npwp="01.222.333.4-555.000", address="Jakarta",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SD Xendit Test", npsn="20500001", level=School.LEVEL_SD, base_currency="IDR",
        )
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Andi Wijaya", nik="3471010101010303",
            gender=Person.GENDER_MALE, dob="2015-01-01",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.person,
            nis="XND-001", nisn="1234500001", status=Student.STATUS_ACTIVE,
        )

    def tearDown(self):
        clear_current_foundation_id()


@override_settings(XENDIT_API_KEY='xnd_test_key', XENDIT_BASE_URL='https://api.xendit.co')
class CreateVaTests(XenditProviderFixture):
    def test_missing_api_key_raises_before_any_request(self):
        provider = XenditPaymentProvider(api_key='')
        with self.assertRaises(PaymentGatewayError):
            provider.create_va(self.student, self.school, 'BCA', Decimal('100000'), timezone.now())

    @patch('apps.finance.services.payment_providers.requests.post')
    def test_creates_fixed_multi_use_va_with_correct_request_shape(self, mock_post):
        mock_post.return_value = Mock(ok=True, json=lambda: {
            'id': 'va-123', 'account_number': '8808001234500001', 'bank_code': 'BCA', 'is_closed': False, 'is_single_use': False,
        })

        provider = XenditPaymentProvider()
        expires_at = timezone.now() + datetime.timedelta(days=365)
        result = provider.create_va(self.student, self.school, 'bca', Decimal('0.00'), expires_at)

        self.assertEqual(result['va_number'], '8808001234500001')
        self.assertEqual(result['va_bank'], 'BCA')

        call_args = mock_post.call_args
        self.assertEqual(call_args.args[0], 'https://api.xendit.co/callback_virtual_accounts')
        body = call_args.kwargs['json']
        self.assertEqual(body['bank_code'], 'BCA')
        self.assertFalse(body['is_closed'])
        self.assertFalse(body['is_single_use'])
        self.assertEqual(call_args.kwargs['auth'], ('xnd_test_key', ''))

    @patch('apps.finance.services.payment_providers.requests.post')
    def test_deterministic_external_id_same_across_retries(self, mock_post):
        mock_post.return_value = Mock(ok=True, json=lambda: {'account_number': '8808001234500001'})
        provider = XenditPaymentProvider()

        provider.create_va(self.student, self.school, 'BCA', Decimal('0.00'), timezone.now())
        first_external_id = mock_post.call_args.kwargs['json']['external_id']

        provider.create_va(self.student, self.school, 'BCA', Decimal('0.00'), timezone.now())
        second_external_id = mock_post.call_args.kwargs['json']['external_id']

        self.assertEqual(first_external_id, second_external_id)

    @patch('apps.finance.services.payment_providers.requests.post')
    def test_non_2xx_response_raises_gateway_error(self, mock_post):
        mock_post.return_value = Mock(ok=False, status_code=400, text='{"error_code": "DUPLICATE_EXTERNAL_ID_ERROR"}')

        provider = XenditPaymentProvider()
        with self.assertRaises(PaymentGatewayError):
            provider.create_va(self.student, self.school, 'BCA', Decimal('0.00'), timezone.now())

    @patch('apps.finance.services.payment_providers.requests.post')
    def test_network_error_raises_gateway_error(self, mock_post):
        import requests
        mock_post.side_effect = requests.ConnectionError("boom")

        provider = XenditPaymentProvider()
        with self.assertRaises(PaymentGatewayError):
            provider.create_va(self.student, self.school, 'BCA', Decimal('0.00'), timezone.now())


@override_settings(XENDIT_API_KEY='xnd_test_key')
class CreateQrisTests(XenditProviderFixture):
    @patch('apps.finance.services.payment_providers.requests.post')
    def test_creates_dynamic_qris_with_correct_request_shape(self, mock_post):
        mock_post.return_value = Mock(ok=True, json=lambda: {'id': 'qr-123', 'qr_string': '00020101021226...'})

        provider = XenditPaymentProvider()
        result = provider.create_qris(self.student, self.school, Decimal('75000.00'), timezone.now())

        self.assertEqual(result['qris_payload'], '00020101021226...')

        call_args = mock_post.call_args
        self.assertEqual(call_args.args[0], 'https://api.xendit.co/qr_codes')
        body = call_args.kwargs['json']
        self.assertEqual(body['type'], 'DYNAMIC')
        self.assertEqual(body['amount'], 75000.00)


class WebhookVerificationTests(XenditProviderFixture):
    def test_verify_webhook_matches_callback_token(self):
        provider = XenditPaymentProvider(callback_token='secret-token')
        self.assertTrue(provider.verify_webhook({}, headers={'x-callback-token': 'secret-token'}))
        self.assertFalse(provider.verify_webhook({}, headers={'x-callback-token': 'wrong'}))
        self.assertFalse(provider.verify_webhook({}, headers=None))


@override_settings(XENDIT_API_KEY='xnd_test_key', XENDIT_BASE_URL='https://api.xendit.co')
class CheckStatusTests(XenditProviderFixture):
    """ARC-006/ARC-009 sync_payment_status safety net for a missed FIN-013 webhook."""

    def _make_intent(self, method, **overrides):
        defaults = dict(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            method=method, provider='XENDIT', amount=Decimal('300000.00'), currency='IDR',
            expires_at=timezone.now() + datetime.timedelta(hours=24),
            status=PaymentIntentStatus.PENDING, metadata={},
        )
        defaults.update(overrides)
        return PaymentIntent.objects.create(**defaults)

    @patch('apps.finance.services.payment_providers.requests.get')
    def test_qris_matches_by_its_own_external_id(self, mock_get):
        intent = self._make_intent(PaymentMethod.QRIS, metadata={'gateway_external_id': 'qris-1-5-999.0'})
        mock_get.return_value = Mock(ok=True, json=lambda: {'data': [
            {'id': 'txn-1', 'status': 'SUCCESS', 'amount': 300000, 'fee': 2000,
             'created': timezone.now().isoformat(), 'channel_code': 'QRIS'},
        ]})

        result = XenditPaymentProvider().check_status(intent)

        self.assertEqual(result['status'], 'SETTLED')
        self.assertEqual(result['amount'], Decimal('300000'))
        call_args = mock_get.call_args
        self.assertEqual(call_args.kwargs['params']['reference_id'], 'qris-1-5-999.0')

    def test_qris_returns_none_without_a_stored_external_id(self):
        intent = self._make_intent(PaymentMethod.QRIS, metadata={})
        self.assertIsNone(XenditPaymentProvider().check_status(intent))

    @patch('apps.finance.services.payment_providers.requests.get')
    def test_va_reconstructs_the_stable_account_external_id(self, mock_get):
        intent = self._make_intent(PaymentMethod.VA, va_bank='BCA', va_number='8808001234500001')
        mock_get.return_value = Mock(ok=True, json=lambda: {'data': []})

        result = XenditPaymentProvider().check_status(intent)

        self.assertIsNone(result)
        call_args = mock_get.call_args
        expected_ref = f"studentva-{self.foundation.id}-{self.student.id}-BCA"
        self.assertEqual(call_args.kwargs['params']['reference_id'], expected_ref)

    @patch('apps.finance.services.payment_providers.requests.get')
    def test_va_ignores_a_matching_amount_from_before_the_intent_was_created(self, mock_get):
        intent = self._make_intent(PaymentMethod.VA, va_bank='BCA')
        stale_time = (intent.created_at - datetime.timedelta(days=30)).isoformat()
        mock_get.return_value = Mock(ok=True, json=lambda: {'data': [
            {'id': 'txn-old', 'status': 'SUCCESS', 'amount': 300000, 'fee': 0, 'created': stale_time},
        ]})

        self.assertIsNone(XenditPaymentProvider().check_status(intent))

    @patch('apps.finance.services.payment_providers.requests.get')
    def test_returns_none_when_no_amount_matches(self, mock_get):
        intent = self._make_intent(PaymentMethod.QRIS, metadata={'gateway_external_id': 'qris-1-5-999.0'})
        mock_get.return_value = Mock(ok=True, json=lambda: {'data': [
            {'id': 'txn-1', 'status': 'SUCCESS', 'amount': 999999, 'fee': 0, 'created': timezone.now().isoformat()},
        ]})

        self.assertIsNone(XenditPaymentProvider().check_status(intent))

    def test_manual_and_cash_methods_are_never_queried(self):
        intent = self._make_intent(PaymentMethod.MANUAL)
        with patch('apps.finance.services.payment_providers.requests.get') as mock_get:
            result = XenditPaymentProvider().check_status(intent)
        mock_get.assert_not_called()
        self.assertIsNone(result)

    def test_midtrans_check_status_is_a_documented_no_op(self):
        """create_va/create_qris never mint a real Midtrans order_id (they're
        local simulations), so there is nothing genuine to poll — this is a
        known, deliberate gap, not a bug (see fetch_settlement's own docstring).
        """
        intent = self._make_intent(PaymentMethod.QRIS, provider='MIDTRANS')
        self.assertIsNone(MidtransPaymentProvider().check_status(intent))
