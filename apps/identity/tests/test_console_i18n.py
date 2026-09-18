from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.identity.models import Foundation, RoleAssignment, School, User


class ConsoleEnglishTranslationTests(TestCase):
    """Nav labels and page titles are module-level constants, evaluated at
    import time — using gettext_lazy (not plain str/gettext) is what lets
    them resolve to the site's active language at render time. Setting the
    django_language cookie, the same mechanism the site-wide language
    switcher (django.views.i18n.set_language) uses, is what actually
    exercises LocaleMiddleware end-to-end (see apps/status/tests/test_views.py
    for the established pattern in this repo)."""

    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school = School.objects.create(foundation_id=self.foundation.id, name='S', npsn='12345678', level=School.LEVEL_SMA)
        self.teacher = User.objects.create(
            phone_e164='+6281300000050', full_name='Teacher EN', foundation_id=self.foundation.id,
        )
        self.teacher.set_password('pw12345')
        self.teacher.save()
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.teacher, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.client.force_login(self.teacher)
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'en'

    def test_nav_labels_render_in_english(self):
        response = self.client.get(reverse('console:coming_soon'))
        self.assertContains(response, 'Grading queue')
        self.assertContains(response, 'Attendance & gate', html=True)
        self.assertNotContains(response, 'Antrean penilaian')

    def test_coming_soon_page_title_and_body_render_in_english(self):
        response = self.client.get(reverse('console:coming_soon'))
        self.assertContains(response, "This module isn't available yet")
        self.assertNotContains(response, 'Modul ini belum tersedia')

    def test_landing_page_title_and_stats_labels_render_in_english(self):
        response = self.client.get(reverse('console-home-agenda'))
        self.assertContains(response, "Today&#x27;s agenda")
        self.assertContains(response, 'Active students')
        self.assertNotContains(response, 'Siswa aktif')
