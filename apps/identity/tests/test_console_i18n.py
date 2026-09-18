from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.identity.models import Foundation, Person, RoleAssignment, School, Staff, User


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
        person = Person.objects.create(foundation_id=self.foundation.id, full_name='Teacher EN')
        Staff.objects.create(
            foundation_id=self.foundation.id, person=person, user=self.teacher, school=self.school,
            join_date=timezone.localdate(),
        )
        self.client.force_login(self.teacher)
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'en'

    def test_nav_labels_render_in_english(self):
        """Real nav items render their EN label; coming_soon items stay hidden
        regardless of permission — see apps.identity.nav.COMING_SOON_URL_NAME."""
        response = self.client.get(reverse('console:coming_soon'))
        self.assertContains(response, 'Digital permission slips')
        self.assertContains(response, 'Grading queue')
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
