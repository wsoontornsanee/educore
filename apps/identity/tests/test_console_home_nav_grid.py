from django.test import TestCase
from django.urls import reverse

from apps.identity.models import Foundation, RoleAssignment, School, User


class ConsoleHomeNavGridTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school = School.objects.create(foundation_id=self.foundation.id, name='S', npsn='12345678', level=School.LEVEL_SMA)
        self.user = User.objects.create(
            phone_e164='+6281300000030', full_name='Canteen', foundation_id=self.foundation.id,
        )
        self.user.set_password('pw12345')
        self.user.save()
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.user, role=RoleAssignment.ROLE_CANTEEN_OPERATOR,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.client.force_login(self.user)

    def test_console_home_hides_coming_soon_items_even_with_permission(self):
        """'Kantin & dompet' has no real page yet (coming_soon) and must not
        render even though Canteen Operator holds wallet.topup.read — see
        apps.identity.nav.COMING_SOON_URL_NAME."""
        response = self.client.get(reverse('web-console-home'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Kantin &amp; dompet')
        self.assertNotContains(response, 'Rekonsiliasi')
