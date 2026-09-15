import datetime
from decimal import Decimal
from unittest.mock import Mock, patch
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.finance.services.payment_providers import PaymentGatewayError, XenditPaymentProvider
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
