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

    def test_console_page_hides_coming_soon_items_even_with_permission(self):
        """A coming_soon item never renders, even for a role holding its
        RBAC permission — see apps.identity.nav.COMING_SOON_URL_NAME."""
        response = self.client.get(reverse('console:coming_soon'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Antrean penilaian')
        self.assertNotContains(response, 'Kehadiran &amp; gerbang')
        self.assertNotContains(response, 'Rekonsiliasi')

    def test_nav_brand_block_links_to_console_home(self):
        response = self.client.get(reverse('console:coming_soon'))
        self.assertContains(response, f'href="{reverse("web-console-home")}"')

    def test_nav_has_mobile_drawer_toggle_and_breakpoint(self):
        """Regression guard for the mobile-display bug: the sidebar must not
        be a fixed-width flex child unconditionally shown at every viewport —
        it needs a phone breakpoint (off-canvas drawer) with a real open
        control, not just a static desktop rail."""
        response = self.client.get(reverse('console:coming_soon'))
        content = response.content.decode()
        self.assertIn('id="console-nav-open"', content)
        self.assertIn('@media (max-width: 719px)', content)
        self.assertIn('console-nav-scrim', content)
