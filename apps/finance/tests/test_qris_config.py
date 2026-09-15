from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from pathlib import Path
from rest_framework.test import APIClient

from apps.finance.models import SchoolQrisConfig
from apps.finance.services import (
    InvalidProofFileError,
    get_school_qris_config,
    set_school_qris_config,
    store_payment_proof_file,
)
from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


def build_finance_fixture(foundation_name="Yayasan QRIS Test"):
    clear_current_foundation_id()
    foundation = Foundation.objects.create(
        legal_name=foundation_name, brand_name=foundation_name, npwp="01.333.444.5-666.000", address="Surabaya",
    )
    set_current_foundation_id(foundation.id)
    npsn_suffix = str(abs(hash(foundation_name)) % 100000).zfill(5)
    school = School.all_tenants.create(
        foundation_id=foundation.id, name="SD QRIS Test", npsn=f"302{npsn_suffix}", level=School.LEVEL_SD, base_currency="IDR",
    )
    person = Person.all_tenants.create(foundation_id=foundation.id, nik=f"34710101{npsn_suffix}", full_name="Siswa QRIS")
    student = Student.all_tenants.create(
        foundation_id=foundation.id, school=school, person=person, nisn=npsn_suffix, nis=f"QRIS-{npsn_suffix}", status=Student.STATUS_ACTIVE,
    )
    finance_user = User.objects.create(
        foundation_id=foundation.id, phone_e164=f"+6281{npsn_suffix}", email=f"finance.{npsn_suffix}@qris.school.id", full_name="Bendahara",
    )
    RoleAssignment.all_tenants.create(
        foundation_id=foundation.id, user=finance_user, role=RoleAssignment.ROLE_FINANCE_OFFICER,
        scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=foundation.id,
    )
    return {'foundation': foundation, 'school': school, 'student': student, 'finance_user': finance_user}


class SetSchoolQrisConfigTests(TestCase):
    def setUp(self):
        self.fx = build_finance_fixture()

    def test_set_config_with_payload_only(self):
        config = set_school_qris_config(self.fx['school'], qris_payload='00020101021226...', is_active=True)
        self.assertEqual(config.qris_payload, '00020101021226...')
        self.assertTrue(config.is_active)
        self.assertEqual(config.qris_image_key, '')

    def test_set_config_with_image_stored_on_disk(self):
        image = SimpleUploadedFile('qris.png', b'\x89PNG fake bytes', content_type='image/png')
        config = set_school_qris_config(self.fx['school'], qris_image=image)

        self.assertTrue(config.qris_image_key.startswith(f'qris_config/{self.fx["school"].id}/'))
        stored_path = Path(settings.MEDIA_ROOT) / config.qris_image_key
        self.assertTrue(stored_path.exists())
        stored_path.unlink()

    def test_updating_payload_only_keeps_existing_image(self):
        image = SimpleUploadedFile('qris.png', b'\x89PNG fake bytes', content_type='image/png')
        first = set_school_qris_config(self.fx['school'], qris_image=image, qris_payload='v1')
        second = set_school_qris_config(self.fx['school'], qris_payload='v2')

        self.assertEqual(second.qris_image_key, first.qris_image_key)
        self.assertEqual(second.qris_payload, 'v2')
        Path(settings.MEDIA_ROOT, first.qris_image_key).unlink()

    def test_disallowed_content_type_rejected(self):
        bad_file = SimpleUploadedFile('virus.exe', b'MZ', content_type='application/x-msdownload')
        with self.assertRaises(InvalidProofFileError):
            set_school_qris_config(self.fx['school'], qris_image=bad_file)

    def test_get_config_returns_none_when_unset(self):
        self.assertIsNone(get_school_qris_config(self.fx['school']))

    def test_only_one_config_per_school(self):
        set_school_qris_config(self.fx['school'], qris_payload='v1')
        set_school_qris_config(self.fx['school'], qris_payload='v2')
        self.assertEqual(SchoolQrisConfig.all_tenants.filter(school=self.fx['school']).count(), 1)


class StorePaymentProofFileTests(TestCase):
    def setUp(self):
        self.fx = build_finance_fixture()

    def test_valid_proof_stored_on_disk(self):
        upload = SimpleUploadedFile('bukti.jpg', b'\xff\xd8\xff fake jpeg', content_type='image/jpeg')
        meta = store_payment_proof_file(self.fx['school'], upload)

        self.assertEqual(meta['filename'], 'bukti.jpg')
        stored_path = Path(settings.MEDIA_ROOT) / meta['key']
        self.assertTrue(stored_path.exists())
        stored_path.unlink()

    def test_oversized_proof_rejected(self):
        from apps.finance.services.qris_config import MAX_PROOF_FILE_SIZE

        upload = SimpleUploadedFile('big.pdf', b'x' * 10, content_type='application/pdf')
        upload.size = MAX_PROOF_FILE_SIZE + 1

        with self.assertRaises(InvalidProofFileError):
            store_payment_proof_file(self.fx['school'], upload)


class QrisConfigViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_finance_fixture()

    def test_put_then_get_qris_config_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.put(
            f'/api/v1/finance/schools/{self.fx["school"].id}/qris-config/',
            {'qris_payload': '00020101021226...', 'is_active': True},
            format='multipart',
        )
        self.assertEqual(res.status_code, 200, res.content)

        res2 = self.client.get(f'/api/v1/finance/schools/{self.fx["school"].id}/qris-config/')
        self.assertEqual(res2.status_code, 200, res2.content)
        self.assertEqual(res2.json()['qris_payload'], '00020101021226...')

    def test_get_before_set_returns_404(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.get(f'/api/v1/finance/schools/{self.fx["school"].id}/qris-config/')
        self.assertEqual(res.status_code, 404)


class PaymentProofUploadAndStaticQrisFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_finance_fixture()

    def test_upload_proof_then_submit_static_qris_payment_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])

        upload = SimpleUploadedFile('bukti.jpg', b'\xff\xd8\xff fake jpeg', content_type='image/jpeg')
        res_upload = self.client.post(
            '/api/v1/finance/payments/upload-proof/',
            {'file': upload, 'student_id': self.fx['student'].id},
            format='multipart',
        )
        self.assertEqual(res_upload.status_code, 201, res_upload.content)
        proof_key = res_upload.json()['key']

        res_submit = self.client.post('/api/v1/finance/payments/manual/', {
            'student_id': self.fx['student'].id,
            'amount': '150000.00',
            'proof_file': proof_key,
            'channel': 'STATIC_QRIS',
        }, format='json')
        self.assertEqual(res_submit.status_code, 201, res_submit.content)
        self.assertEqual(res_submit.json()['channel'], 'STATIC_QRIS')
        self.assertEqual(res_submit.json()['status'], 'PENDING_VERIFICATION')
