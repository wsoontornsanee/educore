from django.test import TestCase
from django.urls import reverse

from apps.identity.models import Foundation, User


class ComingSoonViewTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.user = User.objects.create(phone_e164='+6281300000003', full_name='U', foundation_id=self.foundation.id)
        self.user.set_password('pw12345')
        self.user.save()

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse('console:coming_soon'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/web/login/', response.url)

    def test_authenticated_gets_200(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('console:coming_soon'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'belum tersedia')
