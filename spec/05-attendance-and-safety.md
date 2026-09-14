# 05 — Attendance and Campus Safety

## 1. Scope

Gate check-in/out via RFID/NFC card and face recognition, in-class attendance,
authorised pickup, bus tracking, and the notification triggers that make parents
feel the product working on day one.

## 2. Entities

```
attendance_days(id, school_id, student_id, date, status, first_in_at, last_out_at, source, note)
gate_events(id, school_id, device_id, student_id?, credential_id?, direction[IN|OUT],
            occurred_at, method[RFID|FACE|MANUAL|KIOSK], confidence?, photo_key?, status)
class_attendance(id, class_subject_id, timetable_slot_id, date, student_id, status, recorded_by, recorded_at)
absence_requests(id, student_id, date_from, date_to, type[SAKIT|IZIN], reason, attachment_key?, status, decided_by)
credentials(id, student_id|staff_id, type[RFID|NFC|QR], uid, issued_at, revoked_at, status)
pickup_authorizations(id, student_id, person_name, relation, phone, photo_key, valid_from, valid_to, one_time)
pickup_events(id, student_id, authorized_by_guardian_id, picked_up_by, verified_by_staff_id, occurred_at, method)
bus_routes(id, school_id, name, driver_staff_id, vehicle_plate, geofences jsonb)
bus_events(id, route_id, student_id, type[BOARD|ALIGHT], stop_id?, occurred_at, lat, lng)
```

## 3. Attendance status model

Fixed vocabulary (Indonesian school convention) — do not extend without migration:

| Code | Meaning | Set by |
|---|---|---|
| `HADIR` | Present | Gate scan or teacher |
| `TERLAMBAT` | Late | Gate scan after `late_after` time |
| `SAKIT` | Sick (excused) | Approved absence request |
| `IZIN` | Permitted absence | Approved absence request |
| `ALPA` | Unexcused absence | System default at cutoff |
| `DISPEN` | School-sanctioned dispensation | Staff only |

| ID | Requirement |
|---|---|
| `ATT-001` | Daily status MUST derive automatically: first `IN` scan before `late_after` → `HADIR`; after → `TERLAMBAT`; no scan by `absent_cutoff` and no approved request → `ALPA`. |
| `ATT-002` | An approved absence request MUST override a derived status for the covered dates. |
| `ATT-003` | Staff override of a derived status MUST require a note and be audited; the original derived value is retained. |
| `ATT-004` | Class-period attendance MUST be independent of daily gate attendance (a student can be on campus but skip a period). Daily status MUST NOT be recomputed from period records. |
| `ATT-005` | Non-school days (weekends, national holidays, school holidays from the academic calendar) MUST NOT generate `ALPA`. |

## 4. Gate check-in

| ID | Requirement |
|---|---|
| `ATT-006` | Gate scan → parent notification dispatched within 5 seconds (`NFR-002`). |
| `ATT-007` | Duplicate scans of the same credential within `debounce_seconds` (default 120) MUST be recorded but MUST NOT re-notify. |
| `ATT-008` | Direction MUST be inferred per-device (`device.direction = IN | OUT | BIDIRECTIONAL`); for bidirectional devices, alternate from the student's last event that day. |
| `ATT-009` | Unknown/revoked credential MUST record a `gate_events` row with `status=REJECTED` and raise a live alert on the admin gate console. |
| `ATT-010` | Face recognition MUST store templates (not raw images) encrypted with a dedicated KMS key; a match below `confidence_threshold` (default 0.88) falls back to card or manual verification and is never auto-accepted. |
| `ATT-011` | Face enrolment MUST require explicit written guardian consent recorded with timestamp and consent version; without consent the student MUST be card-only. |
| `ATT-012` | Offline gate: the edge agent MUST buffer scans locally and replay on reconnect; replayed events keep their original `occurred_at` and are marked `replayed=true` (see 12 §4). |
| `ATT-013` | Live gate console MUST show arrivals within 3 seconds via cursor polling with student photo, name, class and timestamp, and support manual check-in for a forgotten card in ≤3 taps. |

## 5. Pickup safety

| ID | Requirement |
|---|---|
| `ATT-014` | Only persons on `pickup_authorizations` (or guardians with `can_pickup=true`) may collect a student. |
| `ATT-015` | A guardian MUST be able to create a one-time pickup authorisation from the parent app, producing a QR code valid for a specified window. |
| `ATT-016` | Staff verification screen MUST show the authorised person's photo and name before release, and record `verified_by_staff_id`. |
| `ATT-017` | A completed pickup MUST notify all linked guardians, including those not involved. |
| `ATT-018` | Any release of a student to a non-authorised person MUST be possible only via a `school_admin` override with mandatory reason, and generates a high-priority audit event. |

## 6. Bus tracking

| ID | Requirement |
|---|---|
| `ATT-019` | Board/alight events MUST be captured by the driver's handheld (card tap) and geotagged. |
| `ATT-020` | Geofence entry near a student's stop MUST notify that student's guardians ~5 minutes out (configurable). |
| `ATT-021` | An "unaccounted student" check MUST run at route end: any student boarded but not alighted raises an immediate alert to school admin and guardians. |
| `ATT-022` | Live map for guardians MUST show only their child's route and only during the active run window. |

## 7. Absence requests

| ID | Requirement |
|---|---|
| `ATT-023` | Guardian submits type, dates, reason and optional attachment (doctor's note, ≤10MB). |
| `ATT-024` | Homeroom teacher or school admin approves/rejects; decision notifies the guardian. |
| `ATT-025` | Requests for past dates beyond `backdate_limit_days` (default 7) MUST require admin approval, not teacher. |

## 8. API

```
POST /gate/events                      (edge agent, batch, idempotent)  {events:[...]}
GET  /gate/live?school_id&since=<cursor>   (3s polling — see ARC-015; no WebSocket)
POST /attendance/manual                {student_id, direction, occurred_at, reason}
GET  /attendance/daily?school_id&date&class_id
PATCH /attendance/daily/:id            {status, note}
GET  /attendance/class?class_subject_id&date
PUT  /attendance/class                 {date, records:[{student_id,status}]}
POST /absence-requests | POST /absence-requests/:id/decide
GET  /credentials?student_id | POST /credentials | POST /credentials/:id/revoke
POST /pickup-authorizations | POST /pickup/verify {qr_token|authorization_id}
GET  /bus/routes/:id/live | POST /bus/events
GET  /attendance/reports/summary?from&to&group_by=class|student
```

## 9. Acceptance criteria

1. A card tap at 06:55 produces a parent WhatsApp message before 07:00:05.
2. Three taps in 30 seconds produce three `gate_events` and exactly one notification.
3. A gate offline for 2 hours replays 180 buffered scans on reconnect with no duplicates and correct original timestamps.
4. A student with an approved `SAKIT` request is never marked `ALPA` for those dates.
5. A pickup QR used outside its validity window is rejected with `PICKUP_WINDOW_EXPIRED`.
6. Face enrolment is impossible for a student without a recorded consent record.
