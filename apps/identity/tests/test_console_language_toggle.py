from django.test import TestCase
from django.urls import reverse
from apps.identity.models import Foundation, User


class ConsoleLanguageToggleTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.user = User.objects.create(phone_e164='+6281300000040', full_name='U', foundation_id=self.foundation.id)
        self.user.set_password('pw12345')
        self.user.save()
        self.client.force_login(self.user)

    def test_console_page_has_language_form_targeting_set_language(self):
        response = self.client.get(reverse('console:coming_soon'))
        self.assertContains(response, reverse('set_language'))
