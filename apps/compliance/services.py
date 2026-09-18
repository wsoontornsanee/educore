"""Statutory data export services (spec/14 §4, CMP-017..CMP-022)."""
import re
from abc import ABC, abstractmethod

from apps.academic.models import ClassEnrollment, ClassGroup
from apps.compliance.models import StatutoryExportSchema, StatutorySystem
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
            'person.mother_name': 'nama_ibu_kandung',
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
            'person.home_latitude': 'lintang',
            'person.home_longitude': 'bujur',
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
            'person.home_latitude': 'lintang',
            'person.home_longitude': 'bujur',
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

MANDATORY_FIELD_LABELS = {
    'person.mother_name': 'Nama ibu kandung kosong (wajib per skema ekspor)',
    'person.home_latitude': 'Koordinat lintang tempat tinggal kosong (wajib per skema ekspor)',
    'person.home_longitude': 'Koordinat bujur tempat tinggal kosong (wajib per skema ekspor)',
    'person.birth_city': 'Tempat lahir kosong (wajib per skema ekspor)',
    'person.birth_certificate_number': 'Nomor akta kelahiran kosong (wajib per skema ekspor)',
    'person.citizenship': 'Kewarganegaraan kosong (wajib per skema ekspor)',
    'person.address': 'Alamat jalan kosong (wajib per skema ekspor)',
    'person.rt': 'RT kosong (wajib per skema ekspor)',
    'person.rw': 'RW kosong (wajib per skema ekspor)',
    'person.kelurahan': 'Kelurahan kosong (wajib per skema ekspor)',
    'person.kecamatan': 'Kecamatan kosong (wajib per skema ekspor)',
    'person.kabupaten_kota': 'Kabupaten/Kota kosong (wajib per skema ekspor)',
    'person.provinsi': 'Provinsi kosong (wajib per skema ekspor)',
    'person.postal_code': 'Kode pos kosong (wajib per skema ekspor)',
}


def _is_set(value) -> bool:
    return value not in (None, '')


def validate_statutory_export(
    school: School,
    system: str,
    schema: StatutoryExportSchema = None,
) -> dict:
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

    # --- Active schema resolution & configurable mandatory fields (CMP-020) ---
    if schema is None:
        schema = get_active_schema(school.foundation_id, system)

    mandatory_cfg = {}
    if schema:
        if getattr(schema, 'mandatory_fields', None) and isinstance(schema.mandatory_fields, dict):
            mandatory_cfg.update(schema.mandatory_fields)
        if isinstance(getattr(schema, 'field_map', None), dict) and 'mandatory_fields' in schema.field_map:
            mandatory_cfg.update(schema.field_map['mandatory_fields'])

    mandatory_student_fields = [
        f for f in mandatory_cfg.get('students', [])
        if f not in ('nisn', 'person.nik', 'person.religion', 'rombel')
    ]
    mandatory_staff_fields = [
        f for f in mandatory_cfg.get('staff', [])
        if f not in ('nuptk', 'person.nik', 'appointment_type')
    ]

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

        for field_path in mandatory_student_fields:
            val = _resolve(student, field_path)
            if not _is_set(val):
                problem = MANDATORY_FIELD_LABELS.get(
                    field_path,
                    f'{field_path} kosong (wajib per skema ekspor)',
                )
                issues['students'].append({
                    'record': person.full_name,
                    'rombel': rombel,
                    'field': field_path,
                    'problem': problem,
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

        for field_path in mandatory_staff_fields:
            val = _resolve(staff, field_path)
            if not _is_set(val):
                problem = MANDATORY_FIELD_LABELS.get(
                    field_path,
                    f'{field_path} kosong (wajib per skema ekspor)',
                )
                issues['staff'].append({
                    'record': person.full_name,
                    'field': field_path,
                    'problem': problem,
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
        return validate_statutory_export(self.school, self.system, schema=self.schema)


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
