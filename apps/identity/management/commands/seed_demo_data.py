"""Seed a realistic, fully-staffed demo dataset for one foundation.

Builds, per school of the foundation that owns `--email`:
staff for every role (kepala sekolah, wakasek, TU, bendahara, guru, BK, kantin,
UKS), the academic structure (tahun ajaran, semester, mata pelajaran, rombel,
jam pelajaran, jadwal), 300+ active students with their families and parent
logins, four weeks of daily attendance and two assessments per class subject.

Idempotent per school: a school that already carries rows created by this
command is skipped. All data is synthetic.
"""
import random
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Max

from apps.academic.models import (
    AcademicYear, Assessment, AssessmentScore, AssessmentType, ClassEnrollment, ClassGroup,
    ClassSubject, PeriodGridSlot, Subject, Term, TimetableSlot,
)
from apps.attendance.models import AttendanceDay, AttendanceRule, AttendanceSource, AttendanceStatus
from apps.identity import demo_seed_data as data
from apps.identity.models import (
    Guardian, GuardianLink, Person, RoleAssignment, School, Staff, Student, User,
)

MARKER = "seed_demo_data"
DEFAULT_EMAIL = "nat.wutti@gmail.com"
DEFAULT_PASSWORD = "DemoStaff#2026"
EMAIL_DOMAIN = "alhikmah.sch.id"
ACADEMIC_YEAR = ("2026/2027", date(2026, 7, 13), date(2027, 6, 26))
TERMS = [
    (1, "Semester 1 (Ganjil)", date(2026, 7, 13), date(2026, 12, 19), True),
    (2, "Semester 2 (Genap)", date(2027, 1, 4), date(2027, 6, 26), False),
]
# Weekday period grids (period_no, start, end, is_break, label). Friday ends before Jumat prayer.
_GRID_MON_THU = [
    (1, "07:00", "07:40", False, ""), (2, "07:40", "08:20", False, ""), (3, "08:20", "09:00", False, ""),
    (4, "09:00", "09:20", True, "Istirahat 1"),
    (5, "09:20", "10:00", False, ""), (6, "10:00", "10:40", False, ""), (7, "10:40", "11:20", False, ""),
    (8, "11:20", "12:00", True, "Istirahat 2 / Dzuhur"),
    (9, "12:00", "12:40", False, ""), (10, "12:40", "13:20", False, ""),
]
_GRID_FRIDAY = _GRID_MON_THU[:6]
# SD teaches periods 1-7 only.
SD_LAST_PERIOD = 7
ATTENDANCE_DAYS = 20
HOLIDAYS = {date(2026, 8, 17), date(2026, 8, 25)}  # HUT RI, Maulid Nabi
WIB = ZoneInfo("Asia/Jakarta")
BATCH = 500
LEVEL_PROFILE = {
    "SD": "SD", "MI": "SD", "SMP": "SMP", "MTs": "SMP", "SMA": "SMA", "MA": "SMA", "SMK": "SMA",
}
GRADES = {"SD": range(1, 7), "SMP": range(7, 10), "SMA": range(10, 13)}
PHONE_PREFIXES = ["811", "812", "813", "821", "822", "852", "853", "856", "857", "858", "877", "878", "881", "895", "896", "899"]
SCORE_BANDS = ((90, "Sangat Baik"), (80, "Baik"), (70, "Cukup"), (0, "Perlu Bimbingan"))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", ".", text.lower()).strip(".")


class Command(BaseCommand):
    help = "Seeds realistic demo data (staff, students, classes, attendance, grades) for the foundation of --email."

    def add_arguments(self, parser):
        parser.add_argument("--email", default=DEFAULT_EMAIL, help="Foundation admin whose foundation is seeded.")
        parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Login password for every seeded staff account.")
        parser.add_argument("--seed", type=int, default=20260919, help="Random seed (same seed => same data).")

    # ------------------------------------------------------------------ entry
    def handle(self, *args, **opts):
        admin = User.all_tenants.filter(email__iexact=opts["email"]).first()
        if admin is None:
            raise CommandError(f"User {opts['email']} not found.")
        self.foundation_id = admin.foundation_id
        self.rng = random.Random(opts["seed"])
        self.password_hash = make_password(opts["password"])
        self.password = opts["password"]
        self.phones = set(User.all_tenants.with_deleted().values_list("phone_e164", flat=True))
        self.emails = {e.lower() for e in User.all_tenants.with_deleted().exclude(email__isnull=True).values_list("email", flat=True)}
        self.nisns = set(Student.all_tenants.with_deleted().exclude(nisn__isnull=True).values_list("nisn", flat=True))

        schools = list(School.all_tenants.filter(foundation_id=self.foundation_id, is_active=True).order_by("id"))
        if not schools:
            raise CommandError("Foundation has no active schools; run seed_demo_foundation first.")

        self._seed_foundation_staff()
        for school in schools:
            if Student.all_tenants.filter(school=school, created_by=MARKER).exists():
                self.stdout.write(f"  - {school.name}: already seeded, skipping")
                continue
            with transaction.atomic():
                self._seed_school(school)
        self.stdout.write(self.style.SUCCESS(
            f"Demo data ready. Staff login password: {self.password} (email or phone; see staff list)."
        ))

    # ---------------------------------------------------------------- helpers
    def _bulk(self, model, objs):
        """Bulk insert with client-assigned PKs (MySQL cannot return ids from bulk_create)."""
        if not objs:
            return objs
        base = model._base_manager.aggregate(m=Max("pk"))["m"] or 0
        for i, obj in enumerate(objs, start=1):
            obj.pk = base + i
        model._base_manager.bulk_create(objs, batch_size=BATCH)
        return objs

    def _phone(self) -> str:
        while True:
            phone = f"+62{self.rng.choice(PHONE_PREFIXES)}{self.rng.randint(0, 99999999):08d}"
            if phone not in self.phones:
                self.phones.add(phone)
                return phone

    def _email(self, local: str) -> str:
        local, n = _slug(local), 1
        email = f"{local}@{EMAIL_DOMAIN}"
        while email in self.emails:
            n += 1
            email = f"{local}{n}@{EMAIL_DOMAIN}"
        self.emails.add(email)
        return email

    def _nisn(self) -> str:
        while True:
            nisn = f"{self.rng.randint(0, 9999999999):010d}"
            if nisn not in self.nisns:
                self.nisns.add(nisn)
                return nisn

    def _home(self) -> dict:
        kel, kec, pos = self.rng.choice(data.LOCALITIES)
        return {
            "address": f"{self.rng.choice(data.STREETS)} No. {self.rng.randint(1, 120)}",
            "rt": f"{self.rng.randint(1, 12):03d}", "rw": f"{self.rng.randint(1, 10):03d}",
            "kelurahan": kel, "kecamatan": kec, "kabupaten_kota": "Jakarta Selatan",
            "provinsi": "DKI Jakarta", "postal_code": pos,
            "home_latitude": Decimal(str(round(-6.30 + self.rng.uniform(-0.05, 0.05), 6))),
            "home_longitude": Decimal(str(round(106.80 + self.rng.uniform(-0.05, 0.05), 6))),
        }

    def _nik(self, dob: date, gender: str) -> str:
        day = dob.day + (40 if gender == Person.GENDER_FEMALE else 0)
        return f"3174{self.rng.randint(1, 10):02d}{day:02d}{dob.month:02d}{dob.year % 100:02d}{self.rng.randint(1, 9999):04d}"

    def _person(self, full_name, gender, dob, home, **extra) -> Person:
        return Person(
            foundation_id=self.foundation_id, created_by=MARKER, full_name=full_name, gender=gender, dob=dob,
            nik=self._nik(dob, gender), religion=Person.RELIGION_ISLAM, birth_city=self.rng.choice(data.BIRTH_CITIES),
            birth_certificate_number=f"AL-{dob.year}-{self.rng.randint(100000, 999999)}", citizenship="WNI",
            **home, **extra,
        )

    def _given_name(self, gender):
        return self.rng.choice(data.MALE_NAMES if gender == Person.GENDER_MALE else data.FEMALE_NAMES)

    def _dob(self, year_lo, year_hi) -> date:
        return date(self.rng.randint(year_lo, year_hi), 1, 1) + timedelta(days=self.rng.randint(0, 364))

    def _user(self, full_name, email=None) -> User:
        return User(
            foundation_id=self.foundation_id, created_by=MARKER, phone_e164=self._phone(), email=email,
            full_name=full_name, status=User.STATUS_ACTIVE, is_active=True,
        )

    # ------------------------------------------------------------------ staff
    def _make_staff(self, school, specs):
        """specs: list of dicts {name, gender, role, scope, email_local, dob range, degree, title}."""
        persons, users, staff, roles = [], [], [], []
        for i, spec in enumerate(specs):
            gender = spec.get("gender") or self.rng.choice([Person.GENDER_MALE, Person.GENDER_FEMALE])
            name = spec.get("name") or f"{self._given_name(gender)} {self.rng.choice(data.FAMILY_NAMES)}"
            if spec.get("title"):
                name = f"{name}, {spec['title']}"
            dob = self._dob(*spec.get("born", (1974, 2000)))
            persons.append(self._person(name, gender, dob, self._home()))
            users.append(self._user(name, self._email(spec.get("email_local") or name.split(",")[0])))
            users[-1].password = self.password_hash
            degree = spec.get("degree", "S1")
            join = date(self.rng.randint(*spec.get("joined", (2012, 2025))), self.rng.randint(1, 12), 1)
            emp = spec.get("employment") or self.rng.choices(
                [Staff.TYPE_PERMANENT, Staff.TYPE_CONTRACT, Staff.TYPE_HONORARY], [60, 25, 15])[0]
            staff.append(Staff(
                foundation_id=self.foundation_id, created_by=MARKER, school=school, employment_type=emp,
                nip=f"{join.year}{self.rng.randint(0, 9999999):07d}",
                nuptk=f"{self.rng.randint(0, 9999999999999999):016d}" if spec.get("teaching") else None,
                appointment_type=Staff.APPT_GTY if emp == Staff.TYPE_PERMANENT else Staff.APPT_PTT,
                certification_status=self.rng.choices(
                    [Staff.CERT_SERTIFIKAT, Staff.CERT_PROSES, Staff.CERT_BELUM], [45, 15, 40])[0] if spec.get("teaching") else "",
                highest_degree=degree, degree_institution=self.rng.choice(data.STAFF_UNIVERSITIES),
                degree_graduation_year=min(join.year, dob.year + 24 + self.rng.randint(0, 3)),
                join_date=join, status=Staff.STATUS_ACTIVE,
            ))
            roles.append(spec["role"])
        self._bulk(Person, persons)
        self._bulk(User, users)
        for st, p, u in zip(staff, persons, users):
            st.person, st.user = p, u
        self._bulk(Staff, staff)
        scope_type, scope_id = (RoleAssignment.SCOPE_SCHOOL, school.id) if school else (RoleAssignment.SCOPE_FOUNDATION, self.foundation_id)
        self._bulk(RoleAssignment, [
            RoleAssignment(foundation_id=self.foundation_id, created_by=MARKER, user=u, role=r,
                           scope_type=scope_type, scope_id=scope_id)
            for u, r in zip(users, roles)
        ])
        return staff

    def _seed_foundation_staff(self):
        if User.all_tenants.filter(email=f"bendahara.yayasan@{EMAIL_DOMAIN}").exists():
            return
        with transaction.atomic():
            self._make_staff(None, [{
                "name": "Hj. Ratna Sari Dewi, S.E., Ak.", "gender": Person.GENDER_FEMALE, "role": RoleAssignment.ROLE_FINANCE_OFFICER,
                "email_local": "bendahara.yayasan", "degree": "S2", "employment": Staff.TYPE_PERMANENT, "joined": (2012, 2016),
            }])
        self.stdout.write(self.style.SUCCESS("  - Created foundation-level Bendahara Yayasan"))

    def _staff_specs(self, school, profile, n_classes):
        code = school.level.lower()
        R = RoleAssignment
        specs = [
            {"role": R.ROLE_SCHOOL_ADMIN, "email_local": f"kepsek.{code}", "title": "M.Pd.", "degree": "S2", "born": (1968, 1980), "joined": (2008, 2014), "employment": Staff.TYPE_PERMANENT},
            {"role": R.ROLE_SCHOOL_ADMIN, "email_local": f"wakasek.kurikulum.{code}", "title": "M.Pd.", "degree": "S2", "born": (1974, 1986), "joined": (2010, 2016), "employment": Staff.TYPE_PERMANENT},
            {"role": R.ROLE_SCHOOL_ADMIN, "email_local": f"wakasek.kesiswaan.{code}", "title": "S.Pd.", "born": (1974, 1988), "joined": (2010, 2017), "employment": Staff.TYPE_PERMANENT},
            {"role": R.ROLE_SCHOOL_ADMIN, "email_local": f"tu1.{code}", "degree": "D3", "born": (1980, 1998), "joined": (2014, 2022)},
            {"role": R.ROLE_SCHOOL_ADMIN, "email_local": f"tu2.{code}", "degree": "SMA", "born": (1985, 2001), "joined": (2016, 2024)},
            {"role": R.ROLE_FINANCE_OFFICER, "email_local": f"bendahara.{code}", "title": "S.E.", "born": (1978, 1992), "joined": (2012, 2020), "employment": Staff.TYPE_PERMANENT},
            {"role": R.ROLE_FINANCE_OFFICER, "email_local": f"keuangan.{code}", "degree": "D3", "born": (1988, 2002), "joined": (2018, 2025)},
            {"role": R.ROLE_COUNSELLOR, "email_local": f"bk1.{code}", "title": "S.Psi.", "born": (1980, 1996), "joined": (2014, 2023), "teaching": True},
            {"role": R.ROLE_CANTEEN_OPERATOR, "email_local": f"kantin1.{code}", "degree": "SMA", "born": (1975, 2000), "joined": (2015, 2024), "employment": Staff.TYPE_CONTRACT},
            {"role": R.ROLE_CANTEEN_OPERATOR, "email_local": f"kantin2.{code}", "degree": "SMA", "born": (1975, 2000), "joined": (2015, 2024), "employment": Staff.TYPE_CONTRACT},
            {"role": R.ROLE_CLINIC_OFFICER, "email_local": f"uks.{code}", "title": "S.Kep., Ns.", "born": (1982, 1998), "joined": (2016, 2024)},
        ]
        if profile != "SD":
            specs.append({"role": R.ROLE_COUNSELLOR, "email_local": f"bk2.{code}", "title": "S.Pd.", "born": (1982, 1998), "joined": (2016, 2024), "teaching": True})
        # Teachers: (subject codes taught, homeroom-eligible)
        if profile == "SD":
            teacher_subjects = [["HOMEROOM"]] * n_classes + [s for s in data.SD_SPECIALISTS for _ in range(2)]
        elif profile == "SMP":
            teacher_subjects = data.SMP_TEACHERS
        else:
            teacher_subjects = data.SMA_TEACHERS
        n_teacher = 0
        for subjects in teacher_subjects:
            n_teacher += 1
            title = "S.Pd.I." if subjects[0] in ("PAI", "BAQ") else "S.Kom." if subjects[0] == "INF" else self.rng.choice(["S.Pd.", "S.Pd.", "S.Pd.", "M.Pd."])
            specs.append({"role": R.ROLE_TEACHER, "title": title, "teaching": True, "subjects": subjects,
                          "email_local": f"guru{n_teacher}.{code}", "born": (1975, 2001), "joined": (2011, 2025)})
        return specs

    # ---------------------------------------------------------------- school
    def _seed_school(self, school):
        profile = LEVEL_PROFILE.get(school.level, "SMA")
        rng = self.rng
        self.stdout.write(f"  - {school.name}")

        # Class layout
        layout = []  # (grade, name, stream)
        for g in GRADES[profile]:
            if profile == "SD":
                layout += [(g, f"{g}-{c}", None) for c in "AB"]
            elif profile == "SMP":
                layout += [(g, f"{g}-{c}", None) for c in "ABCD"]
            elif g == 10:
                layout += [(g, f"X-{n}", None) for n in range(1, 5)]
            else:
                roman = "XI" if g == 11 else "XII"
                layout += [(g, f"{roman} IPA {n}", "IPA") for n in (1, 2)] + [(g, f"{roman} IPS {n}", "IPS") for n in (1, 2)]

        specs = self._staff_specs(school, profile, len(layout))
        staff = self._make_staff(school, specs)
        teachers = [(st, sp["subjects"]) for st, sp in zip(staff, specs) if sp["role"] == RoleAssignment.ROLE_TEACHER]
        self.stdout.write(f"      staff: {len(staff)}")

        # Academic year, terms, grid, subjects
        year = AcademicYear.all_tenants.create(
            foundation_id=self.foundation_id, created_by=MARKER, school=school, name=ACADEMIC_YEAR[0],
            start_date=ACADEMIC_YEAR[1], end_date=ACADEMIC_YEAR[2], is_active=True)
        terms = {n: Term.all_tenants.create(
            foundation_id=self.foundation_id, created_by=MARKER, academic_year=year, name=name, term_no=n,
            start_date=s, end_date=e, is_active=act) for n, name, s, e, act in TERMS}
        term = terms[1]
        grid = self._seed_grid(school, profile)
        subjects = self._seed_subjects(school, profile)

        # Classes, class subjects
        homeroom_pool = [st for st, subs in teachers if subs != ["HOMEROOM"]] if profile != "SD" else [st for st, subs in teachers if subs == ["HOMEROOM"]]
        classes = []
        for i, (grade, name, stream) in enumerate(layout):
            classes.append(ClassGroup(
                foundation_id=self.foundation_id, created_by=MARKER, school=school, academic_year=year,
                grade_level=grade, name=name, homeroom_teacher=homeroom_pool[i % len(homeroom_pool)], capacity=36))
        self._bulk(ClassGroup, classes)
        class_subjects = self._seed_class_subjects(profile, layout, classes, subjects, teachers, term)
        self._seed_timetable(profile, layout, classes, class_subjects, grid)

        students_by_class = self._seed_students(school, profile, classes)
        n_students = sum(len(v) for v in students_by_class.values())
        self.stdout.write(f"      students: {n_students} in {len(classes)} classes")
        self._seed_attendance(school, profile, students_by_class)
        self._seed_assessments(class_subjects, students_by_class)

    def _seed_grid(self, school, profile):
        rows = []
        for dow in range(1, 6):
            for p, start, end, brk, label in (_GRID_FRIDAY if dow == 5 else _GRID_MON_THU):
                if profile == "SD" and not brk and p > SD_LAST_PERIOD:
                    continue
                rows.append(PeriodGridSlot(
                    foundation_id=self.foundation_id, created_by=MARKER, school=school, day_of_week=dow, period_no=p,
                    start_time=time.fromisoformat(start), end_time=time.fromisoformat(end), is_break=brk, label=label))
        self._bulk(PeriodGridSlot, rows)
        return {(r.day_of_week, r.period_no): r for r in rows if not r.is_break}

    def _seed_subjects(self, school, profile):
        codes = {"SD": data.SD_SUBJECTS, "SMP": data.SMP_SUBJECTS,
                 "SMA": data.SMA_COMMON + data.SMA_IPA + data.SMA_IPS}[profile]
        rows = []
        for code in codes:
            info = data.SUBJECT_CATALOG[code]
            rows.append(Subject(
                foundation_id=self.foundation_id, created_by=MARKER, school=school, code=code, name=info["name"],
                level=school.level, is_religious=info["is_religious"], credit_hours=info["credit_hours"], is_active=True))
        self._bulk(Subject, rows)
        return {s.code: s for s in rows}

    def _subject_codes_for(self, profile, grade, stream):
        if profile == "SD":
            return data.SD_SUBJECTS
        if profile == "SMP":
            return data.SMP_SUBJECTS
        if grade == 10:
            return data.SMA_COMMON
        return data.SMA_COMMON + (data.SMA_IPA if stream == "IPA" else data.SMA_IPS)

    def _seed_class_subjects(self, profile, layout, classes, subjects, teachers, term):
        by_subject = defaultdict(list)
        for st, subs in teachers:
            for code in subs:
                by_subject[code].append(st)
        rows = []
        for i, ((grade, name, stream), cls) in enumerate(zip(layout, classes)):
            for code in self._subject_codes_for(profile, grade, stream):
                if profile == "SD" and code in ("PPKN", "BIN", "MTK", "IPAS"):
                    teacher = cls.homeroom_teacher
                else:
                    pool = by_subject[code]
                    teacher = pool[i % len(pool)]
                rows.append(ClassSubject(
                    foundation_id=self.foundation_id, created_by=MARKER, class_group=cls, subject=subjects[code],
                    teacher=teacher, term=term))
        return self._bulk(ClassSubject, rows)

    def _seed_timetable(self, profile, layout, classes, class_subjects, grid):
        busy = set()  # (teacher_id, dow, period)
        slots_by_class = defaultdict(set)  # class_id -> {(dow, period)}
        cells = list(grid.keys())
        rows = []
        # Heaviest subjects first so they get the easy picks.
        for cs in sorted(class_subjects, key=lambda c: -c.subject.credit_hours):
            days_used = set()
            for _ in range(cs.subject.credit_hours):
                options = [c for c in cells if c not in slots_by_class[cs.class_group_id] and (cs.teacher_id, *c) not in busy]
                fresh = [c for c in options if c[0] not in days_used]
                if not (fresh or options):
                    continue
                dow, period = self.rng.choice(fresh or options)
                busy.add((cs.teacher_id, dow, period))
                slots_by_class[cs.class_group_id].add((dow, period))
                days_used.add(dow)
                g = grid[(dow, period)]
                rows.append(TimetableSlot(
                    foundation_id=self.foundation_id, created_by=MARKER, class_subject=cs, day_of_week=dow,
                    period_no=period, start_time=g.start_time, end_time=g.end_time,
                    room=f"Ruang {cs.class_group.name}"))
        self._bulk(TimetableSlot, rows)

    # -------------------------------------------------------------- students
    def _seed_students(self, school, profile, classes):
        rng = self.rng
        school_no = school.npsn[-2:]
        seq = 0
        families = []  # {father, mother, home, surname, kids}
        persons, guardians, users, students, links, enrollments = [], [], [], [], [], []
        by_class = {}

        def new_family():
            home = self._home()
            surname = rng.choice(data.FAMILY_NAMES)
            single = rng.random() < 0.06  # single-parent household: mother only
            mother = self._person(f"{self._given_name('P')} {rng.choice(data.FAMILY_NAMES)}", "P", self._dob(1970, 1995), home)
            g_mother = Guardian(foundation_id=self.foundation_id, created_by=MARKER, person=mother, occupation=rng.choice(data.OCCUPATIONS))
            fam = {"mother": mother, "g_mother": g_mother, "g_father": None, "home": home, "surname": surname,
                   "kids": 0, "classes": set()}
            persons.append(mother)
            guardians.append(g_mother)
            if not single:
                father = self._person(f"{self._given_name('L')} {surname}", "L", self._dob(1968, 1992), home)
                fam["g_father"] = Guardian(foundation_id=self.foundation_id, created_by=MARKER, person=father, occupation=rng.choice(data.OCCUPATIONS))
                persons.append(father)
                guardians.append(fam["g_father"])
            # One parent per family holds the app login and is the billing contact.
            primary = rng.choice([g for g in (fam["g_father"], g_mother) if g])
            user = self._user(primary.person.full_name)
            user.password = make_password(None)
            users.append(user)
            primary.user = user
            fam["primary"] = primary
            families.append(fam)
            return fam

        for cls in classes:
            size = rng.randint(28, 34)
            by_class[cls.id] = []
            for _ in range(size):
                seq += 1
                # ~12% of students are siblings of an already-seeded student in another class.
                fam = rng.choice(families) if families and rng.random() < 0.12 else None
                if fam is None or fam["kids"] >= 3 or cls.id in fam["classes"]:
                    fam = new_family()
                fam["kids"] += 1
                fam["classes"].add(cls.id)
                gender = rng.choice(["L", "P"])
                born_year = 2026 - (6 + cls.grade_level) - rng.choice([0, 0, 1])
                dob = self._dob(born_year, born_year)
                entry_year = 2026 - (cls.grade_level - {"SD": 1, "SMP": 7, "SMA": 10}[profile])
                person = self._person(
                    f"{self._given_name(gender)} {fam['surname']}", gender, dob, fam["home"],
                    mother_name=fam["mother"].full_name)
                persons.append(person)
                stu = Student(
                    foundation_id=self.foundation_id, created_by=MARKER, school=school, person=person,
                    nisn=self._nisn(), nis=f"{entry_year}{school_no}{seq:04d}", status=Student.STATUS_ACTIVE)
                students.append(stu)
                by_class[cls.id].append(stu)
                enrollments.append((stu, cls))
                links.append((fam, stu))

        # PPDB prospects for the next intake.
        first_grade = {"SD": 1, "SMP": 7, "SMA": 10}[profile]
        for n in range(1, 9):
            gender = rng.choice(["L", "P"])
            born = 2026 - (5 + first_grade)
            person = self._person(f"{self._given_name(gender)} {rng.choice(data.FAMILY_NAMES)}", gender, self._dob(born, born), self._home())
            persons.append(person)
            students.append(Student(
                foundation_id=self.foundation_id, created_by=MARKER, school=school, person=person, nisn=None,
                nis=f"PPDB-2027-{school_no}{n:03d}", status=Student.STATUS_PROSPECT, target_grade_level=first_grade))

        self._bulk(Person, persons)
        self._bulk(User, users)
        # FK columns were captured before the client-assigned pks existed: re-assign to refresh them.
        for g in guardians:
            g.person, g.user = g.person, g.user
        self._bulk(Guardian, guardians)
        for stu in students:
            stu.person = stu.person
        self._bulk(Student, students)

        self._bulk(RoleAssignment, [
            RoleAssignment(foundation_id=self.foundation_id, created_by=MARKER, user=u, role=RoleAssignment.ROLE_PARENT,
                           scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=school.id) for u in users])
        link_rows = []
        for fam, stu in links:
            for relation, guardian in ((GuardianLink.RELATION_MOTHER, fam["g_mother"]), (GuardianLink.RELATION_FATHER, fam["g_father"])):
                if guardian is None:
                    continue
                link_rows.append(GuardianLink(
                    foundation_id=self.foundation_id, created_by=MARKER, guardian=guardian, student=stu, relation=relation,
                    is_primary=fam["primary"] is guardian, can_pickup=True, financial_responsible=fam["primary"] is guardian))
        self._bulk(GuardianLink, link_rows)
        self._bulk(ClassEnrollment, [
            ClassEnrollment(foundation_id=self.foundation_id, created_by=MARKER, student=s, class_group=c,
                            enrolled_at=ACADEMIC_YEAR[1], is_active=True) for s, c in enrollments])
        return by_class

    # ------------------------------------------------------------ attendance
    def _seed_attendance(self, school, profile, students_by_class):
        rng = self.rng
        AttendanceRule.all_tenants.create(
            foundation_id=self.foundation_id, created_by=MARKER, school=school,
            late_after_time=time(7, 15), absent_cutoff_time=time(9, 0))
        days, d = [], date(2026, 9, 18)
        while len(days) < ATTENDANCE_DAYS:
            if d.weekday() < 5 and d not in HOLIDAYS:
                days.append(d)
            d -= timedelta(days=1)
        out_hour = {"SD": (12, 30), "SMP": (13, 20), "SMA": (13, 20)}[profile]
        rows = []
        for students in students_by_class.values():
            for stu in students:
                late_prone = rng.random() < 0.06
                sick_prone = rng.random() < 0.04
                for day in days:
                    roll = rng.random()
                    p_late = 0.22 if late_prone else 0.025
                    p_sick = 0.09 if sick_prone else 0.012
                    if roll < 0.004:
                        status = AttendanceStatus.ALPA
                    elif roll < 0.004 + p_sick:
                        status = AttendanceStatus.SAKIT
                    elif roll < 0.004 + p_sick + 0.008:
                        status = AttendanceStatus.IZIN
                    elif roll < 0.004 + p_sick + 0.008 + p_late:
                        status = AttendanceStatus.TERLAMBAT
                    else:
                        status = AttendanceStatus.HADIR
                    first_in = last_out = None
                    source = AttendanceSource.GATE
                    if status == AttendanceStatus.HADIR:
                        first_in = time(6, rng.randint(20, 59)) if rng.random() < 0.5 else time(7, rng.randint(0, 14))
                    elif status == AttendanceStatus.TERLAMBAT:
                        first_in = time(7, rng.randint(16, 55))
                    elif status == AttendanceStatus.ALPA:
                        source = AttendanceSource.SYSTEM
                    else:
                        source = AttendanceSource.TEACHER
                    if first_in:
                        first_in = datetime.combine(day, first_in, tzinfo=WIB)
                        last_out = datetime.combine(day, time(out_hour[0], out_hour[1] + rng.randint(0, 25)), tzinfo=WIB)
                    rows.append(AttendanceDay(
                        foundation_id=self.foundation_id, created_by=MARKER, school=school, student=stu, date=day,
                        status=status, first_in_at=first_in, last_out_at=last_out, source=source,
                        note={"SAKIT": "Surat dokter", "IZIN": "Izin keluarga"}.get(status, "")))
        self._bulk(AttendanceDay, rows)
        self.stdout.write(f"      attendance rows: {len(rows)}")

    # ---------------------------------------------------------------- grades
    def _seed_assessments(self, class_subjects, students_by_class):
        rng = self.rng
        ability = {}
        for students in students_by_class.values():
            for stu in students:
                ability[stu.pk] = rng.gauss(78, 8)
        defs = [
            (AssessmentType.SUMMATIVE, "Ulangan Harian 1", Decimal("30.00"), datetime(2026, 8, 21, 9, 0, tzinfo=WIB)),
            (AssessmentType.FORMATIVE, "Kuis Formatif Bab 2", Decimal("0.00"), datetime(2026, 9, 4, 9, 0, tzinfo=WIB)),
        ]
        assessments = []
        for cs in class_subjects:
            for typ, title, weight, due in defs:
                assessments.append(Assessment(
                    foundation_id=self.foundation_id, created_by=MARKER, class_subject=cs, type=typ, title=title,
                    max_score=Decimal("100.00"), weight=weight, due_at=due, published=True))
        self._bulk(Assessment, assessments)
        scores = []
        graded_by = {}
        for a in assessments:
            cs = a.class_subject
            teacher_user = graded_by.setdefault(cs.teacher_id, cs.teacher.user)
            offset = rng.gauss(0, 4)
            for stu in students_by_class[cs.class_group_id]:
                value = max(35.0, min(100.0, ability[stu.pk] + offset + rng.gauss(0, 6)))
                value = round(value * 2) / 2
                descriptor = next(label for floor, label in SCORE_BANDS if value >= floor)
                scores.append(AssessmentScore(
                    foundation_id=self.foundation_id, created_by=MARKER, assessment=a, student=stu,
                    score=Decimal(str(value)), descriptor=descriptor, graded_by=teacher_user,
                    graded_at=a.due_at + timedelta(days=3)))
        self._bulk(AssessmentScore, scores)
        self.stdout.write(f"      assessments: {len(assessments)}, scores: {len(scores)}")
