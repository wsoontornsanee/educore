from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.academic.models import ClassGroup, Subject
from apps.academic.tests.base import build_academic_fixture
from educore.middleware.tenancy import clear_current_foundation_id


class TenancyIsolationTests(TestCase):
    def setUp(self):
        self.fx_a = build_academic_fixture(foundation_name="Yayasan A")
        clear_current_foundation_id()
        self.fx_b = build_academic_fixture(foundation_name="Yayasan B")

    def test_subject_queryset_is_tenant_scoped(self):
        clear_current_foundation_id()
        from educore.middleware.tenancy import set_current_foundation_id
        set_current_foundation_id(self.fx_a['foundation'].id)
        codes = set(Subject.objects.values_list('code', flat=True))
        self.assertIn('MTK', codes)
        self.assertEqual(Subject.objects.count(), 1)

    def test_no_foundation_context_returns_empty(self):
        clear_current_foundation_id()
        self.assertEqual(Subject.objects.count(), 0)


class UniqueConstraintTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()

    def test_duplicate_subject_code_per_school_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Subject.objects.create(
                    foundation_id=self.fx['foundation'].id,
                    school=self.fx['school'],
                    code='MTK',
                    name='Matematika Lanjut',
                )

    def test_duplicate_class_group_name_per_academic_year_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ClassGroup.objects.create(
                    foundation_id=self.fx['foundation'].id,
                    school=self.fx['school'],
                    academic_year=self.fx['academic_year'],
                    grade_level=10,
                    name='X IPA 1',
                )
