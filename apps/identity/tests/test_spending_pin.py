"""Guardian spending PIN (spec 18 QRS-029): storage, strength, attempt policy, OTP reset, charge integration."""
import datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import AuditEvent
from apps.identity import pin as pin_service
from apps.identity.models import OTPChallenge, User, UserPin
from apps.identity.pin import PinError, change_pin, check_pin, hash_pin, reset_pin_with_otp, set_pin, verify_pin_hash
from apps.identity.services import request_phone_otp
from apps.wallet.qr_charge import create_qr_session
from apps.wallet.tests.test_qr_charge import GUARDIAN_PIN, QRFixtureMixin, make_guardian
from educore.middleware.tenancy import set_current_foundation_id


def make_user(fx, phone='+628151234567', email='pin@x.id'):
    set_current_foundation_id(fx['foundation'].id)
    return User.objects.create(foundation_id=fx['foundation'].id, phone_e164=phone, email=email, full_name='Wali PIN')


class PinBase(QRFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user(self.fx)

    def code(self, err_fn, *a, **kw):
        with self.assertRaises(PinError) as ctx:
            err_fn(*a, **kw)
        return ctx.exception


class StorageTests(PinBase):
    def test_hash_is_argon2id_hmac_salted_and_verifiable(self):
        h1, h2 = hash_pin('482913'), hash_pin('482913')
        self.assertTrue(h1.startswith('argon2id-hmac$19456,2,1$'))
        self.assertNotEqual(h1, h2)  # per-hash salt
        self.assertNotIn('482913', h1)
        self.assertTrue(verify_pin_hash('482913', h1))
        self.assertFalse(verify_pin_hash('482914', h1))

    def test_pepper_is_required_to_verify(self):
        h = hash_pin('482913')
        with self.settings(EDUCORE_PIN_PEPPER='a-different-secret'):
            self.assertFalse(verify_pin_hash('482913', h))  # a DB leak without the pepper cannot be checked offline
            h2 = hash_pin('482913')
            self.assertTrue(verify_pin_hash('482913', h2))
        self.assertFalse(verify_pin_hash('482913', h2))

    def test_garbage_verifiers_fail_closed(self):
        for bad in ('', 'x', 'argon2id-hmac$a,b,c$x$y', 'md5$1$2$3', 'argon2id-hmac$19456,2,1$!!$!!'):
            self.assertFalse(verify_pin_hash('482913', bad))

    def test_only_a_verifier_is_stored(self):
        set_pin(self.user, '482913')
        record = UserPin.all_tenants.get(user=self.user)
        self.assertNotIn('482913', record.pin_hash)
        self.assertEqual(record.foundation_id, self.user.foundation_id)


class StrengthTests(PinBase):
    def test_malformed_pins_refused(self):
        for bad in ('', '12345', '1234567', 'abcdef', '12 456', '４８２９１３'):
            self.assertEqual(self.code(set_pin, self.user, bad).code, 'PIN_INVALID_FORMAT', bad)

    def test_guessable_pins_refused(self):
        for weak in ('111111', '000000', '123456', '654321', '234567', '123123', '121212', '123321'):
            self.assertEqual(self.code(set_pin, self.user, weak).code, 'PIN_TOO_WEAK', weak)

    def test_phone_suffix_refused(self):
        self.assertEqual(self.code(set_pin, self.user, '234567').code, 'PIN_TOO_WEAK')  # also sequential
        u = make_user(self.fx, phone='+628159380417', email='p2@x.id')
        self.assertEqual(self.code(set_pin, u, '380417').code, 'PIN_TOO_WEAK')

    def test_good_pin_accepted_once(self):
        set_pin(self.user, '482913')
        self.assertEqual(self.code(set_pin, self.user, '739105').code, 'PIN_ALREADY_SET')


class AttemptPolicyTests(PinBase):
    def setUp(self):
        super().setUp()
        set_pin(self.user, '482913')

    def wrong(self, n=1):
        codes = []
        for _ in range(n):
            codes.append(self.code(check_pin, self.user, '000111').code)
        return codes

    def test_correct_pin_passes(self):
        check_pin(self.user, '482913')

    def test_failure_count_persists_across_the_refusal(self):
        self.wrong(1)
        self.assertEqual(UserPin.all_tenants.get(user=self.user).failed_count, 1)  # not rolled back with the error

    def test_three_failures_lock_for_fifteen_minutes(self):
        self.assertEqual(self.wrong(3), ['PIN_INVALID', 'PIN_INVALID', 'PIN_LOCKED'])
        err = self.code(check_pin, self.user, '482913')  # even the right PIN is refused while locked
        self.assertEqual(err.code, 'PIN_LOCKED')
        self.assertAlmostEqual(err.extra['retry_after_seconds'], 900, delta=5)

    def test_lock_expires_and_success_resets_counters(self):
        self.wrong(3)
        later = timezone.now() + datetime.timedelta(minutes=16)
        with mock.patch('apps.identity.pin.timezone.now', return_value=later):
            check_pin(self.user, '482913')
        record = UserPin.all_tenants.get(user=self.user)
        self.assertEqual((record.failed_count, record.locked_until), (0, None))

    def test_attempts_left_reported(self):
        e1 = self.code(check_pin, self.user, '000111')
        e2 = self.code(check_pin, self.user, '000111')
        self.assertEqual((e1.extra['attempts_left'], e2.extra['attempts_left']), (2, 1))

    def test_ten_failures_require_otp_reset(self):
        t = timezone.now()
        for i in range(9):
            with mock.patch('apps.identity.pin.timezone.now', return_value=t + datetime.timedelta(minutes=16 * i)):
                self.code(check_pin, self.user, '000111')
        with mock.patch('apps.identity.pin.timezone.now', return_value=t + datetime.timedelta(minutes=16 * 9)):
            self.assertEqual(self.code(check_pin, self.user, '000111').code, 'PIN_RESET_REQUIRED')
            self.assertEqual(self.code(check_pin, self.user, '482913').code, 'PIN_RESET_REQUIRED')
        self.assertTrue(pin_service.get_pin_status(self.user)['requires_otp_reset'])

    def test_failures_older_than_24h_do_not_accumulate(self):
        self.wrong(2)
        with mock.patch('apps.identity.pin.timezone.now', return_value=timezone.now() + datetime.timedelta(hours=25)):
            self.assertEqual(self.code(check_pin, self.user, '000111').extra['attempts_left'], 2)

    def test_not_set(self):
        u = make_user(self.fx, phone='+628159999999', email='n@x.id')
        self.assertEqual(self.code(check_pin, u, '482913').code, 'PIN_NOT_SET')

    def test_lock_and_reset_required_are_audited(self):
        self.wrong(3)
        self.assertTrue(AuditEvent.objects.filter(action='identity.pin.locked').exists())


class ChangeAndResetTests(PinBase):
    def setUp(self):
        super().setUp()
        set_pin(self.user, '482913')

    def test_change_needs_current_pin_and_counts_failures(self):
        self.assertEqual(self.code(change_pin, self.user, '000111', '739105').code, 'PIN_INVALID')
        change_pin(self.user, '482913', '739105')
        check_pin(self.user, '739105')
        self.assertEqual(self.code(check_pin, self.user, '482913').code, 'PIN_INVALID')

    def test_change_validates_new_pin_first(self):
        self.assertEqual(self.code(change_pin, self.user, '482913', '111111').code, 'PIN_TOO_WEAK')
        check_pin(self.user, '482913')  # unchanged

    def _otp(self, phone=None):
        challenge, raw = request_phone_otp(phone or self.user.phone_e164)
        return challenge, raw

    def test_reset_with_valid_otp_clears_lock_and_sets_new_pin(self):
        for _ in range(3):
            self.code(check_pin, self.user, '000111')
        challenge, raw = self._otp()
        reset_pin_with_otp(self.user, challenge.id, raw, '739105')
        check_pin(self.user, '739105')
        record = UserPin.all_tenants.get(user=self.user)
        self.assertEqual((record.failed_count, record.requires_otp_reset), (0, False))

    def test_reset_rejects_wrong_code_and_someone_elses_challenge(self):
        challenge, raw = self._otp()
        self.assertEqual(self.code(reset_pin_with_otp, self.user, challenge.id, '000000', '739105').code, 'OTP_INVALID')
        other_challenge, other_raw = self._otp('+628157654321')
        self.assertEqual(self.code(reset_pin_with_otp, self.user, other_challenge.id, other_raw, '739105').code, 'OTP_INVALID')
        check_pin(self.user, '482913')  # unchanged

    def test_otp_is_single_use(self):
        challenge, raw = self._otp()
        reset_pin_with_otp(self.user, challenge.id, raw, '739105')
        self.assertEqual(self.code(reset_pin_with_otp, self.user, challenge.id, raw, '857260').code, 'OTP_INVALID')


class PinApiTests(PinBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_status_set_change_flow(self):
        self.assertFalse(self.client.get('/api/v1/me/pin/').json()['is_set'])
        res = self.client.post('/api/v1/me/pin/', {'pin': '482913'}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.json()['is_set'])
        self.assertNotIn('pin_hash', res.json())
        self.assertEqual(self.client.post('/api/v1/me/pin/', {'pin': '739105'}, format='json').json()['error'], 'PIN_ALREADY_SET')
        res = self.client.put('/api/v1/me/pin/', {'current_pin': '000111', 'new_pin': '739105'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['attempts_left'], 2)
        res = self.client.put('/api/v1/me/pin/', {'current_pin': '482913', 'new_pin': '739105'}, format='json')
        self.assertEqual(res.status_code, 200)

    def test_weak_pin_rejected_over_api(self):
        res = self.client.post('/api/v1/me/pin/', {'pin': '123456'}, format='json')
        self.assertEqual((res.status_code, res.json()['error']), (400, 'PIN_TOO_WEAK'))

    def test_locked_returns_423_with_retry_after(self):
        set_pin(self.user, '482913')
        for _ in range(2):
            self.client.put('/api/v1/me/pin/', {'current_pin': '000111', 'new_pin': '739105'}, format='json')
        res = self.client.put('/api/v1/me/pin/', {'current_pin': '000111', 'new_pin': '739105'}, format='json')
        self.assertEqual((res.status_code, res.json()['error']), (423, 'PIN_LOCKED'))
        self.assertIn('retry_after_seconds', res.json())

    def test_reset_endpoint(self):
        set_pin(self.user, '482913')
        challenge, raw = request_phone_otp(self.user.phone_e164)
        res = self.client.post('/api/v1/me/pin/reset/', {'challenge_id': challenge.id, 'code': raw, 'new_pin': '739105'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.client.post('/api/v1/me/pin/reset/', {'challenge_id': 'x'}, format='json').status_code, 400)

    def test_anonymous_refused(self):
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get('/api/v1/me/pin/').status_code, (401, 403))


class ChargeIntegrationTests(QRFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.guardian = make_guardian(self.fx)
        self.client = APIClient()
        self.client.force_authenticate(user=self.guardian)
        self.token = create_qr_session(self.terminal)['token']
        set_current_foundation_id(self.fx['foundation'].id)

    def charge(self, pin, key='k', token=None, amount='5000.00'):
        body = {'token': token or self.token, 'student_id': self.student.id, 'amount': amount, 'idempotency_key': key}
        if pin is not None:
            body['pin'] = pin
        return self.client.post('/api/v1/wallet/qr/charge/', body, format='json')

    def test_correct_pin_charges(self):
        res = self.charge(GUARDIAN_PIN)
        self.assertEqual(res.status_code, 201, res.content)

    def test_missing_pin_is_a_validation_error_and_charges_nothing(self):
        self.assertEqual(self.charge(None).status_code, 400)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))

    def test_wrong_pin_charges_nothing_and_counts(self):
        res = self.charge('000111')
        self.assertEqual((res.status_code, res.json()['error']), (400, 'PIN_INVALID'))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))
        self.assertEqual(UserPin.all_tenants.get(user=self.guardian).failed_count, 1)

    def test_no_pin_set_is_refused_with_setup_hint(self):
        UserPin.all_tenants.filter(user=self.guardian).delete()
        res = self.charge(GUARDIAN_PIN)
        self.assertEqual((res.status_code, res.json()['error']), (400, 'PIN_NOT_SET'))

    def test_locked_pin_reveals_nothing_about_the_qr(self):
        for _ in range(3):
            self.charge('000111')
        good = self.charge(GUARDIAN_PIN)
        garbage = self.charge(GUARDIAN_PIN, token='garbage-token')
        self.assertEqual((good.status_code, garbage.status_code), (423, 423))
        self.assertEqual(good.json()['error'], garbage.json()['error'])  # same answer for a valid and an invalid QR
        self.assertEqual({k for k in good.json()}, {k for k in garbage.json()})

    def test_business_refusals_do_not_count_against_the_pin(self):
        res = self.charge(GUARDIAN_PIN, amount='60000.00')  # above the cap
        self.assertEqual(res.json()['error'], 'AMOUNT_ABOVE_CAP')
        self.assertEqual(UserPin.all_tenants.get(user=self.guardian).failed_count, 0)

    def test_successful_charge_resets_the_failure_count(self):
        self.charge('000111')
        self.assertEqual(self.charge(GUARDIAN_PIN, key='ok').status_code, 201)
        self.assertEqual(UserPin.all_tenants.get(user=self.guardian).failed_count, 0)

    def test_resolve_needs_no_pin(self):
        res = self.client.post('/api/v1/wallet/qr/resolve/', {'token': self.token, 'student_id': self.student.id}, format='json')
        self.assertEqual(res.status_code, 200)
