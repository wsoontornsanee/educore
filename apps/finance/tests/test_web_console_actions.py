"""Keuangan console write actions (POST views): gate, tenancy, scope, service wiring."""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.finance.tests.test_web_console import make_foundation, make_invoice, make_student, make_user
from apps.identity.models import RoleAssignment
from educore.middleware.tenancy import clear_current_foundation_id


def flashes(response):
    """Flash message texts on a followed response."""
    return [str(message) for message in response.context['messages']]


class ActionTestBase(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation, (self.school1, self.school2) = make_foundation('A')
        self.s1 = make_student(self.foundation, self.school1, 'Budi Satu', '0001')
        self.s2 = make_student(self.foundation, self.school2, 'Sari Dua', '0002')
        self.admin = make_user(
            self.foundation, '+6281300008001', RoleAssignment.ROLE_FOUNDATION_ADMIN,
            RoleAssignment.SCOPE_FOUNDATION, self.foundation.id, staff_school=self.school1, name='Admin',
        )
        self.officer = make_user(
            self.foundation, '+6281300008002', RoleAssignment.ROLE_FINANCE_OFFICER,
            RoleAssignment.SCOPE_FOUNDATION, self.foundation.id, staff_school=self.school1, name='Officer',
        )
        self.scoped_officer = make_user(
            self.foundation, '+6281300008003', RoleAssignment.ROLE_FINANCE_OFFICER,
            RoleAssignment.SCOPE_SCHOOL, self.school1.id, staff_school=self.school1, name='Scoped',
        )
