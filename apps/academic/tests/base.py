import datetime
from django.utils import timezone

from apps.identity.models import Foundation, Person, School, Staff, Student, User
from apps.academic.models import AcademicYear, ClassGroup, ClassSubject, Subject, Term
from educore.middleware.tenancy import set_current_foundation_id


def build_academic_fixture(foundation_name="Yayasan Cendekia Mandiri"):
    """Shared fixture: foundation/school/academic-year/term/class/teacher/student."""
    foundation = Foundation.objects.create(
        legal_name=foundation_name,
        brand_name=foundation_name,
        npwp="01.111.222.3-000.000",
        address="Jakarta",
    )
    set_current_foundation_id(foundation.id)

    npsn_suffix = str(abs(hash(foundation_name)) % 100000).zfill(5)
    school = School.all_tenants.create(
        foundation_id=foundation.id,
        name="SMA Cendekia Mandiri",
        npsn=f"209{npsn_suffix}",
        level=School.LEVEL_SMA,
    )

    academic_year = AcademicYear.objects.create(
        foundation_id=foundation.id,
        school=school,
        name="2026/2027",
        start_date=datetime.date(2026, 7, 1),
        end_date=datetime.date(2027, 6, 30),
    )

    term = Term.objects.create(
        foundation_id=foundation.id,
        academic_year=academic_year,
        name="Semester 1 (Ganjil)",
        term_no=1,
        start_date=datetime.date(2026, 7, 1),
        end_date=datetime.date(2026, 12, 20),
    )

    teacher_person = Person.all_tenants.create(
        foundation_id=foundation.id, nik="3471010101010101", full_name="Bu Siti Rahayu"
    )
    teacher_user = User.objects.create(
        foundation_id=foundation.id,
        phone_e164=f"+62811{npsn_suffix}",
        email=f"siti.{npsn_suffix}@cendekia.sch.id",
        full_name="Bu Siti Rahayu",
    )
    teacher = Staff.all_tenants.create(
        foundation_id=foundation.id,
        person=teacher_person,
        user=teacher_user,
        school=school,
        nip="198501012010011001",
        employment_type=Staff.TYPE_PERMANENT,
        join_date=datetime.date(2020, 1, 1),
        status=Staff.STATUS_ACTIVE,
    )

    class_group = ClassGroup.objects.create(
        foundation_id=foundation.id,
        school=school,
        academic_year=academic_year,
        grade_level=10,
        name="X IPA 1",
        homeroom_teacher=teacher,
    )

    subject = Subject.objects.create(
        foundation_id=foundation.id, school=school, code="MTK", name="Matematika", credit_hours=4,
    )

    class_subject = ClassSubject.objects.create(
        foundation_id=foundation.id,
        class_group=class_group,
        subject=subject,
        teacher=teacher,
        term=term,
    )

    student_person = Person.all_tenants.create(
        foundation_id=foundation.id, nik="3471010101010202", full_name="Andi Wijaya"
    )
    student = Student.all_tenants.create(
        foundation_id=foundation.id,
        school=school,
        person=student_person,
        nisn="1122334455",
        nis="X-001",
        status=Student.STATUS_ACTIVE,
    )

    return {
        'foundation': foundation,
        'school': school,
        'academic_year': academic_year,
        'term': term,
        'teacher': teacher,
        'teacher_user': teacher_user,
        'class_group': class_group,
        'subject': subject,
        'class_subject': class_subject,
        'student': student,
    }


def enroll_in_class_of(staff, student, name='Kelas Uji'):
    """Put `student` in a class that `staff` is homeroom teacher of, so a
    teacher-only user sees them under the per-teacher class scope."""
    from apps.academic.models import AcademicYear, ClassEnrollment, ClassGroup

    foundation_id = student.foundation_id
    year = AcademicYear.all_tenants.filter(foundation_id=foundation_id, school_id=student.school_id).first()
    if year is None:
        year = AcademicYear.all_tenants.create(
            foundation_id=foundation_id, school_id=student.school_id, name='2026/2027',
            start_date=datetime.date(2026, 7, 1), end_date=datetime.date(2027, 6, 30),
        )
    class_group = ClassGroup.all_tenants.filter(
        foundation_id=foundation_id, school_id=student.school_id, homeroom_teacher=staff, name=name,
    ).first() or ClassGroup.all_tenants.create(
        foundation_id=foundation_id, school_id=student.school_id, academic_year=year, grade_level=10,
        name=name, homeroom_teacher=staff,
    )
    ClassEnrollment.all_tenants.create(
        foundation_id=foundation_id, student=student, class_group=class_group,
        enrolled_at=datetime.date(2026, 7, 1), is_active=True,
    )
    return class_group
