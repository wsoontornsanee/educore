from django.test import TestCase
from django.urls import reverse
from apps.identity.models import Foundation, RoleAssignment, School, User


class ConsoleNavRenderTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school = School.objects.create(foundation_id=self.foundation.id, name='S', npsn='12345678', level=School.LEVEL_SMA)
        self.teacher = User.objects.create(
            phone_e164='+6281300000004', full_name='Teacher Nav', foundation_id=self.foundation.id,
        )
        self.teacher.set_password('pw12345')
        self.teacher.save()
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.teacher, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.client.force_login(self.teacher)

    def test_console_page_renders_nav_items_teacher_can_reach(self):
        response = self.client.get(reverse('console:coming_soon'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Antrean penilaian')
        # html=True: the label's literal "&" is HTML-escaped to "&amp;" by
        # Django's autoescaping when rendered via {{ item.label }}; comparing
        # as parsed HTML (rather than a raw substring) matches semantically.
        self.assertContains(response, 'Kehadiran & gerbang', html=True)
        self.assertNotContains(response, 'Rekonsiliasi')

    def test_nav_brand_block_links_to_console_home(self):
        response = self.client.get(reverse('console:coming_soon'))
        self.assertContains(response, f'href="{reverse("web-console-home")}"')
