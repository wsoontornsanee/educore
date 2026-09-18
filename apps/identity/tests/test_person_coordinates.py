"""Unit tests for Person mother_name and home coordinates (lat/long) fields."""
from decimal import Decimal
from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.fields import CoordinateField
from apps.identity.models import Foundation, Person, School, Student, User
from apps.identity.rbac import ROLE_FOUNDATION_ADMIN, assign_role, SCOPE_FOUNDATION
from apps.identity.serializers import StudentSerializer, StaffPersonSummarySerializer
from apps.identity.views import StudentViewSet
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class PersonCoordinatesTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Nusantara", brand_name="Nusantara", npwp="01.234.567.8-999.000"
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Nusantara",
            npsn="12345678",
            level=School.LEVEL_SMA,
        )

    def test_create_person_with_mother_name_and_coordinates(self):
        person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Budi Santoso",
            nik="3171010101010099",
            dob=date(2008, 1, 1),
            gender=Person.GENDER_MALE,
            mother_name="Siti Rahayu",
            home_latitude=Decimal("-6.208800"),
            home_longitude=Decimal("106.845600"),
        )
        person.full_clean()
        person.refresh_from_db()

        self.assertEqual(person.mother_name, "Siti Rahayu")
        self.assertEqual(person.home_latitude, Decimal("-6.208800"))
        self.assertEqual(person.home_longitude, Decimal("106.845600"))

    def test_float_rejected_for_coordinates(self):
        """Float values are strictly rejected by CoordinateField."""
        field = CoordinateField()

        with self.assertRaises(ValidationError):
            field.to_python(-6.2088)

        with self.assertRaises(ValueError):
            field.get_prep_value(-6.2088)

    def test_coordinates_range_validation(self):
        """Latitude must be between -90 and 90; longitude between -180 and 180."""
        invalid_lat = Person(
            foundation_id=self.foundation.id,
            full_name="Invalid Lat",
            home_latitude=Decimal("95.000000"),
        )
        with self.assertRaises(ValidationError):
            invalid_lat.full_clean()

        invalid_lon = Person(
            foundation_id=self.foundation.id,
            full_name="Invalid Lon",
            home_longitude=Decimal("185.000000"),
        )
        with self.assertRaises(ValidationError):
            invalid_lon.full_clean()

    def test_student_serializer_exposes_new_fields(self):
        person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Dewi Lestari",
            mother_name="Ibu Pertiwi",
            home_latitude=Decimal("-7.250445"),
            home_longitude=Decimal("112.768845"),
        )
        student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=person,
            nis="2026101",
            status=Student.STATUS_ACTIVE,
        )

        data = StudentSerializer(student).data
        self.assertIn('person', data)
        self.assertEqual(data['person']['mother_name'], "Ibu Pertiwi")
        self.assertEqual(data['person']['home_latitude'], "-7.250445")
        self.assertEqual(data['person']['home_longitude'], "112.768845")

    def test_staff_summary_serializer_exposes_coordinates(self):
        person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Guru Teladan",
            home_latitude=Decimal("-6.175392"),
            home_longitude=Decimal("106.827153"),
        )
        data = StaffPersonSummarySerializer(person).data
        self.assertEqual(data['home_latitude'], "-6.175392")
        self.assertEqual(data['home_longitude'], "106.827153")

    def test_student_viewset_create_saves_mother_name_and_coordinates(self):
        user = User.all_tenants.create_user(
            phone_e164="+6281299990001",
            foundation_id=self.foundation.id,
            full_name="Admin Foundation",
        )
        assign_role(user, ROLE_FOUNDATION_ADMIN, SCOPE_FOUNDATION, self.foundation.id, self.foundation.id)

        factory = APIRequestFactory()
        payload = {
            'school_id': self.school.id,
            'nis': '2026500',
            'full_name': 'Rian Pratama',
            'mother_name': 'Kartini',
            'home_latitude': '-6.914744',
            'home_longitude': '107.609810',
        }
        request = factory.post('/api/v1/students/', payload, format='json')
        force_authenticate(request, user=user)

        view = StudentViewSet.as_view({'post': 'create'})
        response = view(request)

        self.assertEqual(response.status_code, 201)
        created_student = Student.objects.get(nis='2026500')
        self.assertEqual(created_student.person.mother_name, 'Kartini')
        self.assertEqual(created_student.person.home_latitude, Decimal('-6.914744'))
        self.assertEqual(created_student.person.home_longitude, Decimal('107.609810'))
