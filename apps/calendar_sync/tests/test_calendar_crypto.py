from django.test import TestCase, override_settings

from apps.calendar_sync.crypto import decrypt_secret, encrypt_secret


class CalendarCryptoTests(TestCase):
    def test_round_trip(self):
        ciphertext = encrypt_secret("oauth-refresh-token")
        self.assertNotEqual(ciphertext, "oauth-refresh-token")
        self.assertEqual(decrypt_secret(ciphertext), "oauth-refresh-token")

    @override_settings(EDUCORE_CALENDAR_FERNET_KEY='')
    def test_round_trip_with_secret_key_fallback(self):
        ciphertext = encrypt_secret("token")
        self.assertEqual(decrypt_secret(ciphertext), "token")

    def test_decrypt_wrong_key_raises(self):
        from cryptography.fernet import Fernet
        ciphertext = Fernet(Fernet.generate_key()).encrypt(b"data").decode()
        with self.assertRaises(RuntimeError):
            decrypt_secret(ciphertext)

    @override_settings(EDUCORE_CALENDAR_FERNET_KEY='not-a-valid-fernet-key')
    def test_malformed_explicit_key_fails_hard_instead_of_silent_fallback(self):
        with self.assertRaises(RuntimeError):
            encrypt_secret("token")
