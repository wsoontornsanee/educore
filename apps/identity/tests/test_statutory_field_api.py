"""Tests for the DAPODIK/EMIS field expansion API surface (CMP-017).

Verifies the new statutory fields flow through the create endpoints:
- POST /students/ with Person statutory fields (religion, birth city,
  birth certificate number, structured address)
- POST /staff/ with Staff statutory fields (NUPTK, appointment type,
  certification, degree) — plus person_extra passthrough on the service
"""
from rest_framework import status
from rest_framework.test import APITestCase

from apps.identity.models import Foundation, Person, School, Staff, User
from apps.identity.rbac import assign_role, ROLE_FOUNDATION_ADMIN, SCOPE_FOUNDATION
from apps.identity.services import create_user_with_person
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context


class StatutoryFieldCreateAPITests(APITestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Insan Cemerlang",
            brand_name="Insan Cemerlang",
        )
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SD Insan Cemerlang",
            npsn="50400001",
            level=School.LEVEL_SD,
        )
        self.admin = User.all_tenants.create_user(
            foundation_id=self.foundation.id,
            phone_e164="+6281100001111",
            full_name="Admin Yayasan",
        )
        assign_role(
            user=self.admin,
            role=ROLE_FOUNDATION_ADMIN,
            scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
            foundation_id=self.foundation.id,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_create_student_with_statutory_fields(self):
        """POST /students/ persists the new Person statutory fields."""
        self.client.force_authenticate(user=self.admin)
        payload = {
            'school_id': self.school.id,
            'nis': '2026-SD-201',
            'nisn': '0012345678',
            'full_name': 'Siti Aminah',
            'nik': '3201012010010009',
            'dob': '2010-01-01',
            'gender': 'P',
            'address': 'Jl. Melati No. 12',
            'religion': 'ISLAM',
            'birth_city': 'Bandung',
            'birth_certificate_number': 'ACT-2010-112233',
            'citizenship': 'WNI',
            'rt': '004',
            'rw': '007',
            'dusun': 'Cibaduyut',
            'kelurahan': 'Menteng',
            'kecamatan': 'Tebet',
            'kabupaten_kota': 'Jakarta Selatan',
            'provinsi': 'DKI Jakarta',
            'postal_code': '12810',
        }
        response = self.client.post('/api/v1/students/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        person = Person.all_tenants.get(nik='3201012010010009')
        self.assertEqual(person.religion, 'ISLAM')
        self.assertEqual(person.birth_city, 'Bandung')
        self.assertEqual(person.birth_certificate_number, 'ACT-2010-112233')
        self.assertEqual(person.citizenship, 'WNI')
        self.assertEqual(person.rt, '004')
        self.assertEqual(person.rw, '007')
        self.assertEqual(person.dusun, 'Cibaduyut')
        self.assertEqual(person.kelurahan, 'Menteng')
        self.assertEqual(person.kecamatan, 'Tebet')
        self.assertEqual(person.kabupaten_kota, 'Jakarta Selatan')
        self.assertEqual(person.provinsi, 'DKI Jakarta')
        self.assertEqual(person.postal_code, '12810')

        # Serializer exposes the fields back
        self.assertEqual(response.data['person']['religion'], 'ISLAM')
        self.assertEqual(response.data['person']['kabupaten_kota'], 'Jakarta Selatan')

    def test_create_student_without_statutory_fields_still_works(self):
        """Backward compat: omitting the new optional fields defaults them."""
        self.client.force_authenticate(user=self.admin)
        payload = {
            'school_id': self.school.id,
            'nis': '2026-SD-202',
            'full_name': 'Budi Minimal',
            'phone_e164_unused': None,
        }
        response = self.client.post('/api/v1/students/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        person = Person.all_tenants.get(full_name='Budi Minimal')
        self.assertEqual(person.religion, '')
        self.assertEqual(person.citizenship, 'WNI')  # default

    def test_create_staff_with_statutory_fields(self):
        """POST /staff/ persists NUPTK, appointment type, certification, and degree."""
        self.client.force_authenticate(user=self.admin)
        payload = {
            'school_id': self.school.id,
            'nip': '198505202010011002',
            'nuptk': '1612345678900077',
            'employment_type': 'PERMANENT',
            'appointment_type': 'PNS',
            'certification_status': 'SERTIFIKAT',
            'highest_degree': 'S1',
            'degree_institution': 'Universitas Pendidikan Indonesia',
            'degree_graduation_year': 2008,
            'join_date': '2010-01-01',
            'full_name': 'Pak Guru Baru',
            'nik': '3201018505200001',
            'phone_e164': '+6281100005555',
            'religion': 'KRISTEN',
            'birth_city': 'Semarang',
        }
        response = self.client.post('/api/v1/staff/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        staff = Staff.all_tenants.get(nuptk='1612345678900077')
        self.assertEqual(staff.appointment_type, 'PNS')
        self.assertEqual(staff.certification_status, 'SERTIFIKAT')
        self.assertEqual(staff.highest_degree, 'S1')
        self.assertEqual(staff.degree_institution, 'Universitas Pendidikan Indonesia')
        self.assertEqual(staff.degree_graduation_year, 2008)
        self.assertEqual(staff.person.religion, 'KRISTEN')
        self.assertEqual(staff.person.birth_city, 'Semarang')

        # Serializer exposes the new fields back
        self.assertEqual(response.data['nuptk'], '1612345678900077')
        self.assertEqual(response.data['appointment_type'], 'PNS')
        self.assertEqual(response.data['highest_degree'], 'S1')

    def test_create_user_with_person_extra_passthrough(self):
        """create_user_with_person person_extra dict carries statutory fields."""
        with tenant_context(self.foundation.id):
            user, person = create_user_with_person(
                foundation_id=self.foundation.id,
                full_name='Ibu Statistik',
                phone='+6281100006666',
                nik='3201011980010001',
                person_extra={
                    'religion': 'HINDU',
                    'birth_city': 'Denpasar',
                    'kelurahan': 'Sanur',
                    'not_a_real_field': 'ignored',
                },
            )
        self.assertEqual(person.religion, 'HINDU')
        self.assertEqual(person.birth_city, 'Denpasar')
        self.assertEqual(person.kelurahan, 'Sanur')
        self.assertEqual(person.citizenship, 'WNI')  # untouched default
