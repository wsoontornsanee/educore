"""Tests for DRF Idempotency support (ARC-010, ARC-014)."""
from django.test import RequestFactory, TestCase
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.core.idempotency import IdempotentViewMixin, compute_request_hash
from apps.core.models import IdempotencyRecord


class DummyActionView(IdempotentViewMixin, APIView):
    call_count = 0

    def post(self, request, *args, **kwargs):
        DummyActionView.call_count += 1
        name = request.data.get('name', 'Anonymous')
        if name == 'TRIGGER_ERROR':
            return Response({"error": "BAD_DATA"}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"status": "SUCCESS", "name": name, "call_count": DummyActionView.call_count}, status=status.HTTP_201_CREATED)


class IdempotencyTests(TestCase):
    def setUp(self):
        DummyActionView.call_count = 0
        self.factory = RequestFactory()
        IdempotencyRecord.objects.all().delete()

    def test_compute_request_hash_deterministic(self):
        hash1 = compute_request_hash('POST', '/api/v1/test/', b'{"amount": 100}')
        hash2 = compute_request_hash('POST', '/api/v1/test/', b'{"amount": 100}')
        hash3 = compute_request_hash('POST', '/api/v1/test/', b'{"amount": 200}')

        self.assertEqual(hash1, hash2)
        self.assertNotEqual(hash1, hash3)

    def test_request_without_idempotency_key_executes_each_time(self):
        view = DummyActionView.as_view()

        req1 = self.factory.post('/api/v1/test/', {'name': 'Budi'}, format='json')
        resp1 = view(req1)
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp1.data['call_count'], 1)

        req2 = self.factory.post('/api/v1/test/', {'name': 'Budi'}, format='json')
        resp2 = view(req2)
        self.assertEqual(resp2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp2.data['call_count'], 2)
        self.assertEqual(IdempotencyRecord.objects.count(), 0)

    def test_same_key_and_same_payload_replays_cached_response(self):
        view = DummyActionView.as_view()
        key = "idemp-key-001"

        # First execution
        req1 = self.factory.post(
            '/api/v1/test/',
            {'name': 'Ahmad'},
            format='json',
            HTTP_IDEMPOTENCY_KEY=key,
        )
        resp1 = view(req1)
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp1.data['name'], 'Ahmad')
        self.assertEqual(DummyActionView.call_count, 1)
        self.assertEqual(IdempotencyRecord.objects.filter(key=key).count(), 1)

        # Second execution with exact same key and payload
        req2 = self.factory.post(
            '/api/v1/test/',
            {'name': 'Ahmad'},
            format='json',
            HTTP_IDEMPOTENCY_KEY=key,
        )
        resp2 = view(req2)
        self.assertEqual(resp2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp2['X-Cache'], 'HIT-Idempotent')
        # View handler should not have been called a second time
        self.assertEqual(DummyActionView.call_count, 1)

    def test_same_key_with_different_payload_returns_409_conflict(self):
        view = DummyActionView.as_view()
        key = "idemp-key-002"

        # First request with payload A
        req1 = self.factory.post(
            '/api/v1/test/',
            {'name': 'Siti'},
            format='json',
            HTTP_IDEMPOTENCY_KEY=key,
        )
        resp1 = view(req1)
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)

        # Second request with same key but payload B
        req2 = self.factory.post(
            '/api/v1/test/',
            {'name': 'Dewi'},
            format='json',
            HTTP_IDEMPOTENCY_KEY=key,
        )
        resp2 = view(req2)
        self.assertEqual(resp2.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp2.data['error'], 'IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD')
        self.assertEqual(DummyActionView.call_count, 1)

    def test_error_response_is_not_stored_in_idempotency_record(self):
        view = DummyActionView.as_view()
        key = "idemp-key-error-003"

        req1 = self.factory.post(
            '/api/v1/test/',
            {'name': 'TRIGGER_ERROR'},
            format='json',
            HTTP_IDEMPOTENCY_KEY=key,
        )
        resp1 = view(req1)
        self.assertEqual(resp1.status_code, status.HTTP_400_BAD_REQUEST)
        # 4xx error should not be persisted in IdempotencyRecord
        self.assertEqual(IdempotencyRecord.objects.filter(key=key).count(), 0)
