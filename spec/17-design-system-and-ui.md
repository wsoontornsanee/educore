# 17 — Design System and UI Specification

## 1. Overview & Surfaces

This document defines the official design system, component specifications, interaction patterns, and surface mockups for **EduCore Indonesia**. It pairs directly with the functional specifications in `spec/00` through `spec/16`.

| Attribute | Specification |
|---|---|
| **Version** | 1.0 — September 2026 |
| **Primary Surfaces** | Web Admin (1440px desktop) · Teacher Suite (Web & Tablet) · Parent Mobile (React Native) · Canteen POS (1024×768 Tablet) · Gate Kiosk |
| **Implementation** | Django templates + HTMX + Tailwind CSS (compiled build-time, no Node runtime in production) · React Native (Expo) in `/mobile` |
| **Primary Language** | Bahasa Indonesia (`id-ID`) first; English secondary |

---

## 2. Core Design Principles

| ID | Principle | Rule |
|---|---|---|
| `UI-001` | **Red is for Action** | Brand red (`#C8102E`) marks the single most important action or hero KPI on a screen. If three things are red, nothing is. Everything else is warm ink on paper. |
| `UI-002` | **Numbers are the Interface** | Bendahara, Kepala Sekolah, and Ketua Yayasan read figures, not prose. Money and counts get tabular monospace typography, right alignment, and prominent size. |
| `UI-003` | **Built for a 3G Phone** | Every screen explicitly defines its loading, stale, and offline states. Silence or an indefinite loading spinner is never an acceptable state. |
| `UI-004` | **Bahasa Indonesia First** | Indonesian is the source language; English is the translation. Layouts and labels must tolerate 30% text expansion without breaking or truncating critical figures. |

---

## 3. Foundations: Colour Palette

### 3.1 Brand — Merah (Flag-Saturated Red)
A white-dominant surface with a single saturated red drawn from the Indonesian national flag.

```
brand-50   #FFF5F6   Subtle highlights, alert backgrounds
brand-100  #FDE7EA   Active tint, selected row background
brand-200  #F8C4CC   Soft border tint
brand-600  #C8102E   PRIMARY BRAND — main action, active tab, hero KPI
brand-700  #A50D26   Hover state
brand-800  #8E0B20   Active / pressed state
```

### 3.2 Neutral — Warm Ink and Paper
Neutrals are warmed slightly so large white canvas areas read as administrative paper rather than screen glare.

```
ink-900     #16110F   Primary body text, high-contrast headings
ink-700     #3A302C   Secondary headings, active menu labels
ink-500     #6B615C   Muted text, helper labels, timestamps
line        #E5DDD9   Borders, card dividers, table row lines
surface-alt #F7F4F2   App canvas background, neutral tags, zebra rows
surface     #FFFFFF   Card background, modal surface, paper white
```

### 3.3 Semantic — Independent of Brand Red

| State | Role | Hex Value | Background Tint | Context |
|---|---|---|---|---|
| **Success** | Lunas, Hadir, Berhasil | `#0E7A4F` | `#E6F4EE` | Paid invoices, present attendance, active gates |
| **Warning** | Jatuh Tempo, Terlambat | `#B56A00` | `#FDF1E0` | Upcoming due dates, tardiness, expiring cards |
| **Danger** | Destructive, Alpa, Gagal | `#B3261E` | `#FCE9E7` | Destructive deletes, absent unexcused, errors |
| **Info** | Sistem, Pengumuman | `#1B5FA8` | `#E8F0FA` | System notices, sick leave (Sakit), guidance |

> [!CRITICAL]
> **Critical Color Rule:** Brand red `#C8102E` means **primary action**. Destructive actions use `#B3261E`, which is visibly browner/darker and **always** requires a confirmation dialog. Never use brand red to signal a form error or system failure.

### 3.4 Attendance Status Vocabulary (`spec/05 §3`)
Standardized tokens used identically across web, mobile, and printed report cards (rapor):

```
HADIR      #0E7A4F on #E6F4EE   ● Present on time
TERLAMBAT  #B56A00 on #FDF1E0   ● Late arrival with recorded minutes
SAKIT      #1B5FA8 on #E8F0FA   ● Sick leave verified with doctor note
IZIN       #6B615C on #F0ECE9   ● Excused absence with parental letter
ALPA       #B3261E on #FCE9E7   ● Unexcused absence triggering parent alert
DISPEN     #7C3AED on #F3E8FF   ● School dispensational duty / competition
```
*Rule:* Every status badge carries both a solid dot and an uppercase label. Colour is never the sole carrier of meaning.

---

## 4. Foundations: Typography

EduCore uses three purposeful type families:

1. **`Plus Jakarta Sans`** (Weights 700, 800 only):
   - Headings, page titles, and hero KPI figures.
   - An Indonesian-designed geometric typeface chosen deliberately for national alignment.
2. **`IBM Plex Sans`** (Weights 400, 500, 600):
   - All UI copy, form inputs, table data, body text, and alerts.
   - Exceptional legibility at 14px on low-DPI Android devices.
3. **`IBM Plex Mono`** (Weights 400, 500, 600):
   - Monetary figures, NISN, NIK, Virtual Account numbers, RFID UIDs, timestamps, and codes.
   - Tabular numerals ensure financial ledgers align vertically on decimal points.

### 4.1 Type Hierarchy Scale

| Token | Size / Leading | Weight | Font Family | Example Usage |
|---|---|---|---|---|
| `display` | 48–64px / 1.1 | 800 | Plus Jakarta Sans | Hero financial totals: `Rp 1.482.500.000` |
| `h1` | 32px / 1.2 | 700 | Plus Jakarta Sans | Screen title: `Manajemen Keuangan Yayasan` |
| `h2` | 22px / 1.3 | 700 | Plus Jakarta Sans | Card title: `Tagihan Belum Lunas` |
| `h3` | 18px / 1.4 | 600 | IBM Plex Sans | Sub-section / table group header |
| `body` | 15px / 1.5 | 400 | IBM Plex Sans | Standard interface text, list descriptions |
| `body-sm` | 13px / 1.4 | 400/500 | IBM Plex Sans | Helper notes, freshness metadata: `Diperbarui 4 mnt lalu` |
| `label` | 11px / 1.2 | 600 | IBM Plex Mono | Overline, data key, table header: `NISN · 0071234567` |

### 4.2 Typographic Guardrails
- **Minimum font sizes:** 13px web floor, 14sp mobile floor, 12pt in printed rapor and official payslips.
- **Reading comfort:** Line length capped at 75 characters (`max-w-prose`).
- **Alignment:** Never center-align paragraphs longer than two lines.

---

## 5. Foundations: Space, Shape & Elevation

### 5.1 Spacing Scale (4px Base)
```
4px   Icon-to-text gap
8px   Label-to-input gap, inline element spacing
12px  Button group gap, chip list padding
16px  Compact card padding, table cell vertical padding
24px  Standard card padding, form grid row gap
32px  Section margin, dashboard card gap
48px  Major section divider gap
64px  Outer page layout gutters
```

### 5.2 Shape & Border Radii
EduCore embraces an **institutional, crisp aesthetic**:
- `0px` (Square sharp corners): Cards, data tables, metrics panels, modals, and tabs. Avoids consumer-app soft pill rounding in enterprise admin views.
- `4px`: Action buttons, text input fields, select menus.
- `9999px` (Full round): Status indicator dots, user avatars, pill badges.

### 5.3 Elevation & Layering
- `flat` (Default): `1px solid #E5DDD9` border, no shadow. Used for 95% of cards and panels.
- `raised`: `box-shadow: 0 4px 12px rgba(22, 17, 15, 0.08)` for dropdowns and popovers.
- `overlay`: `box-shadow: 0 16px 32px rgba(22, 17, 15, 0.16)` with 40% `#16110F` backdrop for modal dialogs and slide-over drawers.
- *Rule:* Shadows convey physical elevation layer, never surface decoration.

---

## 6. Component Library Specifications

### 6.1 Buttons

| Variant | Height | Styling | Usage |
|---|---|---|---|
| **Primary** | 40px (Web) / 48px (Mobile/POS) | `bg-brand-600 text-white hover:bg-brand-700` | Single primary action per view (e.g. `Terbitkan Tagihan`) |
| **Secondary** | 40px / 48px | `border border-line bg-surface text-ink-900 hover:bg-surface-alt` | Alternative action (e.g. `Simpan Draf`) |
| **Ghost** | 40px / 32px | `text-ink-700 hover:bg-surface-alt` | Table row action (e.g. `Lihat Detail`) |
| **Destructive** | 40px / 48px | `bg-danger text-white hover:opacity-90` | Hard mutations (e.g. `Batalkan Tagihan`), requires confirm modal |

- **Loading State:** Disabled button showing `"Memproses..."` with an animated circle pulse; form inputs freeze to prevent double submission.

### 6.2 Form Inputs
- **Floating/Above Labels:** Labels are permanently rendered above input fields. Placeholders never replace labels.
- **Helper & Validation Text:** Displayed below the field in 13px (`ink-500` for helper, `danger` for errors).
- **Rupiah Input Contract (`CUR-013`):**
  - Currency prefix `Rp` is rendered inside the field as an immutable decorative addon.
  - Formatter automatically inserts thousands dots on keystroke (`1.350.000`).
  - Input masks disallow decimal commas or periods for IDR.
  - Submits pure string `"1350000.00"` to backend endpoints.

### 6.3 Data Tables
- **Row Height:** Exactly 52px for scannable data density.
- **Sticky Header:** Table header stays anchored on scroll with `1px solid #E5DDD9` border.
- **Numeric Alignment:** All currency, counts, percentages, and dates are right-aligned in `IBM Plex Mono`.
- **Zebra Striping:** Alternating rows (`surface` and `surface-alt`) enabled on tables with >8 columns.

### 6.4 Badges & Status Chips
- Machine statuses rendered in uppercase monospace: `LUNAS`, `JATUH TEMPO`, `SEBAGIAN`, `DRAF`, `MENUNGGAK 45 HARI`.
- Human notifications rendered in sentence case with leading iconography.

---

## 7. Production Surface Layouts & Mockups

### 7.1 Foundation Dashboard (Desktop 1440px)
- **Header:** Foundation switcher (`Yayasan Al-Hikmah Nusantara · 3 unit sekolah`), current active period (`September 2026`), reporting currency notice (`mata uang pelaporan IDR`).
- **Hero Metrics Bar (4 KPI Cards):**
  1. *Tingkat Penagihan:* `94,2%` (with `▲ 3,1 poin vs Agustus` delta).
  2. *Terkumpul Bulan Ini:* `Rp 1,48 M` (`▲ Rp 112 jt`).
  3. *Tunggakan Total:* `Rp 91,4 jt` (`64 siswa · 31 lewat 60 hari`).
  4. *Kehadiran Hari Ini:* `96,8%` (`1.842 dari 1.903 siswa`).
- **Chart Area:** 6-month SPP revenue inflow trend (`Apr – Sep`).
- **Approval Drawer:** Quick action widget for fee relief (`Keringanan SPP`) and refund requests.
- **Unit Comparison Table:** Side-by-side performance matrix for SD, SMP, and SMA campuses.

### 7.2 Live Gate Console (Wall Monitor / Guard Terminal)
- **Realtime Mode:** 3-second HTTP polling (`ARC-015`), zero persistent socket requirements.
- **Header Status:** Gate connection pills (`Gerbang Utama: Hijau`, `Gerbang Timur: Kuning/Luring`).
- **Event Feed:** Instant chronological cards showing student photo, full name, class (`VII-A`), auth method (`RFID` vs `Wajah 0,94`), timestamp (`06:55`), and badge (`HADIR`, `TERLAMBAT`).
- **Edge Diagnostics Drawer:** Offline queue counter (`142 scan tersimpan di gateway`), WhatsApp dispatch counter (`631 terkirim, rata-rata jeda 2,8s, 3 gagal`).

### 7.3 Teacher Gradebook (Spreadsheet Keyboard-First)
- **Input Pattern:** Standard spreadsheet controls — `Enter` moves downward, `Tab` moves rightward, `Esc` reverts cell edit.
- **Kurikulum Merdeka Layout:** Grouped by Capaian Pembelajaran (TP 1.1, TP 1.2), Formative weights (10%, 20%), Summative weights (30%), Semester exams (40%), and auto-computed final score.
- **Auto-Save Status:** Header displays `Tersimpan otomatis` with green pulse on keystroke flush.

### 7.4 Parent Mobile App (React Native 375×812)
- **Beranda (Home):**
  - Instant gate status banner: `SUDAH DI SEKOLAH · 06.55 WIB` (Entry via Gerbang Utama, scheduled return 15.30).
  - Urgent SPP due card: `Rp 1.350.000 (H-3)` with quick `Bayar Sekarang` CTA.
  - Canteen balance card: `Saldo: Rp 45.000` with instant top-up shortcut.
- **Pembayaran (Payment):**
  - Itemized invoice breakdown: Base SPP (`1.350.000`), Extracurricular (`150.000`), Sibling discount (`−135.000`), Platform convenience fee (`4.000`), Rounding adjustment `Pembulatan` (`−50`), Final total `Rp 1.368.950`.
  - Payment method selector: Dedicated BCA Virtual Account, QRIS (instant dynamic image), manual bank transfer with receipt upload.
- **Dompet Kantin (Canteen Wallet):**
  - Stored value balance and daily spending limit indicator (`Batas harian: Rp 20.000 · Terpakai: Rp 12.000`).
  - Itemized transaction receipt stream with parental food restriction badges (`Minuman bersoda — Ditolak orang tua`).

### 7.5 Canteen POS Terminal (1024×768 Tablet)
- **Header:** Terminal ID (`Terminal 2`), active session (`12.00–13.00`), operator name, offline queue counter (`LURING · 47 transaksi antre`).
- **Left Column (Menu Grid):** High-contrast 48px touch cards grouped into *Semua*, *Makanan Berat*, *Roti*, *Minuman*, *Buah*. Product badges show dietary markers (`SEHAT`, `BLOKIR`).
- **Right Column (Cart & Student Scan):** Student photo, name, grade, remaining wallet balance (`Rp 45.000`), and daily quota tracker. One-tap payment button (`Bayar dengan Saldo`).

---

## 8. Interaction, Accessibility & Implementation Rules

### 8.1 Mandatory Five Screen States
Every frontend template and viewset MUST define five standard states:

```
1. LOADING  Skeleton screen matching exact destination grid. No centered spinner.
2. EMPTY    Explains why data is absent and provides the button to create it.
3. STALE    Displays "Diperbarui X menit yang lalu" on all rolled-up/cached metrics.
4. OFFLINE  Persistent warning banner. Writes queue locally; reads show last-known time.
5. ERROR    Human Indonesian message stating what happened and how to recover.
```

### 8.2 Accessibility Standard
- **Contrast Ratios:** Minimum 4.5:1 for body copy; 3:1 for text ≥24px. White text on `#C8102E` primary red achieves **5.9:1** (fully WCAG AA compliant).
- **Minimum Touch Targets:** 44×44dp on mobile devices, 48×48px on POS and kiosk touchscreens.
- **Visible Focus States:** 2px `#C8102E` outline with a 2px outer offset. Never suppressed with `outline: none`.
- **Fluid Scalability:** All typography and layout containers must remain legible and unclipped at 200% browser zoom.
- **Motion:** Transitions capped between 120ms and 200ms ease-out. All CSS animations respect `prefers-reduced-motion: reduce`.

### 8.3 Frontend Handoff Architecture
- Design tokens defined in `frontend/tailwind/tailwind.config.js` and mirrored in `mobile/theme.js`.
- Modular UI components live in `frontend/templates/components/` as HTMX-ready partials (`button.html`, `input.html`, `badge.html`, `card.html`, `table.html`).
- Every view reuses existing partials; no screen implements rogue inline styles or unvetted color codes.
