"""Statutory data export services (spec/14 §4, CMP-017..CMP-022)."""
import re
from abc import ABC, abstractmethod

from apps.academic.models import ClassEnrollment, ClassGroup
from apps.compliance.models import (
    DataSubjectRequest,
    DataSubjectRequestStatus,
    DataSubjectRequestSubjectType,
    StatutoryExportSchema,
    StatutorySystem,
)
from apps.core.services import audit
from apps.identity.models import Person, School, Staff, Student


# --- Field format rules (CMP-017: validated at entry) ---

NISN_PATTERN = re.compile(r'^\d{10}$')
NIK_PATTERN = re.compile(r'^\d{16}$')
NPSN_PATTERN = re.compile(r'^\d{8}$')
NUPTK_PATTERN = re.compile(r'^\d{16}$')


class StatutoryExportError(ValueError):
    """Raised when a statutory export cannot be produced."""


def get_active_schema(foundation_id: int, system: str) -> StatutoryExportSchema:
    """
    Return the active StatutoryExportSchema for a system, or None.
    Falls back to the default field map when no schema row is configured.
    """
    return (
        StatutoryExportSchema.objects.filter(
            foundation_id=foundation_id,
            system=system,
            is_active=True,
        )
        .order_by('-id')
        .first()
    )


def get_default_field_map() -> dict:
    """The built-in field map used when no StatutoryExportSchema row exists."""
    return {
        'students': {
            'nisn': 'nisn',
            'nis': 'nis',
            'person.full_name': 'nama',
            'person.gender': 'jenis_kelamin',
            'person.nik': 'nik',
            'person.dob': 'tanggal_lahir',
            'person.birth_city': 'tempat_lahir',
            'person.religion': 'agama',
            'person.birth_certificate_number': 'nomor_akta_kelahiran',
            'person.citizenship': 'kewarganegaraan',
            'person.address': 'alamat_jalan',
            'person.rt': 'rt',
            'person.rw': 'rw',
            'person.dusun': 'dusun',
            'person.kelurahan': 'kelurahan',
            'person.kecamatan': 'kecamatan',
            'person.kabupaten_kota': 'kabupaten_kota',
            'person.provinsi': 'provinsi',
            'person.postal_code': 'kode_pos',
            'status': 'status',
            'rombel': 'rombel',
        },
        'staff': {
            'nuptk': 'nuptk',
            'nip': 'nip',
            'person.full_name': 'nama',
            'person.gender': 'jenis_kelamin',
            'person.nik': 'nik',
            'person.dob': 'tanggal_lahir',
            'person.birth_city': 'tempat_lahir',
            'person.religion': 'agama',
            'employment_type': 'jenis_ptk',
            'appointment_type': 'kepegawaian',
            'certification_status': 'status_sertifikasi',
            'highest_degree': 'pendidikan_tertinggi',
            'degree_institution': 'institusi',
            'degree_graduation_year': 'tahun_lulus',
            'status': 'status',
        },
        'rombel': {
            'name': 'nama_rombel',
            'grade_level': 'tingkat_pendidikan',
            'homeroom_teacher.person.full_name': 'wali_kelas',
        },
    }


# --- Pre-export validation (CMP-018) ---

def _is_set(value) -> bool:
    return value not in (None, '')


def validate_statutory_export(school: School, system: str) -> dict:
    """
    Pre-export validation report (CMP-018).

    Returns a dict of per-entity issue lists; each issue names the record,
    the missing/invalid field, and — for students — the rombel (class group)
    so operators can fix data by name and class per spec/14 §7 criterion 1.
    """
    issues = {
        'school': [],
        'students': [],
        'staff': [],
        'rombel': [],
    }

    # --- School-level identifiers + statutory profile (CMP-017/019) ---
    if not _is_set(school.npsn):
        issues['school'].append({
            'record': school.name,
            'field': 'npsn',
            'problem': 'NPSN kosong',
        })
    elif not NPSN_PATTERN.match(str(school.npsn)):
        issues['school'].append({
            'record': school.name,
            'field': 'npsn',
            'problem': f'NPSN harus 8 digit angka (dapat: {school.npsn})',
        })

    if not _is_set(school.ownership_status):
        issues['school'].append({
            'record': school.name,
            'field': 'ownership_status',
            'problem': 'Status kepemilikan (negeri/swasta) kosong',
        })

    # EMIS additionally requires the madrasah statistics number.
    if system == StatutorySystem.EMIS and not _is_set(school.nsm):
        issues['school'].append({
            'record': school.name,
            'field': 'nsm',
            'problem': 'NSM kosong (wajib untuk ekspor EMIS)',
        })

    # Structured address completeness: at minimum kecamatan + kabupaten/kota + provinsi.
    missing_address = [
        field for field in ('kecamatan', 'kabupaten_kota', 'provinsi')
        if not _is_set(getattr(school, field, ''))
    ]
    if missing_address:
        issues['school'].append({
            'record': school.name,
            'field': 'address',
            'problem': f'Alamat sekolah belum lengkap: {", ".join(missing_address)}',
        })

    # --- Students: missing NISN, malformed NISN/NIK, no active rombel ---
    enrollments = {}
    for enroll in ClassEnrollment.objects.filter(
        foundation_id=school.foundation_id,
        class_group__school=school,
        is_active=True,
        student__deleted_at__isnull=True,
    ).select_related('class_group', 'student'):
        enrollments[enroll.student_id] = enroll.class_group.name

    students = Student.objects.filter(
        foundation_id=school.foundation_id,
        school=school,
        status=Student.STATUS_ACTIVE,
        deleted_at__isnull=True,
    ).select_related('person').order_by('nis')

    for student in students:
        rombel = enrollments.get(student.id)
        person = student.person

        if not _is_set(student.nisn):
            issues['students'].append({
                'record': person.full_name,
                'rombel': rombel,
                'field': 'nisn',
                'problem': 'NISN kosong',
            })
        elif not NISN_PATTERN.match(str(student.nisn)):
            issues['students'].append({
                'record': person.full_name,
                'rombel': rombel,
                'field': 'nisn',
                'problem': f'NISN harus 10 digit angka (dapat: {student.nisn})',
            })

        if not _is_set(person.nik):
            issues['students'].append({
                'record': person.full_name,
                'rombel': rombel,
                'field': 'person.nik',
                'problem': 'NIK kosong',
            })
        elif not NIK_PATTERN.match(str(person.nik)):
            issues['students'].append({
                'record': person.full_name,
                'rombel': rombel,
                'field': 'person.nik',
                'problem': f'NIK harus 16 digit angka (dapat: {person.nik})',
            })

        if not _is_set(person.religion):
            issues['students'].append({
                'record': person.full_name,
                'rombel': rombel,
                'field': 'person.religion',
                'problem': 'Agama kosong',
            })

        if rombel is None:
            issues['students'].append({
                'record': person.full_name,
                'rombel': None,
                'field': 'rombel',
                'problem': 'Siswa aktif tanpa rombel (belum ditempatkan di kelas)',
            })

    # --- Staff: missing/malformed NUPTK for teaching staff, NIK ---
    staff_qs = Staff.objects.filter(
        foundation_id=school.foundation_id,
        status=Staff.STATUS_ACTIVE,
        deleted_at__isnull=True,
    ).filter(
        # Teaching staff attached to this school, or foundation-wide staff
        # that plausibly teaches here. School-scoped staff at sibling schools
        # are excluded.
        models_q_school(school),
    ).select_related('person').order_by('person__full_name')

    for staff in staff_qs:
        person = staff.person
        if not _is_set(staff.nuptk):
            issues['staff'].append({
                'record': person.full_name,
                'field': 'nuptk',
                'problem': 'NUPTK kosong',
            })
        elif not NUPTK_PATTERN.match(str(staff.nuptk)):
            issues['staff'].append({
                'record': person.full_name,
                'field': 'nuptk',
                'problem': f'NUPTK harus 16 digit angka (dapat: {staff.nuptk})',
            })

        if not _is_set(person.nik):
            issues['staff'].append({
                'record': person.full_name,
                'field': 'person.nik',
                'problem': 'NIK kosong',
            })
        elif not NIK_PATTERN.match(str(person.nik)):
            issues['staff'].append({
                'record': person.full_name,
                'field': 'person.nik',
                'problem': f'NIK harus 16 digit angka (dapat: {person.nik})',
            })

        if not _is_set(staff.appointment_type):
            issues['staff'].append({
                'record': person.full_name,
                'field': 'appointment_type',
                'problem': 'Jenis pengangkatan (CPNS/PNS/PPPK/GTY/PTT) kosong',
            })

    # --- Rombel: must have a homeroom teacher ---
    rombels = ClassGroup.objects.filter(
        foundation_id=school.foundation_id,
        school=school,
        deleted_at__isnull=True,
    ).select_related('homeroom_teacher__person')

    for rombel in rombels:
        if rombel.homeroom_teacher_id is None:
            issues['rombel'].append({
                'record': rombel.name,
                'field': 'homeroom_teacher',
                'problem': 'Rombel tanpa wali kelas',
            })

    return issues


def models_q_school(school):
    """Q filter for staff belonging to this school (or foundation-wide)."""
    from django.db.models import Q
    return Q(school=school) | Q(school__isnull=True)


# --- Exporter adapter interface (CMP-021) ---

class StatutoryExporter(ABC):
    """
    Adapter interface for statutory export bundles (CMP-021).

    File export is the v1 implementation; when an official ministry API
    becomes available, an API-sync subclass swaps in without touching the
    domain models or callers.
    """

    system: str = None

    def __init__(self, school: School, schema: StatutoryExportSchema = None):
        self.school = school
        self.schema = schema

    @abstractmethod
    def build_bundle(self) -> dict:
        """
        Produce the export bundle. Returns a dict::

            {
                'sheets': {'students': [row, ...], 'staff': [...], 'rombel': [...]},
                'metadata': {'system': ..., 'version': ..., 'school': ..., 'generated_at': ...},
            }
        """

    def validate(self) -> dict:
        """Pre-export validation report (CMP-018). Delegates to the service."""
        return validate_statutory_export(self.school, self.system)


def _resolve(obj, path: str):
    """Resolve a dotted field path like 'person.full_name' or 'rombel'."""
    parts = path.split('.')
    current = obj
    for part in parts:
        if current is None:
            return None
        if part == 'rombel':
            # Virtual field: active class group name for a student
            enrollment = ClassEnrollment.objects.filter(
                foundation_id=current.foundation_id,
                student=current,
                is_active=True,
            ).select_related('class_group').first()
            return enrollment.class_group.name if enrollment else None
        current = getattr(current, part, None)
    return current


class FileStatutoryExporter(StatutoryExporter):
    """
    v1 file-export implementation: rows as dicts keyed by statutory column
    names, driven by the schema's field_map (CMP-020).
    """

    def build_bundle(self) -> dict:
        field_map = (
            self.schema.field_map
            if self.schema and self.schema.field_map
            else get_default_field_map()
        )

        sheets = {
            'students': self._build_students_sheet(field_map.get('students', {})),
            'staff': self._build_staff_sheet(field_map.get('staff', {})),
            'rombel': self._build_rombel_sheet(field_map.get('rombel', {})),
        }

        return {
            'sheets': sheets,
            'metadata': {
                'system': self.system,
                'schema_version': self.schema.version if self.schema else 'default',
                'school': self.school.name,
                'npsn': self.school.npsn,
            },
        }

    def _build_students_sheet(self, mapping: dict) -> list:
        enrollments = {}
        for enroll in ClassEnrollment.objects.filter(
            foundation_id=self.school.foundation_id,
            class_group__school=self.school,
            is_active=True,
            student__deleted_at__isnull=True,
        ).select_related('class_group'):
            enrollments[enroll.student_id] = enroll.class_group.name

        rows = []
        students = Student.objects.filter(
            foundation_id=self.school.foundation_id,
            school=self.school,
            status=Student.STATUS_ACTIVE,
            deleted_at__isnull=True,
        ).select_related('person').order_by('nis')

        for student in students:
            row = {}
            for edu_path, column in mapping.items():
                if edu_path == 'rombel':
                    row[column] = enrollments.get(student.id)
                else:
                    value = _resolve(student, edu_path)
                    row[column] = str(value) if value is not None else None
            rows.append(row)
        return rows

    def _build_staff_sheet(self, mapping: dict) -> list:
        rows = []
        staff_qs = Staff.objects.filter(
            foundation_id=self.school.foundation_id,
            status=Staff.STATUS_ACTIVE,
            deleted_at__isnull=True,
        ).filter(
            models_q_school(self.school),
        ).select_related('person').order_by('person__full_name')

        for staff in staff_qs:
            row = {}
            for edu_path, column in mapping.items():
                value = _resolve(staff, edu_path)
                row[column] = str(value) if value is not None else None
            rows.append(row)
        return rows

    def _build_rombel_sheet(self, mapping: dict) -> list:
        rows = []
        rombels = ClassGroup.objects.filter(
            foundation_id=self.school.foundation_id,
            school=self.school,
            deleted_at__isnull=True,
        ).select_related('homeroom_teacher__person').order_by('grade_level', 'name')

        for rombel in rombels:
            row = {}
            for edu_path, column in mapping.items():
                value = _resolve(rombel, edu_path)
                row[column] = str(value) if value is not None else None
            rows.append(row)
        return rows


class DapodikFileExporter(FileStatutoryExporter):
    """DAPODIK (Kemendikbudristek) file export (CMP-018)."""
    system = StatutorySystem.DAPODIK


class EmisFileExporter(FileStatutoryExporter):
    """EMIS (Kemenag) file export for madrasah schools (CMP-019)."""
    system = StatutorySystem.EMIS


EXPORTERS = {
    StatutorySystem.DAPODIK: DapodikFileExporter,
    StatutorySystem.EMIS: EmisFileExporter,
}


def get_exporter(system: str, school: School) -> StatutoryExporter:
    """Resolve the exporter adapter for a system (CMP-021 swap point)."""
    exporter_cls = EXPORTERS.get(system)
    if exporter_cls is None:
        raise StatutoryExportError(f"Unknown statutory system: {system}")
    return exporter_cls(school=school, schema=get_active_schema(school.foundation_id, system))


# --- DSAR access export bundle (CMP-011) ---

class PersonNotFoundError(ValueError):
    """Raised when a DSAR request targets a subject that doesn't exist in this foundation."""


def _serialize_date(value):
    return value.isoformat() if value else None


def collect_person_data_bundle(subject_type: str, subject_id: int, foundation_id: int) -> dict:
    """DSAR access-export bundle (CMP-011): Identity + Academic + Attendance +
    Finance for one Student or Staff. Notifications/wallet/campus-life data
    are a documented follow-up, not pulled in here. Shape is sheet-ready:
    every key except 'identity' is a flat list of row-dicts, consumed
    directly by apps/compliance/exports.py's dsar_access renderer."""
    from apps.attendance.models import AttendanceDay, PeriodAttendance
    from apps.finance.models import Invoice, Payment

    if subject_type == 'STUDENT':
        student = Student.objects.filter(foundation_id=foundation_id, id=subject_id).select_related('person').first()
        if student is None:
            raise PersonNotFoundError(f"Student {subject_id} not found in foundation {foundation_id}")
        person = student.person

        from apps.academic.models import ReportCard

        academic_enrollments = [
            {
                'class_group': e.class_group.name,
                'enrolled_at': _serialize_date(e.enrolled_at),
                'is_active': e.is_active,
            }
            for e in ClassEnrollment.objects.filter(student=student).select_related('class_group')
        ]
        report_cards = [
            {
                'term_id': rc.term_id,
                'status': rc.status,
                'published_at': rc.published_at.isoformat() if rc.published_at else None,
            }
            for rc in ReportCard.objects.filter(student=student, is_current=True)
        ]
        attendance_days = [
            {'date': _serialize_date(a.date), 'status': a.status}
            for a in AttendanceDay.objects.filter(student=student).order_by('date')
        ]
        period_attendances = [
            {'date': _serialize_date(p.date), 'status': p.status, 'source': p.source}
            for p in PeriodAttendance.objects.filter(student=student).order_by('date')
        ]
        invoices = [
            {
                'number': inv.number, 'period': inv.period, 'total': str(inv.total),
                'currency': inv.currency, 'status': inv.status,
            }
            for inv in Invoice.objects.filter(student=student)
        ]
        payments = [
            {
                'reference': p.reference, 'amount': str(p.amount), 'currency': p.currency,
                'status': p.status, 'paid_at': p.paid_at.isoformat() if p.paid_at else None,
            }
            for p in Payment.objects.filter(student=student)
        ]

        return {
            'identity': {
                'subject_type': 'STUDENT', 'full_name': person.full_name, 'nik': person.nik,
                'dob': _serialize_date(person.dob), 'gender': person.gender, 'address': person.address,
                'nis': student.nis, 'nisn': student.nisn, 'status': student.status,
            },
            'academic_enrollments': academic_enrollments,
            'academic_report_cards': report_cards,
            'attendance_days': attendance_days,
            'period_attendances': period_attendances,
            'invoices': invoices,
            'payments': payments,
        }

    if subject_type == 'STAFF':
        staff = Staff.objects.filter(foundation_id=foundation_id, id=subject_id).select_related('person').first()
        if staff is None:
            raise PersonNotFoundError(f"Staff {subject_id} not found in foundation {foundation_id}")
        person = staff.person
        return {
            'identity': {
                'subject_type': 'STAFF', 'full_name': person.full_name, 'nik': person.nik,
                'dob': _serialize_date(person.dob), 'gender': person.gender, 'address': person.address,
                'nip': staff.nip, 'nuptk': staff.nuptk, 'status': staff.status,
            },
            'academic_enrollments': [],
            'academic_report_cards': [],
            'attendance_days': [],
            'period_attendances': [],
            'invoices': [],
            'payments': [],
        }

    raise PersonNotFoundError(f"Unknown subject_type: {subject_type}")


# --- Right to erasure (CMP-012) ---

class PersonNotErasableError(ValueError):
    """Raised when erasure is requested for a subject that is not yet in a
    departed/terminal status (CMP-012: "processing erasure requests for
    departed students/staff"), or that doesn't exist."""


_STUDENT_ERASABLE_STATUSES = {Student.STATUS_GRADUATED, Student.STATUS_TRANSFERRED_OUT}
_STAFF_ERASABLE_STATUSES = {Staff.STATUS_OFFBOARDED}


def _anonymize_person(person) -> None:
    person.full_name = f"[ERASED-{person.id}]"
    person.nik = None
    person.dob = None
    person.address = ''
    person.birth_city = ''
    person.birth_certificate_number = ''
    person.religion = ''
    person.rt = ''
    person.rw = ''
    person.dusun = ''
    person.kelurahan = ''
    person.kecamatan = ''
    person.kabupaten_kota = ''
    person.provinsi = ''
    person.postal_code = ''
    person.save()


def _anonymize_user(user) -> None:
    """Anonymize the identifying PII on a Staff member's linked login account.

    Only identity fields — is_active/status/permissions are deliberately left
    alone: account deactivation is a separate lifecycle concern from erasure.
    phone_e164 is UNIQUE and NOT NULL, so it gets an id-based placeholder
    rather than a bare empty string (which would collide on the second erasure).
    """
    if user is None:
        return
    user.full_name = f"[ERASED-{user.id}]"
    user.phone_e164 = f"[ERASED-{user.id}]"
    user.email = None
    user.save(update_fields=['full_name', 'phone_e164', 'email'])


def _erase_subject_photos(subject_type, subject) -> None:
    """Blank the erased subject's facial imagery immediately (CMP-012).

    The CMP-013 retention sweeper only purges gate photos past their own
    90-day window; an accepted erasure request must not wait for it.
    all_tenants is used deliberately: erase_person may run outside any
    thread-local tenant context (management commands, services), and the
    queryset is already pinned to this one subject's rows.
    """
    from apps.attendance.models import GateEvent

    if subject_type == DataSubjectRequestSubjectType.STUDENT:
        if getattr(subject, 'photo_key', ''):
            subject.photo_key = ''
            subject.save(update_fields=['photo_key'])
        GateEvent.all_tenants.filter(student=subject).exclude(photo_key='').update(photo_key='')
    else:
        GateEvent.all_tenants.filter(staff=subject).exclude(photo_key='').update(photo_key='')


def erase_person(subject_type, subject_id, foundation_id, requested_by, requested_by_name) -> DataSubjectRequest:
    """CMP-012 right to erasure. Refuses on anyone not already departed, or
    unknown. Anonymizes the Person PII-vault row only — Student/Staff/
    academic/financial rows keep their FK to the now-anonymized Person, so
    retention obligations (10yr financial, permanent-default academic) are
    preserved with zero special-casing."""
    if subject_type == DataSubjectRequestSubjectType.STUDENT:
        subject = Student.objects.filter(foundation_id=foundation_id, id=subject_id).select_related('person').first()
        erasable_statuses = _STUDENT_ERASABLE_STATUSES
    elif subject_type == DataSubjectRequestSubjectType.STAFF:
        subject = (
            Staff.objects.filter(foundation_id=foundation_id, id=subject_id)
            .select_related('person', 'user')
            .first()
        )
        erasable_statuses = _STAFF_ERASABLE_STATUSES
    else:
        subject = None
        erasable_statuses = set()

    if subject is None:
        DataSubjectRequest.objects.create(
            foundation_id=foundation_id, subject_type=subject_type, subject_id=subject_id,
            status=DataSubjectRequestStatus.REFUSED, requested_by=requested_by,
            requested_by_name=requested_by_name,
            refusal_reason=f"{subject_type} {subject_id} tidak ditemukan di yayasan {foundation_id}.",
        )
        raise PersonNotErasableError(f"{subject_type} {subject_id} not found in foundation {foundation_id}")

    if subject.status not in erasable_statuses:
        refusal_reason = (
            f"Tidak dapat menghapus data: status saat ini '{subject.status}' bukan status "
            f"keluar/lulus (CMP-012 hanya mengizinkan penghapusan untuk yang sudah keluar)."
        )
        DataSubjectRequest.objects.create(
            foundation_id=foundation_id, subject_type=subject_type, subject_id=subject_id,
            status=DataSubjectRequestStatus.REFUSED, requested_by=requested_by,
            requested_by_name=requested_by_name, refusal_reason=refusal_reason,
        )
        raise PersonNotErasableError(refusal_reason)

    person = subject.person
    person_id = person.id
    _anonymize_person(person)
    _erase_subject_photos(subject_type, subject)
    if subject_type == DataSubjectRequestSubjectType.STAFF:
        _anonymize_user(subject.user)

    audit(
        action='compliance.person.erase',
        entity_type='Person',
        entity_id=str(person_id),
        actor_id=requested_by,
        foundation_id=foundation_id,
        diff={'erased': True, 'subject_type': subject_type, 'subject_id': subject_id},
    )

    return DataSubjectRequest.objects.create(
        foundation_id=foundation_id, subject_type=subject_type, subject_id=subject_id,
        status=DataSubjectRequestStatus.COMPLETED, requested_by=requested_by,
        requested_by_name=requested_by_name,
    )
