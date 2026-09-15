"""Bulk Student Importer service supporting XLSX/CSV with atomic dry-run diff preview (spec/02 §5, IAM-017, IAM-018).

Enforces:
- Atomicity: File fails as a whole if any row has validation or duplicate errors.
- Precise reporting: Pinpoints all offending rows and fields (spec/02 §8.3).
- Validation:
  * NISN: exactly 10 digits when provided (IAM-018).
  * NIK: exactly 16 digits when provided (IAM-018).
  * Phone: E.164 normalized (+62...).
  * Duplicate detection on:
    1. NISN (in-file and database-wide under foundation).
    2. (full_name.lower(), dob) combination.
    3. NIS within the target school.
"""
import csv
import io
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple
from django.db import transaction
from django.utils import timezone
from apps.core.services import audit, record_domain_event
from .models import School, Person, Student, Guardian, GuardianLink, User
from .services import normalize_phone_e164


class StudentBulkImporter:
    """Handles bulk student imports from XLSX and CSV files with dry-run diffing."""

    def __init__(self, foundation_id: int, school_id: int, actor_id: Optional[str] = None):
        self.foundation_id = foundation_id
        self.school_id = school_id
        self.actor_id = actor_id
        self.school = School.all_tenants.get(foundation_id=foundation_id, id=school_id)

    def parse_file(self, file_obj, filename: str) -> List[Dict[str, Any]]:
        """Parse uploaded file into list of row dictionaries."""
        filename_lower = filename.lower()
        if filename_lower.endswith('.xlsx'):
            return self._parse_xlsx(file_obj)
        elif filename_lower.endswith('.csv'):
            return self._parse_csv(file_obj)
        else:
            raise ValueError("Format berkas tidak didukung. Harap unggah berkas .xlsx atau .csv.")

    def _parse_xlsx(self, file_obj) -> List[Dict[str, Any]]:
        import openpyxl
        wb = openpyxl.load_workbook(file_obj, data_only=True)
        sheet = wb.active

        rows = list(sheet.iter_rows(values_only=True))
        if not rows or len(rows) < 2:
            return []

        # Header normalization
        raw_headers = [str(cell).strip().lower() if cell is not None else '' for cell in rows[0]]
        header_map = self._map_headers(raw_headers)

        parsed_rows = []
        for row_idx, row_values in enumerate(rows[1:], start=2):
            if not any(row_values):
                continue  # skip completely blank rows
            row_dict = {'__row_num__': row_idx}
            for col_idx, val in enumerate(row_values):
                canonical_col = header_map.get(col_idx)
                if canonical_col:
                    row_dict[canonical_col] = self._normalize_val(val)
            parsed_rows.append(row_dict)

        return parsed_rows

    def _parse_csv(self, file_obj) -> List[Dict[str, Any]]:
        content = file_obj.read()
        if isinstance(content, bytes):
            content = content.decode('utf-8-sig', errors='replace')
        
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        if not rows or len(rows) < 2:
            return []

        raw_headers = [cell.strip().lower() for cell in rows[0]]
        header_map = self._map_headers(raw_headers)

        parsed_rows = []
        for row_idx, row_values in enumerate(rows[1:], start=2):
            if not any(row_values):
                continue
            row_dict = {'__row_num__': row_idx}
            for col_idx, val in enumerate(row_values):
                canonical_col = header_map.get(col_idx)
                if canonical_col:
                    row_dict[canonical_col] = self._normalize_val(val)
            parsed_rows.append(row_dict)

        return parsed_rows

    def _normalize_val(self, val) -> Any:
        if val is None:
            return ''
        if isinstance(val, (datetime, date)):
            return val.strftime('%Y-%m-%d')
        val_str = str(val).strip()
        # Remove floating decimal representation from numeric spreadsheet cells (e.g. 12345.0 -> 12345)
        if val_str.endswith('.0') and val_str[:-2].isdigit():
            return val_str[:-2]
        return val_str

    def _map_headers(self, headers: List[str]) -> Dict[int, str]:
        """Maps spreadsheet column positions to canonical internal keys."""
        mapping = {}
        for idx, h in enumerate(headers):
            h_clean = h.replace(' ', '_').replace('-', '_')
            if h_clean in ('nis', 'nomor_induk', 'no_induk'):
                mapping[idx] = 'nis'
            elif h_clean in ('nisn', 'nomor_induk_siswa_nasional'):
                mapping[idx] = 'nisn'
            elif h_clean in ('nama', 'nama_lengkap', 'full_name', 'student_name'):
                mapping[idx] = 'full_name'
            elif h_clean in ('nik', 'nik_siswa'):
                mapping[idx] = 'nik'
            elif h_clean in ('tanggal_lahir', 'tgl_lahir', 'dob', 'birth_date'):
                mapping[idx] = 'dob'
            elif h_clean in ('gender', 'jenis_kelamin', 'jk'):
                mapping[idx] = 'gender'
            elif h_clean in ('alamat', 'address', 'domisili'):
                mapping[idx] = 'address'
            elif h_clean in ('nama_wali', 'nama_orang_tua', 'guardian_name', 'parent_name'):
                mapping[idx] = 'guardian_name'
            elif h_clean in ('no_hp_wali', 'telepon_wali', 'guardian_phone', 'parent_phone'):
                mapping[idx] = 'guardian_phone'
            elif h_clean in ('nik_wali', 'guardian_nik', 'parent_nik'):
                mapping[idx] = 'guardian_nik'
            elif h_clean in ('hubungan', 'hubungan_wali', 'relation', 'guardian_relation'):
                mapping[idx] = 'guardian_relation'
            elif h_clean in ('pekerjaan_wali', 'occupation', 'guardian_occupation'):
                mapping[idx] = 'guardian_occupation'
        return mapping

    def execute(self, file_obj, filename: str, dry_run: bool = True) -> Dict[str, Any]:
        """Executes parsing, validation, and optional database persistence."""
        rows = self.parse_file(file_obj, filename)
        if not rows:
            return {
                'success': False,
                'message': 'Berkas tidak memuat baris data yang valid.',
                'total_rows': 0,
                'valid_rows': 0,
                'error_rows': 0,
                'errors': [{'row': 0, 'column': 'file', 'message': 'Berkas kosong atau format header tidak sesuai.'}],
                'preview': [],
            }

        # 1. Pre-load existing database identifiers for duplicate detection
        existing_nisns = set(
            Student.all_tenants.filter(
                foundation_id=self.foundation_id,
                nisn__isnull=False,
                deleted_at__isnull=True,
            ).values_list('nisn', flat=True)
        )
        existing_school_niss = set(
            Student.all_tenants.filter(
                foundation_id=self.foundation_id,
                school_id=self.school_id,
                deleted_at__isnull=True,
            ).values_list('nis', flat=True)
        )
        existing_name_dobs = set(
            Person.all_tenants.filter(
                foundation_id=self.foundation_id,
                student_profiles__isnull=False,
                deleted_at__isnull=True,
            ).values_list('full_name', 'dob')
        )
        # Normalize existing name+dob strings for case-insensitive matching
        existing_name_dob_keys = {
            (name.strip().lower(), dob.strftime('%Y-%m-%d') if dob else None)
            for name, dob in existing_name_dobs
        }

        # In-file tracking for cross-row duplicates
        seen_nisns: Dict[str, int] = {}
        seen_school_niss: Dict[str, int] = {}
        seen_name_dobs: Dict[Tuple[str, Optional[str]], int] = {}

        errors: List[Dict[str, Any]] = []
        validated_rows: List[Dict[str, Any]] = []

        for r in rows:
            row_num = r['__row_num__']
            row_errors: List[str] = []

            nis = r.get('nis', '').strip()
            nisn = r.get('nisn', '').strip()
            full_name = r.get('full_name', '').strip()
            nik = r.get('nik', '').strip()
            dob_str = r.get('dob', '').strip()
            gender = r.get('gender', '').strip().upper()
            address = r.get('address', '').strip()

            guardian_name = r.get('guardian_name', '').strip()
            guardian_phone = r.get('guardian_phone', '').strip()
            guardian_nik = r.get('guardian_nik', '').strip()
            guardian_relation = r.get('guardian_relation', '').strip().upper()
            guardian_occupation = r.get('guardian_occupation', '').strip()

            # Required field: Full Name
            if not full_name:
                errors.append({'row': row_num, 'column': 'full_name', 'message': 'Nama lengkap siswa wajib diisi.'})
                continue

            # Required field: NIS
            if not nis:
                errors.append({'row': row_num, 'column': 'nis', 'message': 'NIS siswa wajib diisi.'})
                continue

            # NIS duplicates check within school
            if nis in seen_school_niss:
                errors.append({
                    'row': row_num,
                    'column': 'nis',
                    'message': f"Duplikasi NIS '{nis}' dalam berkas (sama dengan baris {seen_school_niss[nis]})."
                })
            elif nis in existing_school_niss:
                errors.append({
                    'row': row_num,
                    'column': 'nis',
                    'message': f"NIS '{nis}' sudah terdaftar pada sekolah ini."
                })
            else:
                seen_school_niss[nis] = row_num

            # NISN Validation (10 digits if present per IAM-018)
            clean_nisn = None
            if nisn:
                if not (nisn.isdigit() and len(nisn) == 10):
                    errors.append({
                        'row': row_num,
                        'column': 'nisn',
                        'message': f"NISN '{nisn}' tidak valid. NISN harus terdiri tepat dari 10 digit angka."
                    })
                elif nisn in seen_nisns:
                    errors.append({
                        'row': row_num,
                        'column': 'nisn',
                        'message': f"Duplikasi NISN '{nisn}' dalam berkas (sama dengan baris {seen_nisns[nisn]})."
                    })
                elif nisn in existing_nisns:
                    errors.append({
                        'row': row_num,
                        'column': 'nisn',
                        'message': f"NISN '{nisn}' sudah terdaftar dalam sistem yayasan."
                    })
                else:
                    seen_nisns[nisn] = row_num
                    clean_nisn = nisn

            # NIK Validation (16 digits if present per IAM-018)
            clean_nik = None
            if nik:
                if not (nik.isdigit() and len(nik) == 16):
                    errors.append({
                        'row': row_num,
                        'column': 'nik',
                        'message': f"NIK '{nik}' tidak valid. NIK harus terdiri tepat dari 16 digit angka."
                    })
                else:
                    clean_nik = nik

            # DOB Validation
            parsed_dob = None
            if dob_str:
                for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d'):
                    try:
                        parsed_dob = datetime.strptime(dob_str, fmt).date()
                        break
                    except ValueError:
                        continue
                if parsed_dob is None:
                    errors.append({
                        'row': row_num,
                        'column': 'dob',
                        'message': f"Format tanggal lahir '{dob_str}' tidak dikenali (gunakan YYYY-MM-DD atau DD/MM/YYYY)."
                    })

            # Duplicate check: (full_name + dob) per IAM-018
            name_dob_key = (full_name.lower(), parsed_dob.strftime('%Y-%m-%d') if parsed_dob else None)
            if name_dob_key in seen_name_dobs:
                errors.append({
                    'row': row_num,
                    'column': 'full_name',
                    'message': f"Duplikasi siswa '{full_name}' dengan tanggal lahir sama dalam berkas (baris {seen_name_dobs[name_dob_key]})."
                })
            elif name_dob_key in existing_name_dob_keys:
                errors.append({
                    'row': row_num,
                    'column': 'full_name',
                    'message': f"Siswa '{full_name}' dengan tanggal lahir sama sudah terdaftar di yayasan."
                })
            else:
                seen_name_dobs[name_dob_key] = row_num

            # Gender Normalization (L / P)
            clean_gender = ''
            if gender:
                if gender in ('L', 'LAKI-LAKI', 'LAKI_LAKI', 'PRIA', 'MALE', 'M'):
                    clean_gender = Person.GENDER_MALE
                elif gender in ('P', 'PEREMPUAN', 'WANITA', 'FEMALE', 'F'):
                    clean_gender = Person.GENDER_FEMALE
                else:
                    errors.append({
                        'row': row_num,
                        'column': 'gender',
                        'message': f"Jenis kelamin '{gender}' tidak valid (gunakan L atau P)."
                    })

            # Guardian phone & relation validation
            clean_guardian_phone = None
            if guardian_phone:
                try:
                    clean_guardian_phone = normalize_phone_e164(guardian_phone)
                except Exception as exc:
                    errors.append({
                        'row': row_num,
                        'column': 'guardian_phone',
                        'message': str(exc)
                    })

            clean_guardian_relation = GuardianLink.RELATION_GUARDIAN
            if guardian_relation:
                if guardian_relation in ('AYAH', 'FATHER', 'PAPA'):
                    clean_guardian_relation = GuardianLink.RELATION_FATHER
                elif guardian_relation in ('IBU', 'MOTHER', 'MAMA'):
                    clean_guardian_relation = GuardianLink.RELATION_MOTHER
                elif guardian_relation in ('WALI', 'GUARDIAN'):
                    clean_guardian_relation = GuardianLink.RELATION_GUARDIAN

            clean_guardian_nik = None
            if guardian_nik:
                if not (guardian_nik.isdigit() and len(guardian_nik) == 16):
                    errors.append({
                        'row': row_num,
                        'column': 'guardian_nik',
                        'message': f"NIK wali '{guardian_nik}' tidak valid (harus 16 digit)."
                    })
                else:
                    clean_guardian_nik = guardian_nik

            validated_rows.append({
                'row_num': row_num,
                'nis': nis,
                'nisn': clean_nisn,
                'full_name': full_name,
                'nik': clean_nik,
                'dob': parsed_dob,
                'gender': clean_gender,
                'address': address,
                'guardian_name': guardian_name,
                'guardian_phone': clean_guardian_phone,
                'guardian_nik': clean_guardian_nik,
                'guardian_relation': clean_guardian_relation,
                'guardian_occupation': guardian_occupation,
            })

        # Atomicity check: If any errors exist, reject the entire import
        if errors:
            return {
                'success': False,
                'dry_run': dry_run,
                'message': f"Impor dibatalkan: ditemukan {len(errors)} kesalahan pada berkas.",
                'total_rows': len(rows),
                'valid_rows': len(rows) - len({e['row'] for e in errors}),
                'error_rows': len({e['row'] for e in errors}),
                'errors': errors,
                'preview': [],
            }

        # Dry Run Mode: Return parsed diff preview
        if dry_run:
            preview = [
                {
                    'row': r['row_num'],
                    'nis': r['nis'],
                    'nisn': r['nisn'],
                    'full_name': r['full_name'],
                    'dob': str(r['dob']) if r['dob'] else None,
                    'gender': r['gender'],
                    'has_guardian': bool(r['guardian_name'] or r['guardian_phone']),
                }
                for r in validated_rows[:20]  # first 20 preview
            ]
            return {
                'success': True,
                'dry_run': True,
                'message': f"Pratinjau impor berhasil: {len(validated_rows)} baris data siap diimpor.",
                'total_rows': len(rows),
                'valid_rows': len(validated_rows),
                'error_rows': 0,
                'errors': [],
                'preview': preview,
            }

        # Execution Mode: Commit within transaction.atomic()
        created_student_ids = []
        with transaction.atomic():
            for r in validated_rows:
                # 1. Create Student Person PII vault record
                person = Person.objects.create(
                    foundation_id=self.foundation_id,
                    full_name=r['full_name'],
                    nik=r['nik'],
                    dob=r['dob'],
                    gender=r['gender'],
                    address=r['address'],
                    created_by=self.actor_id,
                )

                # 2. Create Student profile
                student = Student.objects.create(
                    foundation_id=self.foundation_id,
                    school_id=self.school_id,
                    person=person,
                    nis=r['nis'],
                    nisn=r['nisn'],
                    status=Student.STATUS_PROSPECT,
                    created_by=self.actor_id,
                )
                created_student_ids.append(student.id)

                # 3. Handle optional Guardian and GuardianLink
                if r['guardian_name'] or r['guardian_phone']:
                    # Look up or create guardian person
                    guardian_person = None
                    if r['guardian_nik']:
                        guardian_person = Person.objects.filter(
                            foundation_id=self.foundation_id,
                            nik=r['guardian_nik']
                        ).first()

                    if not guardian_person:
                        guardian_person = Person.objects.create(
                            foundation_id=self.foundation_id,
                            full_name=r['guardian_name'] or f"Wali {r['full_name']}",
                            nik=r['guardian_nik'],
                            created_by=self.actor_id,
                        )

                    # Look up or create user if phone is present
                    guardian_user = None
                    if r['guardian_phone']:
                        guardian_user = User.all_tenants.filter(
                            foundation_id=self.foundation_id,
                            phone_e164=r['guardian_phone']
                        ).first()
                        if not guardian_user:
                            guardian_user = User.objects.create_user(
                                phone_e164=r['guardian_phone'],
                                full_name=guardian_person.full_name,
                                foundation_id=self.foundation_id,
                            )

                    # Look up or create Guardian profile
                    guardian, _ = Guardian.all_tenants.get_or_create(
                        foundation_id=self.foundation_id,
                        person=guardian_person,
                        defaults={
                            'user': guardian_user,
                            'occupation': r['guardian_occupation'],
                            'created_by': self.actor_id,
                        }
                    )

                    # Create GuardianLink
                    GuardianLink.objects.create(
                        foundation_id=self.foundation_id,
                        guardian=guardian,
                        student=student,
                        relation=r['guardian_relation'],
                        is_primary=True,
                        can_pickup=True,
                        financial_responsible=True,
                        created_by=self.actor_id,
                    )

            # Record audit event
            audit(
                action="identity.student.bulk_imported",
                entity_type="Student",
                entity_id=str(self.school_id),
                actor_id=self.actor_id or 'system',
                role='school_admin',
                foundation_id=self.foundation_id,
                school_id=self.school_id,
                diff={
                    'imported_count': len(created_student_ids),
                    'filename': filename,
                }
            )

            # Record domain event
            record_domain_event(
                name='identity.student.bulk_imported',
                foundation_id=self.foundation_id,
                payload={
                    'school_id': self.school_id,
                    'count': len(created_student_ids),
                    'actor_id': self.actor_id,
                }
            )

        return {
            'success': True,
            'dry_run': False,
            'message': f"Berhasil mengimpor {len(created_student_ids)} data siswa secara atomik.",
            'total_rows': len(rows),
            'valid_rows': len(created_student_ids),
            'error_rows': 0,
            'errors': [],
            'created_count': len(created_student_ids),
        }
