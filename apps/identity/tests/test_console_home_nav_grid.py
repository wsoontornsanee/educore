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

    def test_console_home_lists_users_own_nav_items(self):
        response = self.client.get(reverse('web-console-home'))
        self.assertEqual(response.status_code, 200)
        # html=True: the label's literal "&" is HTML-escaped to "&amp;" by
        # {{ item.label }} (see the identical fix in test_console_nav_render.py).
        self.assertContains(response, 'Kantin & dompet', html=True)
        self.assertNotContains(response, 'Rekonsiliasi')
