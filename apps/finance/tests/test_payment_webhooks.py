import hashlib
from decimal import Decimal
from django.test import TestCase

from apps.finance.models import (
    Invoice,
    InvoiceStatus,
    LedgerJournal,
    Payment,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentMethod,
    PaymentStatus,
)
from apps.finance.services.payment_providers import MidtransPaymentProvider, MockPaymentProvider
from apps.finance.services.payments import process_payment_webhook
from apps.identity.models import Foundation, Person, School, Student
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class PaymentWebhookTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Darul Ilmi",
            brand_name="Darul Ilmi",
            npwp="01.999.888.7-666.000",
            address="Yogyakarta",
        )
        set_current_foundation_id(self.foundation.id)

        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Darul Ilmi",
            npsn="20400001",
            level=School.LEVEL_SMA,
            base_currency="IDR",
        )

        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Farhan Ramadhan",
            nik="3471010101010003",
            gender=Person.GENDER_MALE,
            dob="2010-03-20",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person,
            nis="202603001",
            nisn="0034567890",
            status=Student.STATUS_ACTIVE,
        )

        self.invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number=f"INV/{self.school.npsn}/2026/000100",
            period="2026-10",
            due_date="2026-10-10",
            subtotal=Decimal('600000.00'),
            total=Decimal('600000.00'),
            paid=Decimal('0.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_midtrans_signature_verification(self):
        """FIN-013: Midtrans SHA512 signature verification."""
        provider = MidtransPaymentProvider(server_key="test-server-key")
        order_id = "ORDER-12345"
        status_code = "200"
        gross_amount = "600000.00"
        raw = f"{order_id}{status_code}{gross_amount}test-server-key"
        valid_signature = hashlib.sha512(raw.encode('utf-8')).hexdigest()

        payload = {
            'order_id': order_id,
            'status_code': status_code,
            'gross_amount': gross_amount,
            'signature_key': valid_signature,
        }
        self.assertTrue(provider.verify_webhook(payload))

        # Invalid signature fails
        payload['signature_key'] = "invalid_hash"
        self.assertFalse(provider.verify_webhook(payload))

    def test_webhook_idempotency_on_duplicate_call(self):
        """
        Acceptance Criterion 3, FIN-013:
        A duplicate webhook with the same external_id is a no-op returning 200.
        """
        order_id = "MID-ORDER-99901"
        secret = "mock-secret"
        amount = "600000.00"
        sig = hashlib.sha256(f"{order_id}{amount}{secret}".encode('utf-8')).hexdigest()

        payload = {
            'order_id': order_id,
            'amount': amount,
            'fee': '4000.00',
            'status': 'SETTLED',
            'channel': 'BCA_VA',
            'signature': sig,
            'metadata': {'intent_id': None},
        }

        # First webhook call
        res1 = process_payment_webhook(provider_name='MOCK', payload=payload)
        self.assertEqual(res1['status'], 'settled')
        payment_id = res1['payment_id']

        journals_count_after_first = LedgerJournal.objects.filter(ref_id=str(payment_id)).count()
        self.assertEqual(journals_count_after_first, 1)

        # Second webhook call with identical external_id
        res2 = process_payment_webhook(provider_name='MOCK', payload=payload)
        self.assertEqual(res2['status'], 'already_settled')
        self.assertEqual(res2['payment_id'], payment_id)

        # Verify no duplicate journal created
        journals_count_after_second = LedgerJournal.objects.filter(ref_id=str(payment_id)).count()
        self.assertEqual(journals_count_after_second, 1)

        # Verify invoice is still paid exactly once
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.PAID)
        self.assertEqual(self.invoice.paid, Decimal('600000.00'))

    def test_out_of_order_webhook_handling(self):
        """
        Acceptance Criterion 4, FIN-013:
        A settlement webhook arriving before its authorisation webhook still
        results in exactly one SETTLED payment.
        """
        order_id = "MID-OUT-OF-ORDER-888"
        amount = "600000.00"
        secret = "mock-secret"
        sig = hashlib.sha256(f"{order_id}{amount}{secret}".encode('utf-8')).hexdigest()

        # Direct SETTLED webhook arrived first!
        settlement_payload = {
            'order_id': order_id,
            'amount': amount,
            'fee': '3000.00',
            'status': 'SETTLED',
            'channel': 'BNI_VA',
            'signature': sig,
        }

        res = process_payment_webhook(provider_name='MOCK', payload=settlement_payload)
        self.assertEqual(res['status'], 'settled')

        payment = Payment.objects.get(id=res['payment_id'])
        self.assertEqual(payment.status, PaymentStatus.SETTLED)
        self.assertEqual(payment.external_id, order_id)
        self.assertEqual(payment.fee, Decimal('3000.00'))
        self.assertEqual(payment.net, Decimal('597000.00'))

        # Later, authorization / pending webhook arrives for the same order_id
        auth_payload = {
            'order_id': order_id,
            'amount': amount,
            'fee': '3000.00',
            'status': 'PENDING',
            'channel': 'BNI_VA',
            'signature': sig,
        }
        res_auth = process_payment_webhook(provider_name='MOCK', payload=auth_payload)
        self.assertEqual(res_auth['status'], 'already_settled')

        # Payment remains SETTLED
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.SETTLED)
