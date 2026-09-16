# Parent Mobile App v1 (Step 10.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship OTP login, a per-child home/attendance/invoice view, and VA/QRIS payment for guardians, bolted onto the existing `mobile/` (educore-guru) Expo app without touching the teacher tree.

**Architecture:** Two new Django endpoints in `apps.identity` (OTP login, child list) plus zero changes to the already guardian-scoped Invoice/Attendance/PaymentIntent endpoints. On the client, `App.tsx` branches on `user.roles` after login into an entirely separate parent screen tree; a small `cache.ts` helper gives every parent read-screen the same offline-cache-with-staleness-stamp behavior.

**Tech Stack:** Django 5 / DRF / SimpleJWT (backend, `apps/identity`), React Native (Expo) + TypeScript, `node --test` for mobile unit tests, `django.test.TestCase` + DRF `APIClient` for backend tests.

## Global Constraints

- Money: never format/sum on the client — `MoneyField` API values (`{"amount": "...", "currency": "..."}` shape via DRF `DecimalField(coerce_to_string=True)`) are rendered as-is; totals come from `PaymentIntentSerializer`/`InvoiceSerializer`, never computed client-side.
- 3-layer tenancy: every new Django queryset filters by `foundation_id` from `get_current_foundation_id()`/`request.foundation_id` and every new endpoint has an explicit `required_permission` (fail-closed per `HasRequiredPermission`, IAM-010).
- `id-ID` first: every user-facing string in new screens/error messages is Indonesian, matching existing screens (`LoginScreen.tsx`, `StaleOfflineBanner.tsx`).
- Institutional square design: reuse `mobile/src/theme/tokens.ts` (`colors`, `typography`, `spacing`, `radius` — radius is always `0`). No new tokens are introduced.
- No PII in logs: never `console.log`/print a phone number, OTP code, or child name.
- Test coverage ≥80% on new business logic (AGENTS.md §4). Every new service/view/screen ships with a test in the same task.
- **Deviation from the design doc, resolved during planning:** the design doc's §3.3 assumed a "payment-settled push" already exists to refresh the invoice list. It does not — no notification category or push payload for payment settlement exists anywhere in the codebase today, and wiring one is new `apps.notifications`/webhook backend work outside this slice's stated backend scope (§2 says "reused, unchanged"). Task 12 below instead polls `GET /payment-intents/:id/` every 3s while `PaymentScreen` is open (capped at the intent's `expires_at`), which satisfies spec/08's acceptance criterion #2 ("invoice list reflects it within 30s... without pulling to refresh") with zero backend changes. This is a corrected implementation choice, not a scope change — flag it in the PR description.
- **Resolved from the design doc's open §7 item:** `apps.identity.guardian_access` and every existing guardian-scoped viewset (`InvoiceViewSet`, `AttendanceDayViewSet`, `PaymentIntentViewSet`) gate on `HasRequiredPermission`, which requires an actual `RoleAssignment(role='parent', ...)` row for the user (see `apps/identity/rbac.py` `ROLE_PERMISSIONS[ROLE_PARENT]` and `apps/identity/tests/test_guardian_cross_domain.py:116-121`, which manually creates one in test setup). No production code path creates this row today — `POST /students/:id/guardians/` (`apps/identity/views.py` guardian-link handler) creates the `User` but never a `RoleAssignment`. This is a real, previously-undiscovered gap: **without Task 2's fix, no guardian can pass any existing guardian-scoped endpoint in production.** Task 2 closes it by lazily ensuring the role assignment at OTP-verify time.

---

### Task 1: OTP request endpoint

**Files:**
- Modify: `apps/identity/serializers.py` (add `OtpRequestSerializer`, `OtpVerifySerializer` — both used by Task 1 and Task 2)
- Modify: `apps/identity/views.py` (add `RequestOtpView`)
- Modify: `apps/identity/urls.py` (add `auth/otp/request/`)
- Test: `apps/identity/tests/test_parent_otp_auth.py` (new file, shared by Tasks 1 and 2)

**Interfaces:**
- Consumes: `apps.identity.services.request_phone_otp(phone: str) -> tuple[OTPChallenge, str]` (existing, unchanged).
- Produces: `POST /api/v1/auth/otp/request/` → `201 {"challenge_id": <int>}` on success, `400 {"error": "<id-ID message>"}` on throttle/invalid phone. Task 2 needs no symbol from this task beyond the URL path.

- [ ] **Step 1: Write the failing test**

```python
# apps/identity/tests/test_parent_otp_auth.py
"""Tests for guardian OTP login endpoints (spec/08 PAR-001, spec/02 IAM-002/003)."""
from django.test import TestCase
from rest_framework.test import APIClient
from apps.identity.models import OTPChallenge


class OtpRequestEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_request_otp_returns_challenge_id(self):
        response = self.client.post(
            '/api/v1/auth/otp/request/',
            {'phone_e164': '+6281234567890'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertIn('challenge_id', response.data)
        self.assertTrue(
            OTPChallenge.objects.filter(id=response.data['challenge_id']).exists()
        )

    def test_request_otp_throttles_after_three_sends(self):
        for _ in range(3):
            self.client.post(
                '/api/v1/auth/otp/request/',
                {'phone_e164': '+6281234567891'},
                format='json',
            )
        response = self.client.post(
            '/api/v1/auth/otp/request/',
            {'phone_e164': '+6281234567891'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.data)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.identity.tests.test_parent_otp_auth -v 2`
Expected: FAIL — `404` (no such URL) on both tests.

- [ ] **Step 3: Write minimal implementation**

```python
# apps/identity/serializers.py — append near EduCoreTokenObtainPairSerializer
class OtpRequestSerializer(serializers.Serializer):
    phone_e164 = serializers.CharField()


class OtpVerifySerializer(serializers.Serializer):
    challenge_id = serializers.IntegerField()
    code = serializers.CharField(max_length=6, min_length=6)
```

```python
# apps/identity/views.py — add imports and view
from django.core.exceptions import ValidationError as DjangoValidationError
from .serializers import OtpRequestSerializer, OtpVerifySerializer
from .services import request_phone_otp, verify_phone_otp


class RequestOtpView(views.APIView):
    """POST /api/v1/auth/otp/request/ — guardian OTP login, step 1 (PAR-001, IAM-002)."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = OtpRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            challenge, _raw_code = request_phone_otp(serializer.validated_data['phone_e164'])
        except DjangoValidationError as exc:
            return Response({'error': exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'challenge_id': challenge.id}, status=status.HTTP_201_CREATED)
```

```python
# apps/identity/urls.py — add import and path
from .views import RequestOtpView
# ... inside urlpatterns, alongside 'auth/token/':
    path('auth/otp/request/', RequestOtpView.as_view(), name='otp-request'),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.identity.tests.test_parent_otp_auth -v 2`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/identity/serializers.py apps/identity/views.py apps/identity/urls.py apps/identity/tests/test_parent_otp_auth.py
git commit -m "feat(identity): add guardian OTP request endpoint (PAR-001)"
```

---

### Task 2: OTP verify endpoint — issues JWT, lazily ensures parent RoleAssignment

**Files:**
- Modify: `apps/identity/views.py` (add `VerifyOtpView`)
- Modify: `apps/identity/urls.py` (add `auth/otp/verify/`)
- Modify: `apps/identity/tests/test_parent_otp_auth.py` (append tests)

**Interfaces:**
- Consumes: `apps.identity.services.verify_phone_otp(challenge_id, code) -> tuple[bool, str]` (existing); `apps.identity.rbac.assign_role(user, role, scope_type, scope_id, foundation_id=None, created_by=None) -> RoleAssignment` (existing, idempotent via `update_or_create`); `apps.identity.models.OTPChallenge`, `apps.identity.models.User`, `apps.identity.models.RoleAssignment.ROLE_PARENT`/`SCOPE_FOUNDATION`.
- Produces: `POST /api/v1/auth/otp/verify/` → `200 {"access": str, "refresh": str, "user": {...same shape as EduCoreTokenObtainPairSerializer's 'user' dict...}}`. Task 4 (mobile `parentAuth.ts`) consumes exactly this response shape.

- [ ] **Step 1: Write the failing test**

```python
# apps/identity/tests/test_parent_otp_auth.py — append
from django.contrib.auth.hashers import make_password
from django.utils import timezone
from datetime import timedelta
from apps.identity.models import Foundation, Guardian, GuardianLink, Person, RoleAssignment, School, Student, User
from apps.identity.services import normalize_phone_e164


class OtpVerifyEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Cahaya Ilmu", brand_name="Cahaya Ilmu"
        )
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SD Cahaya Ilmu", npsn="12345678", level="SD"
        )
        self.phone = normalize_phone_e164("081200000001")
        self.guardian_user = User.all_tenants.create_user(
            phone_e164=self.phone,
            full_name="Ibu Siti",
            foundation_id=self.foundation.id,
        )
        person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name="Siti Aminah")
        self.guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation.id, person=person, user=self.guardian_user
        )
        student_person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name="Ahmad Kecil")
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=student_person, nis="2026001"
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id, guardian=self.guardian, student=self.student,
            financial_responsible=True,
        )
        self.challenge = OTPChallenge.objects.create(
            phone_e164=self.phone,
            code_hash=make_password("111111"),
            expires_at=timezone.now() + timedelta(minutes=5),
        )

    def test_verify_otp_issues_jwt_and_grants_parent_role(self):
        self.assertFalse(
            RoleAssignment.all_tenants.filter(
                user=self.guardian_user, role=RoleAssignment.ROLE_PARENT
            ).exists()
        )

        response = self.client.post(
            '/api/v1/auth/otp/verify/',
            {'challenge_id': self.challenge.id, 'code': '111111'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertEqual(response.data['user']['phone_e164'], self.phone)

        self.assertTrue(
            RoleAssignment.all_tenants.filter(
                user=self.guardian_user,
                role=RoleAssignment.ROLE_PARENT,
                scope_type=RoleAssignment.SCOPE_FOUNDATION,
                scope_id=self.foundation.id,
                deleted_at__isnull=True,
            ).exists()
        )

    def test_verify_otp_wrong_code_rejected(self):
        response = self.client.post(
            '/api/v1/auth/otp/verify/',
            {'challenge_id': self.challenge.id, 'code': '000000'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.data)

    def test_verify_otp_unregistered_phone_returns_404(self):
        unregistered_challenge = OTPChallenge.objects.create(
            phone_e164='+6289999999999',
            code_hash=make_password("222222"),
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        response = self.client.post(
            '/api/v1/auth/otp/verify/',
            {'challenge_id': unregistered_challenge.id, 'code': '222222'},
            format='json',
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data.get('code'), 'GUARDIAN_NOT_REGISTERED')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.identity.tests.test_parent_otp_auth -v 2`
Expected: FAIL — `404` (no such URL) on the three new tests.

- [ ] **Step 3: Write minimal implementation**

```python
# apps/identity/views.py — add view
from rest_framework_simplejwt.tokens import RefreshToken
from .rbac import assign_role
from .models import RoleAssignment, User as UserModel


class VerifyOtpView(views.APIView):
    """POST /api/v1/auth/otp/verify/ — guardian OTP login, step 2 (PAR-001, IAM-003)."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = OtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        ok, message = verify_phone_otp(data['challenge_id'], data['code'])
        if not ok:
            return Response({'error': message}, status=status.HTTP_400_BAD_REQUEST)

        from .models import OTPChallenge
        challenge = OTPChallenge.objects.get(id=data['challenge_id'])
        user = UserModel.all_tenants.filter(phone_e164=challenge.phone_e164).first()
        if not user:
            return Response(
                {'error': 'Nomor HP ini belum terdaftar sebagai wali murid.', 'code': 'GUARDIAN_NOT_REGISTERED'},
                status=status.HTTP_404_NOT_FOUND,
            )

        assign_role(
            user=user,
            role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=user.foundation_id,
            foundation_id=user.foundation_id,
        )

        refresh = RefreshToken.for_user(user)
        roles = list(
            RoleAssignment.all_tenants.filter(
                foundation_id=user.foundation_id, user=user, deleted_at__isnull=True
            ).values('id', 'role', 'scope_type', 'scope_id')
        )
        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'id': user.id,
                'full_name': user.full_name,
                'phone_e164': user.phone_e164,
                'email': user.email,
                'foundation_id': user.foundation_id,
                'roles': roles,
            },
        }, status=status.HTTP_200_OK)
```

```python
# apps/identity/urls.py
from .views import RequestOtpView, VerifyOtpView
# ...
    path('auth/otp/request/', RequestOtpView.as_view(), name='otp-request'),
    path('auth/otp/verify/', VerifyOtpView.as_view(), name='otp-verify'),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.identity.tests.test_parent_otp_auth -v 2`
Expected: PASS (5 tests total).

- [ ] **Step 5: Commit**

```bash
git add apps/identity/views.py apps/identity/urls.py apps/identity/tests/test_parent_otp_auth.py
git commit -m "feat(identity): OTP verify issues JWT and lazily grants parent RoleAssignment"
```

---

### Task 3: `GET /api/v1/me/children/`

**Files:**
- Modify: `apps/identity/serializers.py` (add `GuardianChildSerializer`)
- Modify: `apps/identity/views.py` (add `GuardianChildrenView`)
- Modify: `apps/identity/urls.py` (add `me/children/`)
- Test: `apps/identity/tests/test_guardian_children_endpoint.py` (new file)

**Interfaces:**
- Consumes: `apps.identity.guardian_access.is_staff_user(user, foundation_id) -> bool` (existing); `apps.identity.models.GuardianLink` (existing).
- Produces: `GET /api/v1/me/children/` → `200 [{"student_id": int, "full_name": str, "photo_key": str, "financial_responsible": bool}, ...]`, `403` for staff-only users. Task 5 (mobile `children.ts`) consumes exactly this array shape.

- [ ] **Step 1: Write the failing test**

```python
# apps/identity/tests/test_guardian_children_endpoint.py
"""Tests for GET /me/children/ (spec/08 PAR-003, PAR-017)."""
from django.test import TestCase
from rest_framework.test import APIClient
from apps.identity.models import (
    Foundation, Guardian, GuardianLink, Person, RoleAssignment, School, Staff, Student, User,
)
from apps.identity.services import create_user_with_person
from educore.middleware.tenancy import tenant_context


class GuardianChildrenEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.foundation = Foundation.objects.create(legal_name="Yayasan Nusantara", brand_name="Nusantara")
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Nusantara", npsn="87654321", level="SMP"
        )
        self.guardian_user = User.all_tenants.create_user(
            phone_e164="+6281200000010", full_name="Bapak Joko", foundation_id=self.foundation.id,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.guardian_user,
            role=RoleAssignment.ROLE_PARENT, scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )
        person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name="Joko Susilo")
        self.guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation.id, person=person, user=self.guardian_user
        )
        student_person_a = Person.all_tenants.create(foundation_id=self.foundation.id, full_name="Dewi Kecil")
        self.student_financial = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=student_person_a, nis="2026010"
        )
        student_person_b = Person.all_tenants.create(foundation_id=self.foundation.id, full_name="Budi Kecil")
        self.student_non_financial = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=student_person_b, nis="2026011"
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id, guardian=self.guardian, student=self.student_financial,
            financial_responsible=True,
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id, guardian=self.guardian, student=self.student_non_financial,
            financial_responsible=False,
        )

    def _auth(self, user):
        from rest_framework_simplejwt.tokens import RefreshToken
        token = RefreshToken.for_user(user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_returns_only_linked_children_with_financial_flag(self):
        self._auth(self.guardian_user)
        response = self.client.get('/api/v1/me/children/')
        self.assertEqual(response.status_code, 200)
        by_id = {row['student_id']: row for row in response.data}
        self.assertEqual(len(by_id), 2)
        self.assertTrue(by_id[self.student_financial.id]['financial_responsible'])
        self.assertFalse(by_id[self.student_non_financial.id]['financial_responsible'])

    def test_staff_user_gets_403(self):
        with tenant_context(self.foundation.id):
            staff_user, staff_person = create_user_with_person(
                foundation_id=self.foundation.id, full_name="Guru Andi", phone="081200000099",
            )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=staff_user,
            role=RoleAssignment.ROLE_TEACHER, scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school.id,
        )
        self._auth(staff_user)
        response = self.client.get('/api/v1/me/children/')
        self.assertEqual(response.status_code, 403)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.identity.tests.test_guardian_children_endpoint -v 2`
Expected: FAIL — `404` (no such URL).

- [ ] **Step 3: Write minimal implementation**

```python
# apps/identity/serializers.py — append
class GuardianChildSerializer(serializers.Serializer):
    student_id = serializers.IntegerField(source='student.id')
    full_name = serializers.CharField(source='student.person.full_name')
    photo_key = serializers.CharField(source='student.photo_key')
    financial_responsible = serializers.BooleanField()
```

```python
# apps/identity/views.py — add view
from .guardian_access import is_staff_user
from .models import GuardianLink


class GuardianChildrenView(views.APIView):
    """GET /api/v1/me/children/ — child switcher list for the authed guardian (PAR-003, PAR-017)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if is_staff_user(request.user, foundation_id):
            return Response(
                {'error': 'Endpoint ini khusus untuk akun wali murid.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        links = GuardianLink.all_tenants.filter(
            foundation_id=foundation_id,
            guardian__user=request.user,
            guardian__deleted_at__isnull=True,
            deleted_at__isnull=True,
            student__deleted_at__isnull=True,
        ).select_related('student__person')
        serializer = GuardianChildSerializer(links, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
```

```python
# apps/identity/urls.py
from .views import GuardianChildrenView
# ...
    path('me/children/', GuardianChildrenView.as_view(), name='guardian-children'),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.identity.tests.test_guardian_children_endpoint -v 2`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/identity/serializers.py apps/identity/views.py apps/identity/urls.py apps/identity/tests/test_guardian_children_endpoint.py
git commit -m "feat(identity): add GET /me/children/ guardian child-switcher endpoint"
```

---

### Task 4: Mobile types + `parentAuth.ts` OTP service

**Files:**
- Modify: `mobile/src/types/index.ts` (add `ChildSummary`, `OtpRequestResponse`, extend nothing else)
- Create: `mobile/src/services/parentAuth.ts`
- Test: `mobile/__tests__/parentAuth.test.ts`

**Interfaces:**
- Consumes: `apiClient` (`mobile/src/services/api.ts`, existing: `.post<T>(path, body)`), `saveTokens`/`saveUserProfile` (`mobile/src/services/storage.ts`, existing).
- Produces: `requestOtp(phone: string): Promise<{challenge_id: number}>`, `verifyOtp(challengeId: number, code: string): Promise<AuthResponse>` (reuses existing `AuthResponse` type from `mobile/src/types/index.ts`). Task 6 (LoginScreen) calls these two functions by exact name.

- [ ] **Step 1: Write the failing test**

```typescript
// mobile/__tests__/parentAuth.test.ts
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { requestOtp, verifyOtp } from '../src/services/parentAuth.ts';
import { apiClient } from '../src/services/api.ts';
import { clearAuth, getItem, StorageKeys } from '../src/services/storage.ts';

describe('Parent OTP Auth Service', () => {
  beforeEach(async () => {
    await clearAuth();
  });

  it('requests an OTP challenge for a phone number', async () => {
    const originalPost = apiClient.post;
    apiClient.post = (async (path: string, body: any) => {
      assert.strictEqual(path, '/auth/otp/request/');
      assert.strictEqual(body.phone_e164, '+6281234567890');
      return { data: { challenge_id: 42 }, status: 201, headers: {} };
    }) as any;

    try {
      const result = await requestOtp('+6281234567890');
      assert.strictEqual(result.challenge_id, 42);
    } finally {
      apiClient.post = originalPost;
    }
  });

  it('verifies an OTP and persists tokens + profile', async () => {
    const mockResponse = {
      data: {
        access: 'parent-access-token',
        refresh: 'parent-refresh-token',
        user: {
          id: 9,
          full_name: 'Ibu Siti',
          phone_e164: '+6281200000001',
          email: null,
          foundation_id: 3,
          roles: [{ id: 5, role: 'parent', scope_type: 'FOUNDATION', scope_id: 3 }],
        },
      },
      status: 200,
      headers: {},
    };

    const originalPost = apiClient.post;
    apiClient.post = (async (path: string, body: any) => {
      assert.strictEqual(path, '/auth/otp/verify/');
      assert.strictEqual(body.challenge_id, 42);
      assert.strictEqual(body.code, '111111');
      return mockResponse;
    }) as any;

    try {
      const result = await verifyOtp(42, '111111');
      assert.strictEqual(result.user.full_name, 'Ibu Siti');

      const storedAccess = await getItem(StorageKeys.ACCESS_TOKEN);
      assert.strictEqual(storedAccess, 'parent-access-token');
    } finally {
      apiClient.post = originalPost;
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && npm test -- parentAuth`
Expected: FAIL — cannot find module `../src/services/parentAuth.ts`.

- [ ] **Step 3: Write minimal implementation**

```typescript
// mobile/src/types/index.ts — append
export interface ChildSummary {
  student_id: number;
  full_name: string;
  photo_key: string;
  financial_responsible: boolean;
}

export interface OtpRequestResponse {
  challenge_id: number;
}
```

```typescript
// mobile/src/services/parentAuth.ts
/**
 * Guardian OTP login service (spec/08 PAR-001).
 */
import { apiClient } from './api.ts';
import { saveTokens, saveUserProfile } from './storage.ts';
import type { AuthResponse, OtpRequestResponse } from '../types/index.ts';

export async function requestOtp(phoneE164: string): Promise<OtpRequestResponse> {
  const response = await apiClient.post<OtpRequestResponse>('/auth/otp/request/', {
    phone_e164: phoneE164,
  });
  return response.data;
}

export async function verifyOtp(challengeId: number, code: string): Promise<AuthResponse> {
  const response = await apiClient.post<AuthResponse>('/auth/otp/verify/', {
    challenge_id: challengeId,
    code,
  });

  const { access, refresh, user } = response.data;
  await saveTokens(access, refresh);
  await saveUserProfile(user);

  return response.data;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && npm test -- parentAuth`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add mobile/src/types/index.ts mobile/src/services/parentAuth.ts mobile/__tests__/parentAuth.test.ts
git commit -m "feat(mobile): add parent OTP auth service"
```

---

### Task 5: Mobile data services — children, attendance, invoices, payments

**Files:**
- Create: `mobile/src/services/children.ts`
- Create: `mobile/src/services/parentAttendance.ts`
- Create: `mobile/src/services/invoices.ts`
- Create: `mobile/src/services/payments.ts`
- Test: `mobile/__tests__/parentDataServices.test.ts`

**Interfaces:**
- Consumes: `apiClient.get`/`apiClient.post` (existing).
- Produces:
  - `children.ts`: `fetchChildren(): Promise<ChildSummary[]>` (calls `GET /me/children/`).
  - `parentAttendance.ts`: `fetchAttendanceForChild(studentId: number): Promise<AttendanceDayItem[]>` (calls `GET /attendance/daily/?student_id=<id>`).
  - `invoices.ts`: `fetchInvoicesForChild(studentId: number): Promise<InvoiceItem[]>` (calls `GET /invoices/?student_id=<id>`).
  - `payments.ts`: `createPaymentIntent(invoiceIds: number[], method: 'VA' | 'QRIS', bank?: string): Promise<PaymentIntentItem>` (calls `POST /payment-intents/`), `fetchPaymentIntent(id: number): Promise<PaymentIntentItem>` (calls `GET /payment-intents/:id/`, used by Task 12's polling).
  - New types `AttendanceDayItem`, `InvoiceItem`, `PaymentIntentItem` in `mobile/src/types/index.ts`, consumed by Tasks 9–12.

- [ ] **Step 1: Write the failing test**

```typescript
// mobile/__tests__/parentDataServices.test.ts
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { fetchChildren } from '../src/services/children.ts';
import { fetchAttendanceForChild } from '../src/services/parentAttendance.ts';
import { fetchInvoicesForChild } from '../src/services/invoices.ts';
import { createPaymentIntent, fetchPaymentIntent } from '../src/services/payments.ts';
import { apiClient } from '../src/services/api.ts';

describe('Parent data services', () => {
  it('fetchChildren calls /me/children/', async () => {
    const original = apiClient.get;
    apiClient.get = (async (path: string) => {
      assert.strictEqual(path, '/me/children/');
      return { data: [{ student_id: 1, full_name: 'Dewi', photo_key: '', financial_responsible: true }], status: 200, headers: {} };
    }) as any;
    try {
      const result = await fetchChildren();
      assert.strictEqual(result.length, 1);
      assert.strictEqual(result[0].student_id, 1);
    } finally {
      apiClient.get = original;
    }
  });

  it('fetchAttendanceForChild filters by student_id', async () => {
    const original = apiClient.get;
    apiClient.get = (async (path: string) => {
      assert.strictEqual(path, '/attendance/daily/?student_id=7');
      return { data: { results: [] }, status: 200, headers: {} };
    }) as any;
    try {
      const result = await fetchAttendanceForChild(7);
      assert.deepStrictEqual(result, []);
    } finally {
      apiClient.get = original;
    }
  });

  it('fetchInvoicesForChild filters by student_id', async () => {
    const original = apiClient.get;
    apiClient.get = (async (path: string) => {
      assert.strictEqual(path, '/invoices/?student_id=7');
      return { data: { results: [] }, status: 200, headers: {} };
    }) as any;
    try {
      const result = await fetchInvoicesForChild(7);
      assert.deepStrictEqual(result, []);
    } finally {
      apiClient.get = original;
    }
  });

  it('createPaymentIntent posts invoice_ids and method', async () => {
    const original = apiClient.post;
    apiClient.post = (async (path: string, body: any) => {
      assert.strictEqual(path, '/payment-intents/');
      assert.deepStrictEqual(body.invoice_ids, [5]);
      assert.strictEqual(body.method, 'QRIS');
      return { data: { id: 99, status: 'PENDING' }, status: 201, headers: {} };
    }) as any;
    try {
      const result = await createPaymentIntent([5], 'QRIS');
      assert.strictEqual(result.id, 99);
    } finally {
      apiClient.post = original;
    }
  });

  it('fetchPaymentIntent gets by id', async () => {
    const original = apiClient.get;
    apiClient.get = (async (path: string) => {
      assert.strictEqual(path, '/payment-intents/99/');
      return { data: { id: 99, status: 'SETTLED' }, status: 200, headers: {} };
    }) as any;
    try {
      const result = await fetchPaymentIntent(99);
      assert.strictEqual(result.status, 'SETTLED');
    } finally {
      apiClient.get = original;
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && npm test -- parentDataServices`
Expected: FAIL — cannot find modules `children.ts`/`parentAttendance.ts`/`invoices.ts`/`payments.ts`.

- [ ] **Step 3: Write minimal implementation**

```typescript
// mobile/src/types/index.ts — append
export interface AttendanceDayItem {
  id: number;
  student: number;
  date: string;
  status: AttendanceStatus | 'TERLAMBAT' | 'DISPEN';
  first_in_at: string | null;
  first_out_at: string | null;
}

export interface InvoiceItem {
  id: number;
  number: string;
  period: string;
  due_date: string;
  total: string;
  paid: string;
  balance_due: string;
  currency: string;
  status: string;
  is_overdue: boolean;
}

export interface PaymentIntentItem {
  id: number;
  invoice: number;
  method: 'VA' | 'QRIS';
  va_bank?: string;
  va_number?: string;
  qris_payload?: string;
  amount: string;
  base_amount: string;
  convenience_fee_amount: string;
  currency: string;
  expires_at: string;
  status: string;
}
```

```typescript
// mobile/src/services/children.ts
import { apiClient } from './api.ts';
import type { ChildSummary } from '../types/index.ts';

export async function fetchChildren(): Promise<ChildSummary[]> {
  const response = await apiClient.get<ChildSummary[]>('/me/children/');
  return response.data;
}
```

```typescript
// mobile/src/services/parentAttendance.ts
import { apiClient } from './api.ts';
import type { AttendanceDayItem } from '../types/index.ts';

export async function fetchAttendanceForChild(studentId: number): Promise<AttendanceDayItem[]> {
  const response = await apiClient.get<{ results: AttendanceDayItem[] } | AttendanceDayItem[]>(
    `/attendance/daily/?student_id=${studentId}`
  );
  const data = response.data as any;
  return Array.isArray(data) ? data : data.results;
}
```

```typescript
// mobile/src/services/invoices.ts
import { apiClient } from './api.ts';
import type { InvoiceItem } from '../types/index.ts';

export async function fetchInvoicesForChild(studentId: number): Promise<InvoiceItem[]> {
  const response = await apiClient.get<{ results: InvoiceItem[] } | InvoiceItem[]>(
    `/invoices/?student_id=${studentId}`
  );
  const data = response.data as any;
  return Array.isArray(data) ? data : data.results;
}
```

```typescript
// mobile/src/services/payments.ts
import { apiClient } from './api.ts';
import type { PaymentIntentItem } from '../types/index.ts';

export async function createPaymentIntent(
  invoiceIds: number[],
  method: 'VA' | 'QRIS',
  bank?: string
): Promise<PaymentIntentItem> {
  const response = await apiClient.post<PaymentIntentItem>('/payment-intents/', {
    invoice_ids: invoiceIds,
    method,
    ...(bank ? { bank } : {}),
  });
  return response.data;
}

export async function fetchPaymentIntent(id: number): Promise<PaymentIntentItem> {
  const response = await apiClient.get<PaymentIntentItem>(`/payment-intents/${id}/`);
  return response.data;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && npm test -- parentDataServices`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add mobile/src/types/index.ts mobile/src/services/children.ts mobile/src/services/parentAttendance.ts mobile/src/services/invoices.ts mobile/src/services/payments.ts mobile/__tests__/parentDataServices.test.ts
git commit -m "feat(mobile): add parent-facing children/attendance/invoices/payments services"
```

---

### Task 6: Offline cache helper (`storage.ts` extension)

**Files:**
- Modify: `mobile/src/services/storage.ts` (add `cacheSet`/`cacheGet`)
- Test: `mobile/__tests__/cacheStorage.test.ts`

**Interfaces:**
- Consumes: existing `setItem`/`getItem` (`storage.ts`).
- Produces: `cacheSet<T>(key: string, value: T): Promise<void>` (stores `{value, cachedAt: ISOString}`), `cacheGet<T>(key: string): Promise<{value: T, cachedAt: string} | null>`. Tasks 9–11 (Home/Attendance/Invoices screens) consume these two functions by exact name for the `PAR-015` offline-cache behavior.

- [ ] **Step 1: Write the failing test**

```typescript
// mobile/__tests__/cacheStorage.test.ts
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { cacheGet, cacheSet } from '../src/services/storage.ts';

describe('Generic cache storage', () => {
  it('round-trips a cached value with a cachedAt timestamp', async () => {
    await cacheSet('test.cache.key', { hello: 'world' });
    const result = await cacheGet<{ hello: string }>('test.cache.key');
    assert.ok(result);
    assert.deepStrictEqual(result!.value, { hello: 'world' });
    assert.ok(typeof result!.cachedAt === 'string' && result!.cachedAt.length > 0);
  });

  it('returns null for a key that was never cached', async () => {
    const result = await cacheGet('test.cache.missing');
    assert.strictEqual(result, null);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && npm test -- cacheStorage`
Expected: FAIL — `cacheGet`/`cacheSet` not exported.

- [ ] **Step 3: Write minimal implementation**

```typescript
// mobile/src/services/storage.ts — append
export async function cacheSet<T>(key: string, value: T): Promise<void> {
  await setItem(key, JSON.stringify({ value, cachedAt: new Date().toISOString() }));
}

export async function cacheGet<T>(key: string): Promise<{ value: T; cachedAt: string } | null> {
  const raw = await getItem(key);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as { value: T; cachedAt: string };
  } catch {
    return null;
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && npm test -- cacheStorage`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add mobile/src/services/storage.ts mobile/__tests__/cacheStorage.test.ts
git commit -m "feat(mobile): add generic offline cache helper (PAR-015)"
```

---

### Task 7: `LoginScreen` role toggle + OTP flow

**Files:**
- Modify: `mobile/src/screens/LoginScreen.tsx`

**Interfaces:**
- Consumes: `requestOtp`, `verifyOtp` (Task 4); existing `login` (`auth.ts`, unchanged, still used by the `Guru` tab).
- Produces: `LoginScreen`'s existing prop contract `{ onLoginSuccess: (user: UserProfile) => void }` is unchanged — Task 8 (App.tsx) needs no new props from this component.

- [ ] **Step 1: Write the failing test**

Manual/UI component — no `node --test` harness exists for RN components in this repo (only service-layer tests, confirmed by `mobile/package.json`'s `testMatch`). Verification for this task is Step 4's Expo run, not an automated test. Skip step 1/2 for this task only; proceed to implementation.

- [ ] **Step 2: (skipped — no RN component test harness in this repo, see Step 1 note)**

- [ ] **Step 3: Modify `LoginScreen.tsx`**

Add a role toggle above the existing form; keep the `Guru` branch's JSX/handlers byte-for-byte identical, add a new `Wali Murid` branch:

```tsx
// mobile/src/screens/LoginScreen.tsx — replace the component body
import React, { useState } from 'react';
import {
  ActivityIndicator, KeyboardAvoidingView, Platform, SafeAreaView,
  StyleSheet, Text, TextInput, TouchableOpacity, View,
} from 'react-native';
import { login } from '../services/auth';
import { requestOtp, verifyOtp } from '../services/parentAuth';
import { colors, radius, spacing, typography } from '../theme/tokens';
import { UserProfile } from '../types';

interface LoginScreenProps {
  onLoginSuccess: (user: UserProfile) => void;
}

type Role = 'GURU' | 'WALI';
type WaliStep = 'PHONE' | 'CODE';

export const LoginScreen: React.FC<LoginScreenProps> = ({ onLoginSuccess }) => {
  const [role, setRole] = useState<Role>('GURU');

  // Guru (teacher) state — unchanged behavior from the original component.
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');

  // Wali (parent) state.
  const [waliStep, setWaliStep] = useState<WaliStep>('PHONE');
  const [phone, setPhone] = useState('');
  const [challengeId, setChallengeId] = useState<number | null>(null);
  const [code, setCode] = useState('');

  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleGuruSubmit = async () => {
    if (!identifier.trim() || !password) {
      setErrorMsg('Nomor HP / Email dan password wajib diisi.');
      return;
    }
    setLoading(true);
    setErrorMsg(null);
    try {
      const result = await login(identifier.trim(), password);
      onLoginSuccess(result.user);
    } catch (err: any) {
      setErrorMsg(
        err?.response?.data?.non_field_errors?.[0] ||
        err?.response?.data?.error ||
        err?.response?.data?.detail ||
        'Kredensial tidak valid atau akun terkunci. Periksa kembali data Anda.'
      );
    } finally {
      setLoading(false);
    }
  };

  const handleRequestOtp = async () => {
    if (!phone.trim()) {
      setErrorMsg('Nomor HP wajib diisi.');
      return;
    }
    setLoading(true);
    setErrorMsg(null);
    try {
      const result = await requestOtp(phone.trim());
      setChallengeId(result.challenge_id);
      setWaliStep('CODE');
    } catch (err: any) {
      setErrorMsg(err?.response?.data?.error || 'Gagal mengirim kode OTP.');
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOtp = async () => {
    if (!challengeId || code.length !== 6) {
      setErrorMsg('Masukkan 6 digit kode OTP.');
      return;
    }
    setLoading(true);
    setErrorMsg(null);
    try {
      const result = await verifyOtp(challengeId, code);
      onLoginSuccess(result.user);
    } catch (err: any) {
      setErrorMsg(err?.response?.data?.error || 'Kode OTP salah atau kedaluwarsa.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={styles.container}>
        <View style={styles.card}>
          <View style={styles.brandAccent} />
          <View style={styles.header}>
            <Text style={styles.brandTitle}>EDUCORE</Text>
            <Text style={styles.portalTitle}>
              {role === 'GURU' ? 'Portal Guru & Presensi' : 'Portal Wali Murid'}
            </Text>
          </View>

          <View style={styles.roleToggle}>
            <TouchableOpacity
              style={[styles.roleTab, role === 'GURU' && styles.roleTabActive]}
              onPress={() => { setRole('GURU'); setErrorMsg(null); }}
            >
              <Text style={[styles.roleTabText, role === 'GURU' && styles.roleTabTextActive]}>GURU</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.roleTab, role === 'WALI' && styles.roleTabActive]}
              onPress={() => { setRole('WALI'); setErrorMsg(null); }}
            >
              <Text style={[styles.roleTabText, role === 'WALI' && styles.roleTabTextActive]}>WALI MURID</Text>
            </TouchableOpacity>
          </View>

          {errorMsg && (
            <View style={styles.errorBox}>
              <Text style={styles.errorText}>{errorMsg}</Text>
            </View>
          )}

          {role === 'GURU' ? (
            <>
              <View style={styles.formGroup}>
                <Text style={styles.label}>NOMOR HP ATAU EMAIL</Text>
                <TextInput
                  style={styles.input}
                  placeholder="08123456789 atau guru@sekolah.sch.id"
                  placeholderTextColor={colors.subtle}
                  value={identifier}
                  onChangeText={setIdentifier}
                  autoCapitalize="none"
                  autoCorrect={false}
                  keyboardType="email-address"
                />
              </View>
              <View style={styles.formGroup}>
                <Text style={styles.label}>PASSWORD</Text>
                <TextInput
                  style={styles.input}
                  placeholder="••••••••••••"
                  placeholderTextColor={colors.subtle}
                  value={password}
                  onChangeText={setPassword}
                  secureTextEntry
                />
              </View>
              <TouchableOpacity style={styles.submitButton} onPress={handleGuruSubmit} disabled={loading} activeOpacity={0.85}>
                {loading ? <ActivityIndicator color={colors.white} /> : <Text style={styles.submitButtonText}>MASUK KE PORTAL</Text>}
              </TouchableOpacity>
            </>
          ) : waliStep === 'PHONE' ? (
            <>
              <View style={styles.formGroup}>
                <Text style={styles.label}>NOMOR HP</Text>
                <TextInput
                  style={styles.input}
                  placeholder="08123456789"
                  placeholderTextColor={colors.subtle}
                  value={phone}
                  onChangeText={setPhone}
                  keyboardType="phone-pad"
                />
              </View>
              <TouchableOpacity style={styles.submitButton} onPress={handleRequestOtp} disabled={loading} activeOpacity={0.85}>
                {loading ? <ActivityIndicator color={colors.white} /> : <Text style={styles.submitButtonText}>KIRIM KODE OTP</Text>}
              </TouchableOpacity>
            </>
          ) : (
            <>
              <View style={styles.formGroup}>
                <Text style={styles.label}>KODE OTP (6 DIGIT)</Text>
                <TextInput
                  style={styles.input}
                  placeholder="123456"
                  placeholderTextColor={colors.subtle}
                  value={code}
                  onChangeText={setCode}
                  keyboardType="number-pad"
                  maxLength={6}
                />
              </View>
              <TouchableOpacity style={styles.submitButton} onPress={handleVerifyOtp} disabled={loading} activeOpacity={0.85}>
                {loading ? <ActivityIndicator color={colors.white} /> : <Text style={styles.submitButtonText}>VERIFIKASI & MASUK</Text>}
              </TouchableOpacity>
              <TouchableOpacity onPress={() => { setWaliStep('PHONE'); setCode(''); }} style={styles.footer}>
                <Text style={styles.footerText}>Ubah nomor HP / kirim ulang kode</Text>
              </TouchableOpacity>
            </>
          )}

          {role === 'GURU' && (
            <View style={styles.footer}>
              <Text style={styles.footerText}>Bantuan akses atau reset akun? Hubungi Tata Usaha (TU) sekolah.</Text>
            </View>
          )}
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: colors.surface },
  container: { flex: 1, justifyContent: 'center', padding: spacing.base },
  card: {
    backgroundColor: colors.white, borderWidth: 1, borderColor: colors.border,
    padding: spacing.xl, borderRadius: radius.card,
    shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 0,
  },
  brandAccent: { height: 4, backgroundColor: colors.primary, marginBottom: spacing.lg },
  header: { marginBottom: spacing.lg },
  brandTitle: { fontSize: typography.fontSize.xs, fontWeight: typography.fontWeight.bold, color: colors.primary, letterSpacing: 2 },
  portalTitle: { fontSize: typography.fontSize.xxl, fontWeight: typography.fontWeight.bold, color: colors.heading, marginTop: 4 },
  roleToggle: { flexDirection: 'row', marginBottom: spacing.base, borderWidth: 1, borderColor: colors.borderDark },
  roleTab: { flex: 1, paddingVertical: spacing.sm, alignItems: 'center', backgroundColor: colors.white },
  roleTabActive: { backgroundColor: colors.primary },
  roleTabText: { fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold, color: colors.body },
  roleTabTextActive: { color: colors.white },
  errorBox: { backgroundColor: colors.alpaLight, borderWidth: 1, borderColor: colors.alpa, padding: spacing.sm, marginBottom: spacing.base, borderRadius: radius.card },
  errorText: { color: colors.alpa, fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.medium },
  formGroup: { marginBottom: spacing.base },
  label: { fontSize: typography.fontSize.xs, fontWeight: typography.fontWeight.bold, color: colors.body, marginBottom: 6, letterSpacing: 0.5 },
  input: {
    borderWidth: 1, borderColor: colors.borderDark, borderRadius: radius.input,
    paddingHorizontal: spacing.md, paddingVertical: spacing.sm + 2,
    fontSize: typography.fontSize.base, color: colors.heading, backgroundColor: colors.white,
  },
  submitButton: { backgroundColor: colors.primary, borderRadius: radius.button, paddingVertical: spacing.md, alignItems: 'center', justifyContent: 'center', marginTop: spacing.sm },
  submitButtonText: { color: colors.white, fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold, letterSpacing: 1 },
  footer: { marginTop: spacing.xl, alignItems: 'center' },
  footerText: { fontSize: typography.fontSize.xs, color: colors.muted, textAlign: 'center', lineHeight: 16 },
});
```

- [ ] **Step 4: Manually verify**

Run: `cd mobile && npx tsc --noEmit` (type-check passes) — then `npm run android` (or `ios`), tap `WALI MURID`, confirm phone → OTP → (mock backend, from Task 2) lands logged in.
Expected: no TypeScript errors; toggle switches forms; `Guru` path behaves exactly as before.

- [ ] **Step 5: Commit**

```bash
git add mobile/src/screens/LoginScreen.tsx
git commit -m "feat(mobile): add Wali Murid OTP login flow to LoginScreen"
```

---

### Task 8: `ParentShell` (child switcher + tab bar) and `App.tsx` role routing

**Files:**
- Create: `mobile/src/screens/parent/ParentShell.tsx`
- Modify: `mobile/App.tsx`
- Modify: `mobile/src/services/storage.ts` (add `saveLastChildId`/`getLastChildId`)
- Test: `mobile/__tests__/parentShellStorage.test.ts`

**Interfaces:**
- Consumes: `fetchChildren` (Task 5), `cacheGet`/`cacheSet` (Task 6) — not used directly by `ParentShell` itself but by its children (Tasks 9–11), which `ParentShell` renders via a `render prop`/children function.
- Produces: `ParentShell` component with props `{ children: (ctx: { selectedChild: ChildSummary; allChildren: ChildSummary[] }) => React.ReactNode; activeTab: 'HOME' | 'ATTENDANCE' | 'INVOICES'; onTabChange: (tab: 'HOME' | 'ATTENDANCE' | 'INVOICES') => void; onLogout: () => void }`. `saveLastChildId(id: number)`/`getLastChildId(): Promise<number | null>` in `storage.ts`, consumed by `ParentShell` itself (not exposed further).

- [ ] **Step 1: Write the failing test**

```typescript
// mobile/__tests__/parentShellStorage.test.ts
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { getLastChildId, saveLastChildId } from '../src/services/storage.ts';

describe('Last-selected child persistence (PAR-003)', () => {
  it('round-trips the last selected child id', async () => {
    await saveLastChildId(42);
    const result = await getLastChildId();
    assert.strictEqual(result, 42);
  });

  it('returns null when nothing was ever saved', async () => {
    const { removeItem, StorageKeys } = await import('../src/services/storage.ts');
    await removeItem(StorageKeys.LAST_CHILD_ID);
    const result = await getLastChildId();
    assert.strictEqual(result, null);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && npm test -- parentShellStorage`
Expected: FAIL — `getLastChildId`/`saveLastChildId`/`StorageKeys.LAST_CHILD_ID` not exported.

- [ ] **Step 3: Write minimal implementation**

```typescript
// mobile/src/services/storage.ts — extend StorageKeys and add functions
export const StorageKeys = {
  ACCESS_TOKEN: 'educore_access_token',
  REFRESH_TOKEN: 'educore_refresh_token',
  USER_PROFILE: 'educore_user_profile',
  PENDING_PUSH_TOKEN: 'educore_push_token',
  LAST_CHILD_ID: 'educore_parent_last_child_id',
};

export async function saveLastChildId(studentId: number): Promise<void> {
  await setItem(StorageKeys.LAST_CHILD_ID, String(studentId));
}

export async function getLastChildId(): Promise<number | null> {
  const raw = await getItem(StorageKeys.LAST_CHILD_ID);
  if (!raw) return null;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}
```

```tsx
// mobile/src/screens/parent/ParentShell.tsx
/**
 * Parent app shell: persistent child switcher header + bottom tab bar (spec/08 §2, PAR-003).
 */
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, SafeAreaView, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { fetchChildren } from '../../services/children';
import { getLastChildId, saveLastChildId } from '../../services/storage';
import { colors, radius, spacing, typography } from '../../theme/tokens';
import type { ChildSummary } from '../../types';

export type ParentTab = 'HOME' | 'ATTENDANCE' | 'INVOICES';

interface ParentShellProps {
  activeTab: ParentTab;
  onTabChange: (tab: ParentTab) => void;
  onLogout: () => void;
  children: (ctx: { selectedChild: ChildSummary; allChildren: ChildSummary[] }) => React.ReactNode;
}

export const ParentShell: React.FC<ParentShellProps> = ({ activeTab, onTabChange, onLogout, children }) => {
  const [loading, setLoading] = useState(true);
  const [allChildren, setAllChildren] = useState<ChildSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  useEffect(() => {
    (async () => {
      const kids = await fetchChildren();
      setAllChildren(kids);
      const lastId = await getLastChildId();
      const initial = kids.find((k) => k.student_id === lastId) ?? kids[0] ?? null;
      setSelectedId(initial ? initial.student_id : null);
      setLoading(false);
    })();
  }, []);

  const handleSelectChild = async (id: number) => {
    setSelectedId(id);
    await saveLastChildId(id);
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator size="large" color={colors.primary} />
      </SafeAreaView>
    );
  }

  const selectedChild = allChildren.find((k) => k.student_id === selectedId);
  if (!selectedChild) {
    return (
      <SafeAreaView style={styles.center}>
        <Text style={styles.emptyText}>Tidak ada data anak terhubung.</Text>
        <TouchableOpacity onPress={onLogout} style={styles.logoutLink}>
          <Text style={styles.logoutText}>Keluar</Text>
        </TouchableOpacity>
      </SafeAreaView>
    );
  }

  const showInvoicesTab = selectedChild.financial_responsible;

  return (
    <SafeAreaView style={styles.root}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.childSwitcher} contentContainerStyle={styles.childSwitcherContent}>
        {allChildren.map((child) => (
          <TouchableOpacity
            key={child.student_id}
            style={[styles.childChip, child.student_id === selectedId && styles.childChipActive]}
            onPress={() => handleSelectChild(child.student_id)}
          >
            <Text style={[styles.childChipText, child.student_id === selectedId && styles.childChipTextActive]}>
              {child.full_name}
            </Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      <View style={styles.content}>{children({ selectedChild, allChildren })}</View>

      <View style={styles.tabBar}>
        <TouchableOpacity style={styles.tabItem} onPress={() => onTabChange('HOME')}>
          <Text style={[styles.tabLabel, activeTab === 'HOME' && styles.tabLabelActive]}>Home</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.tabItem} onPress={() => onTabChange('ATTENDANCE')}>
          <Text style={[styles.tabLabel, activeTab === 'ATTENDANCE' && styles.tabLabelActive]}>Absensi</Text>
        </TouchableOpacity>
        {showInvoicesTab && (
          <TouchableOpacity style={styles.tabItem} onPress={() => onTabChange('INVOICES')}>
            <Text style={[styles.tabLabel, activeTab === 'INVOICES' && styles.tabLabelActive]}>Tagihan</Text>
          </TouchableOpacity>
        )}
      </View>
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.surface, padding: spacing.xl },
  emptyText: { fontSize: typography.fontSize.base, color: colors.muted, textAlign: 'center', marginBottom: spacing.lg },
  logoutLink: { padding: spacing.sm },
  logoutText: { color: colors.primary, fontWeight: typography.fontWeight.bold },
  childSwitcher: { backgroundColor: colors.white, borderBottomWidth: 1, borderBottomColor: colors.border },
  childSwitcherContent: { paddingHorizontal: spacing.base, paddingVertical: spacing.sm },
  childChip: { paddingHorizontal: spacing.md, paddingVertical: spacing.xs, borderWidth: 1, borderColor: colors.borderDark, borderRadius: radius.badge, marginRight: spacing.sm },
  childChipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  childChipText: { fontSize: typography.fontSize.sm, color: colors.body, fontWeight: typography.fontWeight.medium },
  childChipTextActive: { color: colors.white },
  content: { flex: 1 },
  tabBar: { flexDirection: 'row', borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.white },
  tabItem: { flex: 1, paddingVertical: spacing.md, alignItems: 'center' },
  tabLabel: { fontSize: typography.fontSize.sm, color: colors.muted, fontWeight: typography.fontWeight.medium },
  tabLabelActive: { color: colors.primary, fontWeight: typography.fontWeight.bold },
});
```

```tsx
// mobile/App.tsx — replace the currentUser-branching section only
import { ParentShell, ParentTab } from './src/screens/parent/ParentShell';
import { ParentHomeScreen } from './src/screens/parent/ParentHomeScreen';       // added in Task 9
import { ParentAttendanceScreen } from './src/screens/parent/ParentAttendanceScreen'; // added in Task 10
import { ParentInvoicesScreen } from './src/screens/parent/ParentInvoicesScreen';     // added in Task 11

// inside the App component, alongside existing state:
const [parentTab, setParentTab] = useState<ParentTab>('HOME');
const isParent = (user: UserProfile | null) => !!user?.roles?.some((r) => r.role === 'parent');

// replace the final return's ternary with a three-way branch:
return (
  <View style={styles.root}>
    <StatusBar style="dark" />
    {!currentUser ? (
      <LoginScreen onLoginSuccess={handleLoginSuccess} />
    ) : isParent(currentUser) ? (
      <ParentShell activeTab={parentTab} onTabChange={setParentTab} onLogout={handleLogout}>
        {({ selectedChild }) =>
          parentTab === 'HOME' ? (
            <ParentHomeScreen child={selectedChild} />
          ) : parentTab === 'ATTENDANCE' ? (
            <ParentAttendanceScreen child={selectedChild} />
          ) : (
            <ParentInvoicesScreen child={selectedChild} />
          )
        }
      </ParentShell>
    ) : activeSlot ? (
      <RollCallScreen
        slot={activeSlot}
        dateStr={todayStr}
        initialRoster={getMockRosterForSlot(activeSlot)}
        onBack={() => setActiveSlot(null)}
        onSaved={() => setActiveSlot(null)}
      />
    ) : (
      <AgendaScreen
        user={currentUser}
        onSelectSlot={(slot) => setActiveSlot(slot)}
        onOpenSubstitution={(slot) => setSubModalSlot(slot)}
        onLogout={handleLogout}
      />
    )}
    <SubstitutionModal
      visible={!!subModalSlot}
      slot={subModalSlot}
      onClose={() => setSubModalSlot(null)}
      onResolved={() => setSubModalSlot(null)}
    />
  </View>
);
```

- [ ] **Step 4: Run test to verify it passes, then type-check**

Run: `cd mobile && npm test -- parentShellStorage`
Expected: PASS (2 tests).

Run: `cd mobile && npx tsc --noEmit`
Expected: fails only on missing `ParentHomeScreen`/`ParentAttendanceScreen`/`ParentInvoicesScreen` modules (created in Tasks 9–11) — no other errors. This is expected at this point in the plan; re-run after Task 11.

- [ ] **Step 5: Commit**

```bash
git add mobile/src/services/storage.ts mobile/src/screens/parent/ParentShell.tsx mobile/App.tsx mobile/__tests__/parentShellStorage.test.ts
git commit -m "feat(mobile): add ParentShell child switcher and role-based App.tsx routing"
```

---

### Task 9: `ParentHomeScreen`

**Files:**
- Create: `mobile/src/screens/parent/ParentHomeScreen.tsx`

**Interfaces:**
- Consumes: `fetchAttendanceForChild` (Task 5), `fetchInvoicesForChild` (Task 5), `cacheGet`/`cacheSet` (Task 6), `StaleOfflineBanner` (existing component, unchanged), `ChildSummary` (Task 5's types).
- Produces: `ParentHomeScreen` component with props `{ child: ChildSummary }`. Task 8 renders it directly; no other task depends on its internals.

- [ ] **Step 1–2: (no RN component test harness — see Task 7's note; verified manually in Step 4)**

- [ ] **Step 3: Write the screen**

```tsx
// mobile/src/screens/parent/ParentHomeScreen.tsx
/**
 * Parent Home: per-child status card + outstanding balance summary (spec/08 §2, §4, PAR-002).
 */
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from 'react-native';
import { fetchAttendanceForChild } from '../../services/parentAttendance';
import { fetchInvoicesForChild } from '../../services/invoices';
import { cacheGet, cacheSet } from '../../services/storage';
import { StaleOfflineBanner } from '../../components/StaleOfflineBanner';
import { colors, radius, spacing, typography } from '../../theme/tokens';
import type { AttendanceDayItem, ChildSummary, InvoiceItem } from '../../types';

interface ParentHomeScreenProps {
  child: ChildSummary;
}

const STATUS_LABEL: Record<string, string> = {
  HADIR: 'Sudah di sekolah',
  TERLAMBAT: 'Sudah di sekolah (Terlambat)',
  SAKIT: 'Sakit',
  IZIN: 'Izin',
  ALPA: 'Tidak hadir',
  DISPEN: 'Dispensasi',
};

export const ParentHomeScreen: React.FC<ParentHomeScreenProps> = ({ child }) => {
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [cachedAt, setCachedAt] = useState<string | null>(null);
  const [today, setToday] = useState<AttendanceDayItem | null>(null);
  const [outstandingInvoices, setOutstandingInvoices] = useState<InvoiceItem[]>([]);

  const cacheKey = `educore_parent_home_${child.student_id}`;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const [attendance, invoices] = await Promise.all([
          fetchAttendanceForChild(child.student_id),
          child.financial_responsible ? fetchInvoicesForChild(child.student_id) : Promise.resolve([]),
        ]);
        if (cancelled) return;
        const todayStr = new Date().toISOString().split('T')[0];
        const todayRow = attendance.find((a) => a.date === todayStr) ?? null;
        const outstanding = invoices.filter((inv) => Number(inv.balance_due) > 0);
        setToday(todayRow);
        setOutstandingInvoices(outstanding);
        setOffline(false);
        setCachedAt(null);
        await cacheSet(cacheKey, { today: todayRow, outstanding });
      } catch {
        if (cancelled) return;
        const cached = await cacheGet<{ today: AttendanceDayItem | null; outstanding: InvoiceItem[] }>(cacheKey);
        if (cached) {
          setToday(cached.value.today);
          setOutstandingInvoices(cached.value.outstanding);
          setCachedAt(cached.cachedAt);
        }
        setOffline(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [child.student_id, child.financial_responsible]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }

  const statusLabel = today ? STATUS_LABEL[today.status] ?? today.status : 'Belum tiba';

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <StaleOfflineBanner isOffline={offline} lastSyncedAt={cachedAt} />

      <View style={styles.statusCard}>
        <Text style={styles.statusLabel}>{statusLabel}</Text>
        {today?.first_in_at && <Text style={styles.statusTime}>Tiba: {today.first_in_at}</Text>}
      </View>

      {child.financial_responsible && (
        <View style={styles.invoiceCard}>
          <Text style={styles.cardTitle}>Tagihan</Text>
          {outstandingInvoices.length === 0 ? (
            <Text style={styles.cardBody}>Tidak ada tagihan tertunggak.</Text>
          ) : (
            <Text style={styles.cardBody}>
              {outstandingInvoices.length} tagihan belum lunas — jatuh tempo terdekat {outstandingInvoices[0].due_date}.
            </Text>
          )}
        </View>
      )}
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  content: { padding: spacing.base },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  statusCard: {
    backgroundColor: colors.white, borderWidth: 1, borderColor: colors.border,
    borderRadius: radius.card, padding: spacing.lg, marginBottom: spacing.base,
  },
  statusLabel: { fontSize: typography.fontSize.xl, fontWeight: typography.fontWeight.bold, color: colors.heading },
  statusTime: { fontSize: typography.fontSize.sm, color: colors.muted, marginTop: spacing.xs },
  invoiceCard: {
    backgroundColor: colors.white, borderWidth: 1, borderColor: colors.border,
    borderRadius: radius.card, padding: spacing.lg,
  },
  cardTitle: { fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.bold, color: colors.heading, marginBottom: spacing.xs },
  cardBody: { fontSize: typography.fontSize.sm, color: colors.body },
});
```

- [ ] **Step 4: Manually verify**

Run: `cd mobile && npx tsc --noEmit` — no new errors attributable to this file.
Manual: run app, log in as a seeded guardian, confirm Home renders status + invoice summary; toggle airplane mode, reload, confirm cached data + `StaleOfflineBanner` shows.

- [ ] **Step 5: Commit**

```bash
git add mobile/src/screens/parent/ParentHomeScreen.tsx
git commit -m "feat(mobile): add ParentHomeScreen with offline cache fallback"
```

---

### Task 10: `ParentAttendanceScreen`

**Files:**
- Create: `mobile/src/screens/parent/ParentAttendanceScreen.tsx`

**Interfaces:**
- Consumes: `fetchAttendanceForChild` (Task 5), `cacheGet`/`cacheSet` (Task 6), `StaleOfflineBanner`.
- Produces: `ParentAttendanceScreen` component with props `{ child: ChildSummary }`. Consumed only by Task 8.

- [ ] **Step 1–2: (no RN component test harness — verified manually in Step 4)**

- [ ] **Step 3: Write the screen**

```tsx
// mobile/src/screens/parent/ParentAttendanceScreen.tsx
/**
 * Parent Attendance: per-child attendance timeline (spec/08 §2 "Attendance").
 */
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, FlatList, StyleSheet, Text, View } from 'react-native';
import { fetchAttendanceForChild } from '../../services/parentAttendance';
import { cacheGet, cacheSet } from '../../services/storage';
import { StaleOfflineBanner } from '../../components/StaleOfflineBanner';
import { colors, radius, spacing, typography } from '../../theme/tokens';
import type { AttendanceDayItem, ChildSummary } from '../../types';

interface ParentAttendanceScreenProps {
  child: ChildSummary;
}

const STATUS_COLOR: Record<string, string> = {
  HADIR: colors.hadir,
  TERLAMBAT: colors.izin,
  SAKIT: colors.sakit,
  IZIN: colors.izin,
  ALPA: colors.alpa,
  DISPEN: colors.izin,
};

export const ParentAttendanceScreen: React.FC<ParentAttendanceScreenProps> = ({ child }) => {
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [cachedAt, setCachedAt] = useState<string | null>(null);
  const [days, setDays] = useState<AttendanceDayItem[]>([]);

  const cacheKey = `educore_parent_attendance_${child.student_id}`;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const result = await fetchAttendanceForChild(child.student_id);
        if (cancelled) return;
        const sorted = [...result].sort((a, b) => b.date.localeCompare(a.date));
        setDays(sorted);
        setOffline(false);
        setCachedAt(null);
        await cacheSet(cacheKey, sorted);
      } catch {
        if (cancelled) return;
        const cached = await cacheGet<AttendanceDayItem[]>(cacheKey);
        if (cached) {
          setDays(cached.value);
          setCachedAt(cached.cachedAt);
        }
        setOffline(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [child.student_id]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <StaleOfflineBanner isOffline={offline} lastSyncedAt={cachedAt} />
      <FlatList
        data={days}
        keyExtractor={(item) => String(item.id)}
        contentContainerStyle={styles.list}
        ListEmptyComponent={<Text style={styles.emptyText}>Belum ada data presensi.</Text>}
        renderItem={({ item }) => (
          <View style={styles.row}>
            <View style={[styles.dot, { backgroundColor: STATUS_COLOR[item.status] ?? colors.muted }]} />
            <View style={styles.rowText}>
              <Text style={styles.rowDate}>{item.date}</Text>
              <Text style={styles.rowStatus}>
                {item.status}{item.first_in_at ? ` — Tiba ${item.first_in_at}` : ''}
              </Text>
            </View>
          </View>
        )}
      />
    </View>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  list: { padding: spacing.base },
  emptyText: { fontSize: typography.fontSize.sm, color: colors.muted, textAlign: 'center', marginTop: spacing.xl },
  row: {
    flexDirection: 'row', alignItems: 'center', backgroundColor: colors.white,
    borderWidth: 1, borderColor: colors.border, borderRadius: radius.card,
    padding: spacing.md, marginBottom: spacing.sm,
  },
  dot: { width: 10, height: 10, marginRight: spacing.md },
  rowText: { flex: 1 },
  rowDate: { fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold, color: colors.heading },
  rowStatus: { fontSize: typography.fontSize.xs, color: colors.muted, marginTop: 2 },
});
```

- [ ] **Step 4: Manually verify**

Run: `cd mobile && npx tsc --noEmit` — no new errors from this file.
Manual: run app, open Absensi tab, confirm list renders sorted newest-first with colored status dots.

- [ ] **Step 5: Commit**

```bash
git add mobile/src/screens/parent/ParentAttendanceScreen.tsx
git commit -m "feat(mobile): add ParentAttendanceScreen"
```

---

### Task 11: `ParentInvoicesScreen`

**Files:**
- Create: `mobile/src/screens/parent/ParentInvoicesScreen.tsx`

**Interfaces:**
- Consumes: `fetchInvoicesForChild` (Task 5), `cacheGet`/`cacheSet` (Task 6), `StaleOfflineBanner`.
- Produces: `ParentInvoicesScreen` component with props `{ child: ChildSummary }`; internal navigation to `PaymentScreen` (Task 12) via local state (`selectedInvoiceIds: number[] | null`), consumed only within this file and Task 12's props contract.

- [ ] **Step 1–2: (no RN component test harness — verified manually in Step 4)**

- [ ] **Step 3: Write the screen**

```tsx
// mobile/src/screens/parent/ParentInvoicesScreen.tsx
/**
 * Parent Invoices: outstanding + paid invoice list, quick-pay CTA (spec/08 PAR-005, PAR-006).
 */
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, FlatList, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { fetchInvoicesForChild } from '../../services/invoices';
import { cacheGet, cacheSet } from '../../services/storage';
import { StaleOfflineBanner } from '../../components/StaleOfflineBanner';
import { PaymentScreen } from './PaymentScreen'; // added in Task 12
import { colors, radius, spacing, typography } from '../../theme/tokens';
import type { ChildSummary, InvoiceItem } from '../../types';

interface ParentInvoicesScreenProps {
  child: ChildSummary;
}

export const ParentInvoicesScreen: React.FC<ParentInvoicesScreenProps> = ({ child }) => {
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [cachedAt, setCachedAt] = useState<string | null>(null);
  const [invoices, setInvoices] = useState<InvoiceItem[]>([]);
  const [payingInvoiceIds, setPayingInvoiceIds] = useState<number[] | null>(null);

  const cacheKey = `educore_parent_invoices_${child.student_id}`;

  const load = async () => {
    setLoading(true);
    try {
      const result = await fetchInvoicesForChild(child.student_id);
      setInvoices(result);
      setOffline(false);
      setCachedAt(null);
      await cacheSet(cacheKey, result);
    } catch {
      const cached = await cacheGet<InvoiceItem[]>(cacheKey);
      if (cached) {
        setInvoices(cached.value);
        setCachedAt(cached.cachedAt);
      }
      setOffline(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [child.student_id]);

  if (payingInvoiceIds) {
    return (
      <PaymentScreen
        invoiceIds={payingInvoiceIds}
        onDone={() => { setPayingInvoiceIds(null); load(); }}
      />
    );
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }

  const outstanding = invoices
    .filter((inv) => Number(inv.balance_due) > 0)
    .sort((a, b) => a.due_date.localeCompare(b.due_date));

  const handleQuickPay = () => {
    if (outstanding.length === 1) {
      setPayingInvoiceIds([outstanding[0].id]);
    } else if (outstanding.length > 1) {
      setPayingInvoiceIds([outstanding[0].id]); // oldest pre-selected, PAR-005
    }
  };

  return (
    <View style={styles.root}>
      <StaleOfflineBanner isOffline={offline} lastSyncedAt={cachedAt} />

      {outstanding.length > 0 && !offline && (
        <TouchableOpacity style={styles.payButton} onPress={handleQuickPay} activeOpacity={0.85}>
          <Text style={styles.payButtonText}>
            BAYAR {outstanding.length === 1 ? 'TAGIHAN INI' : `TAGIHAN TERLAMA (${outstanding[0].period})`}
          </Text>
        </TouchableOpacity>
      )}

      <FlatList
        data={invoices}
        keyExtractor={(item) => String(item.id)}
        contentContainerStyle={styles.list}
        ListEmptyComponent={<Text style={styles.emptyText}>Belum ada tagihan.</Text>}
        renderItem={({ item }) => (
          <View style={styles.row}>
            <View style={styles.rowText}>
              <Text style={styles.rowNumber}>{item.number} — {item.period}</Text>
              <Text style={styles.rowDue}>Jatuh tempo: {item.due_date}</Text>
            </View>
            <View style={styles.rowAmounts}>
              <Text style={styles.rowTotal}>{item.currency} {item.total}</Text>
              <Text style={[styles.rowStatus, item.status === 'PAID' && styles.rowStatusPaid]}>{item.status}</Text>
            </View>
          </View>
        )}
      />
    </View>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  payButton: { backgroundColor: colors.primary, margin: spacing.base, paddingVertical: spacing.md, alignItems: 'center', borderRadius: radius.button },
  payButtonText: { color: colors.white, fontWeight: typography.fontWeight.bold, letterSpacing: 1 },
  list: { paddingHorizontal: spacing.base, paddingBottom: spacing.base },
  emptyText: { fontSize: typography.fontSize.sm, color: colors.muted, textAlign: 'center', marginTop: spacing.xl },
  row: {
    flexDirection: 'row', justifyContent: 'space-between', backgroundColor: colors.white,
    borderWidth: 1, borderColor: colors.border, borderRadius: radius.card,
    padding: spacing.md, marginBottom: spacing.sm,
  },
  rowText: { flex: 1 },
  rowNumber: { fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold, color: colors.heading },
  rowDue: { fontSize: typography.fontSize.xs, color: colors.muted, marginTop: 2 },
  rowAmounts: { alignItems: 'flex-end' },
  rowTotal: { fontSize: typography.fontSize.sm, fontWeight: typography.fontWeight.bold, color: colors.heading },
  rowStatus: { fontSize: typography.fontSize.xs, color: colors.alpa, marginTop: 2 },
  rowStatusPaid: { color: colors.hadir },
});
```

- [ ] **Step 4: Manually verify**

Run: `cd mobile && npx tsc --noEmit` — will still fail on missing `./PaymentScreen` until Task 12 lands (expected at this point).

- [ ] **Step 5: Commit**

```bash
git add mobile/src/screens/parent/ParentInvoicesScreen.tsx
git commit -m "feat(mobile): add ParentInvoicesScreen with quick-pay CTA (PAR-005)"
```

---

### Task 12: `PaymentScreen` — VA/QRIS instructions, countdown, settlement polling

**Files:**
- Create: `mobile/src/screens/parent/PaymentScreen.tsx`

**Interfaces:**
- Consumes: `createPaymentIntent`, `fetchPaymentIntent` (Task 5).
- Produces: `PaymentScreen` component with props `{ invoiceIds: number[]; onDone: () => void }`. Consumed only by Task 11.

- [ ] **Step 1–2: (no RN component test harness — verified manually in Step 4)**

- [ ] **Step 3: Write the screen**

```tsx
// mobile/src/screens/parent/PaymentScreen.tsx
/**
 * VA/QRIS payment screen: fee breakdown, instructions, expiry countdown,
 * and settlement polling in place of a not-yet-built payment-settled push
 * (spec/08 PAR-006, PAR-007, PAR-008 acceptance criterion #2).
 */
import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { createPaymentIntent, fetchPaymentIntent } from '../../services/payments';
import { colors, radius, spacing, typography } from '../../theme/tokens';
import type { PaymentIntentItem } from '../../types';

interface PaymentScreenProps {
  invoiceIds: number[];
  onDone: () => void;
}

const POLL_INTERVAL_MS = 3000;

export const PaymentScreen: React.FC<PaymentScreenProps> = ({ invoiceIds, onDone }) => {
  const [method, setMethod] = useState<'VA' | 'QRIS' | null>(null);
  const [intent, setIntent] = useState<PaymentIntentItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  useEffect(() => {
    if (!intent?.expires_at) return;
    const tick = () => {
      const remaining = Math.max(0, Math.floor((new Date(intent.expires_at).getTime() - Date.now()) / 1000));
      setSecondsLeft(remaining);
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [intent?.expires_at]);

  const handleChooseMethod = async (chosen: 'VA' | 'QRIS') => {
    setMethod(chosen);
    setLoading(true);
    setErrorMsg(null);
    try {
      const created = await createPaymentIntent(invoiceIds, chosen);
      setIntent(created);
      pollRef.current = setInterval(async () => {
        try {
          const refreshed = await fetchPaymentIntent(created.id);
          setIntent(refreshed);
          if (refreshed.status === 'SETTLED' || refreshed.status === 'PAID') {
            if (pollRef.current) clearInterval(pollRef.current);
            onDone();
          }
        } catch {
          // Network hiccup during polling — keep trying on the next tick.
        }
      }, POLL_INTERVAL_MS);
    } catch (err: any) {
      setErrorMsg(err?.response?.data?.error || 'Gagal membuat intent pembayaran.');
    } finally {
      setLoading(false);
    }
  };

  if (!method) {
    return (
      <View style={styles.center}>
        <Text style={styles.title}>Pilih Metode Pembayaran</Text>
        <TouchableOpacity style={styles.methodButton} onPress={() => handleChooseMethod('VA')}>
          <Text style={styles.methodButtonText}>Virtual Account (VA)</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.methodButton} onPress={() => handleChooseMethod('QRIS')}>
          <Text style={styles.methodButtonText}>QRIS</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={onDone} style={styles.cancelLink}>
          <Text style={styles.cancelText}>Batal</Text>
        </TouchableOpacity>
      </View>
    );
  }

  if (loading || !intent) {
    return (
      <View style={styles.center}>
        {errorMsg ? (
          <>
            <Text style={styles.errorText}>{errorMsg}</Text>
            <TouchableOpacity onPress={() => setMethod(null)} style={styles.cancelLink}>
              <Text style={styles.cancelText}>Coba lagi</Text>
            </TouchableOpacity>
          </>
        ) : (
          <ActivityIndicator size="large" color={colors.primary} />
        )}
      </View>
    );
  }

  const minutes = Math.floor(secondsLeft / 60);
  const seconds = secondsLeft % 60;

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <View style={styles.summaryCard}>
        <View style={styles.summaryRow}>
          <Text style={styles.summaryLabel}>Jumlah Tagihan</Text>
          <Text style={styles.summaryValue}>{intent.currency} {intent.base_amount}</Text>
        </View>
        <View style={styles.summaryRow}>
          <Text style={styles.summaryLabel}>Biaya Layanan</Text>
          <Text style={styles.summaryValue}>{intent.currency} {intent.convenience_fee_amount}</Text>
        </View>
        <View style={[styles.summaryRow, styles.summaryTotalRow]}>
          <Text style={styles.summaryTotalLabel}>Total Bayar</Text>
          <Text style={styles.summaryTotalValue}>{intent.currency} {intent.amount}</Text>
        </View>
      </View>

      <View style={styles.instructionCard}>
        {intent.method === 'VA' ? (
          <>
            <Text style={styles.cardTitle}>Transfer Virtual Account — {intent.va_bank}</Text>
            <Text style={styles.vaNumber}>{intent.va_number}</Text>
            <Text style={styles.instructionBody}>
              1. Buka aplikasi mobile banking {intent.va_bank}.{'\n'}
              2. Pilih menu Transfer ke Virtual Account.{'\n'}
              3. Masukkan nomor VA di atas, lalu konfirmasi jumlah yang tertera.
            </Text>
          </>
        ) : (
          <>
            <Text style={styles.cardTitle}>Bayar dengan QRIS</Text>
            <Text style={styles.instructionBody}>Pindai kode QRIS pada aplikasi e-wallet atau mobile banking Anda.</Text>
          </>
        )}
        <Text style={styles.countdown}>
          Kedaluwarsa dalam {minutes}:{String(seconds).padStart(2, '0')}
        </Text>
      </View>

      <Text style={styles.waitingNote}>Menunggu konfirmasi pembayaran otomatis…</Text>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  content: { padding: spacing.base },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: spacing.xl },
  title: { fontSize: typography.fontSize.lg, fontWeight: typography.fontWeight.bold, color: colors.heading, marginBottom: spacing.lg },
  methodButton: {
    width: '100%', backgroundColor: colors.primary, borderRadius: radius.button,
    paddingVertical: spacing.md, alignItems: 'center', marginBottom: spacing.sm,
  },
  methodButtonText: { color: colors.white, fontWeight: typography.fontWeight.bold },
  cancelLink: { marginTop: spacing.base },
  cancelText: { color: colors.muted, fontSize: typography.fontSize.sm },
  errorText: { color: colors.alpa, fontSize: typography.fontSize.sm, textAlign: 'center' },
  summaryCard: { backgroundColor: colors.white, borderWidth: 1, borderColor: colors.border, borderRadius: radius.card, padding: spacing.lg, marginBottom: spacing.base },
  summaryRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.xs },
  summaryLabel: { fontSize: typography.fontSize.sm, color: colors.body },
  summaryValue: { fontSize: typography.fontSize.sm, color: colors.heading },
  summaryTotalRow: { borderTopWidth: 1, borderTopColor: colors.border, paddingTop: spacing.sm, marginTop: spacing.xs },
  summaryTotalLabel: { fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.bold, color: colors.heading },
  summaryTotalValue: { fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.bold, color: colors.primary },
  instructionCard: { backgroundColor: colors.white, borderWidth: 1, borderColor: colors.border, borderRadius: radius.card, padding: spacing.lg, marginBottom: spacing.base },
  cardTitle: { fontSize: typography.fontSize.base, fontWeight: typography.fontWeight.bold, color: colors.heading, marginBottom: spacing.sm },
  vaNumber: { fontSize: typography.fontSize.xl, fontWeight: typography.fontWeight.bold, color: colors.primary, letterSpacing: 1, marginBottom: spacing.sm },
  instructionBody: { fontSize: typography.fontSize.sm, color: colors.body, lineHeight: 20 },
  countdown: { fontSize: typography.fontSize.sm, color: colors.offline, fontWeight: typography.fontWeight.bold, marginTop: spacing.base },
  waitingNote: { fontSize: typography.fontSize.xs, color: colors.muted, textAlign: 'center' },
});
```

- [ ] **Step 4: Full verification pass**

Run: `cd mobile && npx tsc --noEmit`
Expected: PASS with zero errors — this closes out the `./PaymentScreen` import gap left open in Tasks 8 and 11.

Run: `cd mobile && npm test`
Expected: PASS, all mobile suites (existing teacher-app tests + `parentAuth.test.ts`, `parentDataServices.test.ts`, `cacheStorage.test.ts`, `parentShellStorage.test.ts`).

Run: `python manage.py test apps.identity -v 2`
Expected: PASS, all identity suites including the three new files from Tasks 1–3.

Manual: run app end-to-end — Wali Murid login → Home → Absensi → Tagihan → pick invoice → choose VA → see instructions + countdown → (in a dev/staging environment, manually settle the `PaymentIntent` via Django admin or the existing webhook test path) → confirm the screen auto-advances via polling within ~30s.

- [ ] **Step 5: Commit**

```bash
git add mobile/src/screens/parent/PaymentScreen.tsx
git commit -m "feat(mobile): add PaymentScreen with VA/QRIS instructions and settlement polling (PAR-006, PAR-007)"
```

---

## Post-implementation (not a plan task — do after Task 12 is reviewed and merged)

Per AGENTS.md §4's Open Items protocol, file these as Notion `[Open Item]` pages in the "Astra Educore" database once this slice ships (each references `spec/08-parent-app.md`):
- Parent Wallet tab (balance, top-up, spend controls).
- Parent Academic tab (grades, homework, report cards, timetable).
- Parent Messages tab (announcements, teacher messages, permission slips).
- Parent Profile tab (notification prefs, `en-US` switch, biometric unlock).
- Absence request with photo attachment (PAR-011).
- Permission slip digital acknowledgement (PAR-012).
- Receipts as downloadable/shareable PDF (PAR-009).
- Real payment-settlement push notification (would let Task 12's polling be replaced/backed up by a push, per PAR-008's literal wording) — new `apps.notifications` category + webhook wiring.
- Push-driven deep link from a notification into attendance detail (PAR-004).
- Analytics event instrumentation (spec/08 §5).

Also update `memory/01_PROJECT.md`'s task table and the Notion task page (`3dc347a6-6594-810d-a66b-c77fbbaad20d`) status to `Done` with the PR link, per AGENTS.md §4 step 5 — only after the PR is actually merged, not before.
