from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.identity.models import Foundation, PlatformRoleAssignment, User
from apps.status.models import ServiceComponent, StatusIncident


class StatusManageAccessTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='Test Foundation', brand_name='Test Foundation')
        self.component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        self.operator = User.objects.create(
            phone_e164='+6281200000008', full_name='Op', is_active=True, foundation_id=self.foundation.id,
        )
        self.operator.set_password('pw12345')
        self.operator.save()
        PlatformRoleAssignment.objects.create(user=self.operator, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR)
        self.plain_user = User.objects.create(
            phone_e164='+6281200000009', full_name='Plain', is_active=True, foundation_id=self.foundation.id,
        )
        self.plain_user.set_password('pw12345')
        self.plain_user.save()

    def test_anonymous_denied(self):
        response = self.client.get(reverse('status_manage:page'))
        self.assertIn(response.status_code, (401, 403))

    def test_non_operator_denied(self):
        self.client.force_login(self.plain_user)
        response = self.client.get(reverse('status_manage:page'))
        self.assertEqual(response.status_code, 403)

    def test_operator_allowed(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse('status_manage:page'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'C1')


class StatusComponentUpdateViewTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='Test Foundation', brand_name='Test Foundation')
        self.component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        self.operator = User.objects.create(phone_e164='+6281200000010', full_name='Op', foundation_id=self.foundation.id)
        self.operator.set_password('pw12345')
        self.operator.save()
        PlatformRoleAssignment.objects.create(user=self.operator, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR)
        self.client.force_login(self.operator)

    def test_set_manual_status(self):
        url = reverse('status_manage:component-update', args=[self.component.id])
        response = self.client.post(url, {'manual_status': ServiceComponent.STATUS_DEGRADED})
        self.assertEqual(response.status_code, 302)
        self.component.refresh_from_db()
        self.assertEqual(self.component.manual_status, ServiceComponent.STATUS_DEGRADED)

    def test_clear_manual_status(self):
        self.component.manual_status = ServiceComponent.STATUS_DOWN
        self.component.save()
        url = reverse('status_manage:component-update', args=[self.component.id])
        response = self.client.post(url, {'manual_status': ''})
        self.assertEqual(response.status_code, 302)
        self.component.refresh_from_db()
        self.assertIsNone(self.component.manual_status)

    def test_invalid_manual_status_rejected(self):
        # Fix 3: a bogus staff typo must not be able to corrupt the row that
        # later feeds BAR_COLORS[status] on the public page.
        url = reverse('status_manage:component-update', args=[self.component.id])
        response = self.client.post(url, {'manual_status': 'NOT_A_REAL_STATUS'})
        self.assertEqual(response.status_code, 400)
        self.component.refresh_from_db()
        self.assertIsNone(self.component.manual_status)


class StatusIncidentManageViewTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='Test Foundation', brand_name='Test Foundation')
        self.operator = User.objects.create(phone_e164='+6281200000011', full_name='Op', foundation_id=self.foundation.id)
        self.operator.set_password('pw12345')
        self.operator.save()
        PlatformRoleAssignment.objects.create(user=self.operator, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR)
        self.client.force_login(self.operator)

    def test_create_incident(self):
        url = reverse('status_manage:incident-create')
        response = self.client.post(url, {
            'severity': StatusIncident.SEVERITY_MINOR,
            'title_id': 'Judul', 'title_en': 'Title',
            'body_id': 'Isi', 'body_en': 'Body',
            'duration_minutes': '15',
            'published': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StatusIncident.objects.count(), 1)
        incident = StatusIncident.objects.first()
        self.assertEqual(incident.created_by, self.operator)

    def test_missing_duration_minutes_rejected(self):
        url = reverse('status_manage:incident-create')
        response = self.client.post(url, {
            'severity': StatusIncident.SEVERITY_MINOR,
            'title_id': 'Judul', 'title_en': 'Title',
            'body_id': 'Isi', 'body_en': 'Body',
            'published': 'on',
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(StatusIncident.objects.count(), 0)

    def test_invalid_duration_minutes_rejected(self):
        url = reverse('status_manage:incident-create')
        response = self.client.post(url, {
            'severity': StatusIncident.SEVERITY_MINOR,
            'title_id': 'Judul', 'title_en': 'Title',
            'body_id': 'Isi', 'body_en': 'Body',
            'duration_minutes': 'not-a-number',
            'published': 'on',
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(StatusIncident.objects.count(), 0)

    def test_invalid_severity_rejected(self):
        url = reverse('status_manage:incident-create')
        response = self.client.post(url, {
            'severity': 'NOT_A_REAL_SEVERITY',
            'title_id': 'Judul', 'title_en': 'Title',
            'body_id': 'Isi', 'body_en': 'Body',
            'duration_minutes': '15',
            'published': 'on',
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(StatusIncident.objects.count(), 0)

    def test_invalid_affected_component_id_rejected(self):
        # IncidentCreateForm's ModelMultipleChoiceField must reject an id
        # that doesn't correspond to a real ServiceComponent, rather than
        # raising an unhandled ValueError/DoesNotExist further down.
        url = reverse('status_manage:incident-create')
        response = self.client.post(url, {
            'severity': StatusIncident.SEVERITY_MINOR,
            'title_id': 'Judul', 'title_en': 'Title',
            'body_id': 'Isi', 'body_en': 'Body',
            'duration_minutes': '15',
            'affected_components': ['999999'],
            'published': 'on',
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(StatusIncident.objects.count(), 0)

    def test_create_incident_with_affected_components(self):
        component = ServiceComponent.objects.create(key='c2', name_id='C2', name_en='C2')
        url = reverse('status_manage:incident-create')
        response = self.client.post(url, {
            'severity': StatusIncident.SEVERITY_MINOR,
            'title_id': 'Judul', 'title_en': 'Title',
            'body_id': 'Isi', 'body_en': 'Body',
            'duration_minutes': '15',
            'affected_components': [str(component.id)],
            'published': 'on',
        })
        self.assertEqual(response.status_code, 302)
        incident = StatusIncident.objects.get()
        self.assertIn(component, incident.affected_components.all())

    def test_manage_page_incident_row_has_publish_toggle_form(self):
        # Fix: the publish-toggle view existed but was unreachable from the
        # UI — manage.html rendered incidents as a read-only table with no
        # form pointing at status_manage:incident-update.
        incident = StatusIncident.objects.create(
            severity=StatusIncident.SEVERITY_MINOR, title_id='Judul', title_en='Title',
            body_id='Isi', body_en='Body', occurred_at=timezone.now(),
            duration_minutes=5, published=False,
        )
        response = self.client.get(reverse('status_manage:page'))
        self.assertContains(response, reverse('status_manage:incident-update', args=[incident.id]))

    def test_toggle_publish(self):
        incident = StatusIncident.objects.create(
            severity=StatusIncident.SEVERITY_MINOR, title_id='A', title_en='A',
            body_id='A', body_en='A', occurred_at=timezone.now(), duration_minutes=5, published=False,
        )
        url = reverse('status_manage:incident-update', args=[incident.id])
        response = self.client.post(url, {'published': 'on'})
        self.assertEqual(response.status_code, 302)
        incident.refresh_from_db()
        self.assertTrue(incident.published)
        self.assertEqual(incident.updated_by, self.operator)
