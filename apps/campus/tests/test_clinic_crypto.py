from django.test import TestCase, override_settings

from apps.campus.crypto import decrypt_note, encrypt_note


class ClinicCryptoTests(TestCase):
    def test_round_trip(self):
        ciphertext = encrypt_note("Demam tinggi, alergi obat penisilin.")
        self.assertNotEqual(ciphertext, "Demam tinggi, alergi obat penisilin.")
        self.assertEqual(decrypt_note(ciphertext), "Demam tinggi, alergi obat penisilin.")

    @override_settings(EDUCORE_CLINIC_FERNET_KEY='')
    def test_round_trip_with_secret_key_fallback(self):
        ciphertext = encrypt_note("Catatan rahasia")
        self.assertEqual(decrypt_note(ciphertext), "Catatan rahasia")

    def test_decrypt_wrong_key_raises(self):
        from cryptography.fernet import Fernet
        ciphertext = Fernet(Fernet.generate_key()).encrypt(b"data").decode()
        with self.assertRaises(RuntimeError):
            decrypt_note(ciphertext)

    @override_settings(EDUCORE_CLINIC_FERNET_KEY='not-a-valid-fernet-key')
    def test_malformed_explicit_key_fails_hard_instead_of_silent_fallback(self):
        with self.assertRaises(RuntimeError):
            encrypt_note("Demam tinggi")

    @override_settings(EDUCORE_COUNSELLING_FERNET_KEY='also-not-valid')
    def test_malformed_explicit_counselling_key_fails_hard(self):
        from apps.campus.crypto import encrypt_notes
        with self.assertRaises(RuntimeError):
            encrypt_notes("Catatan sesi")
