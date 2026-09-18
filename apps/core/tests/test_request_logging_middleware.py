"""Tests for educore.middleware.logging.RequestLoggingMiddleware."""
import logging

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from educore.middleware.logging import RequestLoggingMiddleware, get_current_request_id


class RequestLoggingMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_sets_request_id_header_and_attribute(self):
        middleware = RequestLoggingMiddleware(lambda request: HttpResponse())
        response = middleware(self.factory.get('/api/v1/ping'))
        self.assertIn('X-Request-ID', response)
        self.assertEqual(len(response['X-Request-ID']), 32)

    def test_honors_incoming_x_request_id_header(self):
        middleware = RequestLoggingMiddleware(lambda request: HttpResponse())
        response = middleware(self.factory.get('/api/v1/ping', HTTP_X_REQUEST_ID='caller-supplied-id'))
        self.assertEqual(response['X-Request-ID'], 'caller-supplied-id')

    def test_request_id_available_via_thread_local_during_request(self):
        captured = {}

        def get_response(request):
            captured['request_id'] = get_current_request_id()
            return HttpResponse()

        middleware = RequestLoggingMiddleware(get_response)
        response = middleware(self.factory.get('/api/v1/ping'))
        self.assertEqual(captured['request_id'], response['X-Request-ID'])

    def test_thread_local_cleared_after_request(self):
        middleware = RequestLoggingMiddleware(lambda request: HttpResponse())
        middleware(self.factory.get('/api/v1/ping'))
        self.assertIsNone(get_current_request_id())

    def test_logs_access_line_even_when_get_response_raises(self):
        def get_response(request):
            raise ValueError('boom')

        middleware = RequestLoggingMiddleware(get_response)
        with self.assertLogs('educore.request', level='INFO') as captured:
            with self.assertRaises(ValueError):
                middleware(self.factory.get('/api/v1/boom'))
        self.assertEqual(len(captured.records), 1)
        self.assertIsNone(get_current_request_id())

    def test_logged_extra_fields_present(self):
        middleware = RequestLoggingMiddleware(lambda request: HttpResponse(status=201))
        logger = logging.getLogger('educore.request')
        with self.assertLogs(logger, level='INFO') as captured:
            middleware(self.factory.post('/api/v1/students'))
        record = captured.records[0]
        self.assertEqual(record.method, 'POST')
        self.assertEqual(record.path, '/api/v1/students')
        self.assertEqual(record.status_code, 201)
        self.assertIsInstance(record.duration_ms, float)
