"""Administrasi console page for the metering statement (spec/15 RPT-009), mounted under /web/admin/metering/.

A session-auth HTML surface over `get_metering_statement`, the same service the JSON statement API uses, so the
page and the API can never disagree about what a count means or which schools a caller may see.
"""
import datetime as _dt

from django.http import Http404
from django.utils import timezone
from django.views.generic import TemplateView

from apps.identity.console_access import (
    ConsolePermissionMixin, accessible_school_ids, accessible_school_ids_for_all,
)
from apps.identity.models import School
from apps.reporting.services import (
    METERING_FROZEN, METERING_NOT_COMPUTED, METERING_OPEN, get_metering_roster, get_metering_statement,
)


def _shift_month(month, delta):
    index = month.year * 12 + (month.month - 1) + delta
    return _dt.date(index // 12, index % 12 + 1, 1)


class MeteringStatementPageView(ConsolePermissionMixin, TemplateView):
    """Siswa terhitung: the counted students per school for one month, as the subscription invoice basis reads
    them. A viewer whose `reporting.read` is school-scoped sees only their own schools' counts."""
    template_name = 'pages/admin_metering.html'
    required_permission = 'reporting.read'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # The API's own "current month" (UTC date), so the page and GET /metering/statements/ agree.
        current_month = timezone.now().date().replace(day=1)
        month, invalid_month = current_month, False
        raw = self.request.GET.get('month', '').strip()
        if raw:
            try:
                month = _dt.datetime.strptime(raw, '%Y-%m').date()
            except ValueError:
                invalid_month = True  # fall back to the current month and say so, never a blank page

        ceiling = accessible_school_ids(self.request.user, self.foundation_id, self.required_permission)
        statement = get_metering_statement(self.foundation_id, month, school_ids=ceiling)

        # A roster lists students (NIS), so its link needs `student_records.read` for that school as well.
        roster_ceiling = accessible_school_ids_for_all(
            self.request.user, self.foundation_id, self.required_permission, 'student_records.read',
        )
        for entry in statement['schools']:
            entry['roster_link'] = entry['roster_available'] and (
                roster_ceiling is None or entry['school_id'] in roster_ceiling
            )

        ctx.update({
            'statement': statement,
            'month_value': statement['month'],
            'invalid_month': invalid_month,
            'previous_month': _shift_month(month, -1).strftime('%Y-%m'),
            'next_month': _shift_month(month, 1).strftime('%Y-%m') if month < current_month else None,
            'state_open': METERING_OPEN,
            'state_frozen': METERING_FROZEN,
            'state_not_computed': METERING_NOT_COMPUTED,
            'uncounted': sum(1 for entry in statement['schools'] if entry['state'] == METERING_NOT_COMPUTED),
        })
        return ctx


class MeteringRosterPageView(ConsolePermissionMixin, TemplateView):
    """Siswa terhitung > daftar: WHICH students one school's count for a month was made of, so an invoice dispute
    can be settled (RPT-009). Lists name, NIS and current status; never NISN. It is a sub-page of the statement,
    reached from its rows, so it is not in the navigation. Needs `reporting.read` and `student_records.read` for
    the school: a school outside the viewer's scope reads as not found."""
    template_name = 'pages/admin_metering_roster.html'
    required_permission = 'reporting.read'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        raw_school = self.request.GET.get('school_id', '').strip()
        school = School.objects.filter(id=raw_school, foundation_id=self.foundation_id).first() if raw_school.isdigit() else None
        allowed = accessible_school_ids_for_all(
            self.request.user, self.foundation_id, self.required_permission, 'student_records.read',
        )
        if school is None or (allowed is not None and school.id not in allowed):
            raise Http404("school not found")

        current_month = timezone.now().date().replace(day=1)
        month, invalid_month = current_month, False
        raw = self.request.GET.get('month', '').strip()
        if raw:
            try:
                month = _dt.datetime.strptime(raw, '%Y-%m').date()
            except ValueError:
                invalid_month = True

        roster = get_metering_roster(self.foundation_id, school, month)
        ctx.update({
            'roster': roster,
            'school': school,
            'invalid_month': invalid_month,
            'state_open': METERING_OPEN,
            'state_frozen': METERING_FROZEN,
            'state_not_computed': METERING_NOT_COMPUTED,
            'back_month': roster['month'],
        })
        return ctx
