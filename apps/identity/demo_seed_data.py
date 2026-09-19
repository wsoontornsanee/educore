"""Static vocabulary for the `seed_demo_data` management command.

Indonesian names, Jakarta Selatan addresses and Kurikulum Merdeka subject
catalogues. Everything here is synthetic — no real person is represented.
"""

MALE_NAMES = [
    "Ahmad", "Muhammad", "Abdul", "Rizky", "Fajar", "Budi", "Andi", "Dimas", "Rafi", "Farhan",
    "Aditya", "Bayu", "Reza", "Hendra", "Yusuf", "Ilham", "Naufal", "Arief", "Bagas", "Dani",
    "Eko", "Fikri", "Galih", "Hafiz", "Irfan", "Joko", "Kevin", "Luthfi", "Mizan", "Nurul",
    "Oki", "Putra", "Rangga", "Satria", "Taufik", "Umar", "Vino", "Wahyu", "Yoga", "Zaki",
    "Alif", "Bima", "Cahya", "Daffa", "Egi", "Faisal", "Gilang", "Haikal", "Ivan", "Jibril",
    "Khalid", "Lukman", "Malik", "Naufan", "Rifqi", "Syahrul", "Tegar", "Usman", "Vikri", "Zulkifli",
]
FEMALE_NAMES = [
    "Siti", "Nur", "Aisyah", "Putri", "Dewi", "Sari", "Ayu", "Rina", "Fitri", "Indah",
    "Lestari", "Maya", "Nadia", "Olivia", "Permata", "Qonita", "Rahma", "Salsabila", "Tiara", "Umi",
    "Vina", "Wulan", "Yuni", "Zahra", "Amelia", "Bunga", "Citra", "Dinda", "Eka", "Farah",
    "Gita", "Hana", "Intan", "Jasmine", "Kirana", "Laila", "Mutiara", "Nabila", "Oktavia", "Prisca",
    "Queenza", "Rani", "Sakina", "Tari", "Ulfa", "Valen", "Widya", "Yasmin", "Zulfa", "Alya",
    "Balqis", "Callista", "Devi", "Elsa", "Fatimah", "Ghina", "Hafsah", "Ismi", "Kamila", "Lutfia",
]
# Family / second names shared between siblings and parents.
FAMILY_NAMES = [
    "Pratama", "Saputra", "Wijaya", "Hidayat", "Nugroho", "Santoso", "Ramadhan", "Kusuma", "Firmansyah", "Setiawan",
    "Hakim", "Permana", "Maulana", "Rahman", "Syahputra", "Utomo", "Wibowo", "Hasibuan", "Lubis", "Nasution",
    "Siregar", "Harahap", "Purnomo", "Susanto", "Kurniawan", "Hermawan", "Gunawan", "Suryadi", "Aziz", "Baihaqi",
    "Fauzi", "Ghifari", "Habibi", "Iskandar", "Jauhari", "Kamil", "Lazuardi", "Mahendra", "Nasir", "Oktaviani",
    "Purwanto", "Qodir", "Rosyid", "Sulaiman", "Tanjung", "Ubaidillah", "Vidiansyah", "Wardhana", "Yulianto", "Zainuddin",
]
BIRTH_CITIES = [
    "Jakarta", "Jakarta", "Jakarta", "Bekasi", "Depok", "Tangerang", "Bogor", "Bandung", "Surabaya",
    "Semarang", "Yogyakarta", "Medan", "Palembang", "Makassar", "Padang", "Serang", "Cirebon",
]
OCCUPATIONS = [
    "Pegawai Swasta", "Wiraswasta", "Karyawan BUMN", "PNS", "Guru", "Dosen", "Dokter", "Perawat",
    "Pedagang", "Pengusaha", "Ibu Rumah Tangga", "Konsultan", "Insinyur", "Akuntan", "Pengacara",
    "Sopir", "Buruh", "Petani", "Polisi", "TNI",
]
STAFF_UNIVERSITIES = [
    "Universitas Negeri Jakarta", "UIN Syarif Hidayatullah Jakarta", "Universitas Indonesia",
    "Universitas Pendidikan Indonesia", "Universitas Muhammadiyah Jakarta", "Universitas Islam Negeri Sunan Kalijaga",
    "Universitas Negeri Yogyakarta", "Institut Pertanian Bogor", "Universitas Negeri Semarang",
]

# (kelurahan, kecamatan, kode pos) — Jakarta Selatan.
LOCALITIES = [
    ("Cipete Selatan", "Cilandak", "12410"),
    ("Lebak Bulus", "Cilandak", "12440"),
    ("Pondok Labu", "Cilandak", "12450"),
    ("Gandaria Utara", "Kebayoran Baru", "12140"),
    ("Petogogan", "Kebayoran Baru", "12170"),
    ("Kramat Pela", "Kebayoran Baru", "12130"),
    ("Pasar Minggu", "Pasar Minggu", "12520"),
    ("Cilandak Timur", "Pasar Minggu", "12560"),
    ("Jati Padang", "Pasar Minggu", "12540"),
    ("Pondok Pinang", "Kebayoran Lama", "12310"),
    ("Cipulir", "Kebayoran Lama", "12230"),
    ("Tebet Barat", "Tebet", "12810"),
]
STREETS = [
    "Jl. Fatmawati", "Jl. Cipete Raya", "Jl. Pondok Labu", "Jl. Kemang Selatan", "Jl. TB Simatupang",
    "Jl. Ciputat Raya", "Jl. Bango", "Jl. Pertanian", "Jl. Damai", "Jl. Melati", "Jl. Mawar", "Jl. Kenanga",
    "Jl. Anggrek", "Jl. Cempaka", "Jl. Flamboyan", "Jl. Nusantara", "Jl. Pendidikan", "Jl. Masjid Al-Ikhlas",
]

# Subject catalogue: code -> (name, is_religious, weekly periods, dapodik code)
_S = {
    "PAI": ("Pendidikan Agama Islam dan Budi Pekerti", True, 3),
    "BAQ": ("Baca Tulis Al-Qur'an", True, 2),
    "PPKN": ("Pendidikan Pancasila dan Kewarganegaraan", False, 2),
    "BIN": ("Bahasa Indonesia", False, 4),
    "MTK": ("Matematika", False, 4),
    "IPAS": ("Ilmu Pengetahuan Alam dan Sosial", False, 4),
    "IPA": ("Ilmu Pengetahuan Alam", False, 4),
    "IPS": ("Ilmu Pengetahuan Sosial", False, 3),
    "BING": ("Bahasa Inggris", False, 3),
    "PJOK": ("Pendidikan Jasmani, Olahraga dan Kesehatan", False, 2),
    "SBK": ("Seni Budaya", False, 2),
    "PRK": ("Prakarya", False, 2),
    "INF": ("Informatika", False, 2),
    "BARAB": ("Bahasa Arab", True, 2),
    "SEJ": ("Sejarah", False, 2),
    "FIS": ("Fisika", False, 3),
    "KIM": ("Kimia", False, 3),
    "BIO": ("Biologi", False, 3),
    "MTKL": ("Matematika Tingkat Lanjut", False, 3),
    "EKO": ("Ekonomi", False, 3),
    "GEO": ("Geografi", False, 3),
    "SOS": ("Sosiologi", False, 3),
    "BK": ("Bimbingan Konseling", False, 1),
}
SUBJECT_CATALOG = {code: {"name": v[0], "is_religious": v[1], "credit_hours": v[2]} for code, v in _S.items()}

# Level -> {grade: [class-name prefix...]} is built in the command; here the
# subject codes taught per level / stream.
SD_SUBJECTS = ["PAI", "BAQ", "PPKN", "BIN", "MTK", "IPAS", "PJOK", "SBK", "BING"]
SMP_SUBJECTS = ["PAI", "BAQ", "PPKN", "BIN", "MTK", "IPA", "IPS", "BING", "INF", "PJOK", "SBK", "PRK"]
SMA_COMMON = ["PAI", "PPKN", "BIN", "MTK", "BING", "SEJ", "PJOK", "SBK", "INF"]
SMA_IPA = ["FIS", "KIM", "BIO", "MTKL"]
SMA_IPS = ["EKO", "GEO", "SOS"]

# Who teaches which subjects in each school level, ordered.  SD homeroom
# teachers cover the generalist subjects; specialists cover the rest.
SD_SPECIALISTS = [["PAI", "BAQ"], ["PJOK", "SBK"], ["BING"]]
SMP_TEACHERS = [
    ["PAI", "BAQ"], ["PAI", "BAQ"], ["PPKN"], ["BIN"], ["BIN"], ["MTK"], ["MTK"], ["IPA"], ["IPA"],
    ["IPS"], ["IPS"], ["BING"], ["BING"], ["INF"], ["PJOK"], ["SBK", "PRK"], ["PRK"],
]
SMA_TEACHERS = [
    ["PAI"], ["PAI"], ["PPKN"], ["BIN"], ["BIN"], ["MTK"], ["MTK"], ["MTKL"], ["BING"], ["BING"],
    ["SEJ"], ["PJOK"], ["SBK"], ["INF"], ["FIS"], ["FIS"], ["KIM"], ["KIM"], ["BIO"], ["BIO"],
    ["EKO"], ["EKO"], ["GEO"], ["SOS"],
]

# Base monthly fee (SPP) and one-off fees per school level, in IDR.
SPP_BY_LEVEL = {"SD": 750000, "SMP": 950000, "SMA": 1250000}
