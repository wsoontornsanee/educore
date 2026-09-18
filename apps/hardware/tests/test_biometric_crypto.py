from django.test import TestCase, override_settings

from apps.hardware.crypto import decrypt_template, encrypt_template


class BiometricCryptoTests(TestCase):
    def test_round_trip(self):
        ciphertext = encrypt_template("raw-template-bytes-as-string")
        self.assertNotEqual(ciphertext, "raw-template-bytes-as-string")
        self.assertEqual(decrypt_template(ciphertext), "raw-template-bytes-as-string")

    @override_settings(EDUCORE_BIOMETRIC_FERNET_KEY='')
    def test_round_trip_with_secret_key_fallback(self):
        ciphertext = encrypt_template("template")
        self.assertEqual(decrypt_template(ciphertext), "template")

    def test_decrypt_wrong_key_raises(self):
        from cryptography.fernet import Fernet
        ciphertext = Fernet(Fernet.generate_key()).encrypt(b"data").decode()
        with self.assertRaises(RuntimeError):
            decrypt_template(ciphertext)

    @override_settings(EDUCORE_BIOMETRIC_FERNET_KEY='not-a-valid-fernet-key')
    def test_malformed_explicit_key_fails_hard_instead_of_silent_fallback(self):
        with self.assertRaises(RuntimeError):
            encrypt_template("template")
