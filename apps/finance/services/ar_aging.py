"""AR Aging Report Service (spec/06 §6, FIN-029).

Implements Accounts Receivable (AR) aging calculation across:
- Aging buckets: CURRENT (not yet due), 0-30 days, 31-60 days, 61-90 days, 90+ days.
- Aggregation levels: Foundation/School summary, School rollup, Class group rollup, and Student details.
"""
import datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional
from django.utils import timezone

from apps.academic.models import ClassEnrollment
from apps.finance.models import Invoice, InvoiceStatus
from apps.identity.models import School


AGING_BUCKETS = ['CURRENT', '0_30', '31_60', '61_90', '90_PLUS']


def get_aging_bucket(due_date: datetime.date, as_of_date: datetime.date) -> str:
    """Returns aging bucket key based on invoice due date relative to reference as_of_date."""
    days = (as_of_date - due_date).days
    if days < 0:
        return 'CURRENT'
    elif days <= 30:
        return '0_30'
    elif days <= 60:
        return '31_60'
    elif days <= 90:
        return '61_90'
    else:
        return '90_PLUS'


def get_ar_aging_report(
    foundation_id: int,
    as_of: Optional[datetime.date] = None,
    as_of_date: Optional[datetime.date] = None,
    school: Optional[School] = None,
    class_group_id: Optional[int] = None,
    student_id: Optional[int] = None,
    school_ids: Optional[Iterable[int]] = None,
) -> Dict[str, Any]:
    """Generates AR aging report across 0-30, 31-60, 61-90, 90+ buckets (FIN-029).

    `school_ids` restricts the report to those schools (school-scoped staff);
    None means no restriction.

    Rolls up outstanding tuition and fees by:
    1. Overall summary across aging buckets.
    2. Rollup by school.
    3. Rollup by class group (rombel).
    4. Student detailed line items.
    """
    ref_date = as_of or as_of_date or timezone.localdate()

    invoices_qs = Invoice.all_tenants.filter(
        foundation_id=foundation_id,
        status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID],
        deleted_at__isnull=True,
    ).select_related('school', 'student', 'student__person')

    if school:
        invoices_qs = invoices_qs.filter(school=school)
    if school_ids is not None:
        invoices_qs = invoices_qs.filter(school_id__in=school_ids)

    if student_id:
        invoices_qs = invoices_qs.filter(student_id=student_id)

    # Pre-fetch active enrollments for students in this foundation
    enrollments_qs = ClassEnrollment.all_tenants.filter(
        foundation_id=foundation_id,
        is_active=True,
        deleted_at__isnull=True,
    ).select_related('class_group')
    if school:
        enrollments_qs = enrollments_qs.filter(student__school=school)
    if school_ids is not None:
        enrollments_qs = enrollments_qs.filter(student__school_id__in=school_ids)

    enrollments = {e.student_id: e.class_group for e in enrollments_qs}

    summary_buckets = {b: Decimal('0.00') for b in AGING_BUCKETS}
    total_outstanding = Decimal('0.00')
    invoices_count = 0

    by_school: Dict[int, Dict[str, Any]] = {}
    by_class: Dict[tuple, Dict[str, Any]] = {}
    by_student: Dict[int, Dict[str, Any]] = {}

    currency = 'IDR'

    for invoice in invoices_qs:
        balance = invoice.balance_due
        if balance <= Decimal('0.00'):
            continue

        student = invoice.student
        class_group = enrollments.get(student.id)
        if class_group_id and (not class_group or class_group.id != class_group_id):
            continue

        if invoice.currency:
            currency = invoice.currency

        bucket = get_aging_bucket(invoice.due_date, ref_date)

        summary_buckets[bucket] += balance
        total_outstanding += balance
        invoices_count += 1

        # 1. Rollup by School
        sch = invoice.school
        if sch.id not in by_school:
            by_school[sch.id] = {
                'school_id': sch.id,
                'school_name': sch.name,
                **{b: Decimal('0.00') for b in AGING_BUCKETS},
                'total_outstanding': Decimal('0.00'),
                'invoices_count': 0,
            }
        by_school[sch.id][bucket] += balance
        by_school[sch.id]['total_outstanding'] += balance
        by_school[sch.id]['invoices_count'] += 1

        # 2. Rollup by Class Group
        cg_id = class_group.id if class_group else None
        cg_name = class_group.name if class_group else "Tanpa Kelas"
        class_key = (sch.id, cg_id)
        if class_key not in by_class:
            by_class[class_key] = {
                'school_id': sch.id,
                'school_name': sch.name,
                'class_group_id': cg_id,
                'class_group_name': cg_name,
                **{b: Decimal('0.00') for b in AGING_BUCKETS},
                'total_outstanding': Decimal('0.00'),
                'invoices_count': 0,
            }
        by_class[class_key][bucket] += balance
        by_class[class_key]['total_outstanding'] += balance
        by_class[class_key]['invoices_count'] += 1

        # 3. Rollup by Student
        if student.id not in by_student:
            by_student[student.id] = {
                'school_id': sch.id,
                'student_id': student.id,
                'student_name': student.person.full_name if hasattr(student, 'person') and student.person else '',
                'nis': student.nis or '',
                'nisn': student.nisn or '',
                'class_group_id': cg_id,
                'class_group_name': cg_name,
                **{b: Decimal('0.00') for b in AGING_BUCKETS},
                'total_outstanding': Decimal('0.00'),
                'invoices_count': 0,
            }
        by_student[student.id][bucket] += balance
        by_student[student.id]['total_outstanding'] += balance
        by_student[student.id]['invoices_count'] += 1

    # Format numbers to string with 2 decimal places
    summary_data = {
        **{b: f"{summary_buckets[b]:.2f}" for b in AGING_BUCKETS},
        'total_outstanding': f"{total_outstanding:.2f}",
        'invoices_count': invoices_count,
    }

    schools_list = [
        {
            'school_id': s['school_id'],
            'school_name': s['school_name'],
            **{b: f"{s[b]:.2f}" for b in AGING_BUCKETS},
            'total_outstanding': f"{s['total_outstanding']:.2f}",
            'invoices_count': s['invoices_count'],
        }
        for s in by_school.values()
    ]

    classes_list = [
        {
            'school_id': c['school_id'],
            'school_name': c['school_name'],
            'class_group_id': c['class_group_id'],
            'class_group_name': c['class_group_name'],
            **{b: f"{c[b]:.2f}" for b in AGING_BUCKETS},
            'total_outstanding': f"{c['total_outstanding']:.2f}",
            'invoices_count': c['invoices_count'],
        }
        for c in by_class.values()
    ]

    students_list = [
        {
            'school_id': st['school_id'],
            'student_id': st['student_id'],
            'student_name': st['student_name'],
            'nis': st['nis'],
            'nisn': st['nisn'],
            'class_group_id': st['class_group_id'],
            'class_group_name': st['class_group_name'],
            **{b: f"{st[b]:.2f}" for b in AGING_BUCKETS},
            'total_outstanding': f"{st['total_outstanding']:.2f}",
            'invoices_count': st['invoices_count'],
        }
        for st in by_student.values()
    ]

    return {
        'as_of': str(ref_date),
        'currency': currency,
        'buckets': AGING_BUCKETS,
        'summary': summary_data,
        'by_school': schools_list,
        'by_class_group': classes_list,
        'by_student': students_list,
    }
