"""Tests for UU PDP data subject rights & retention tooling (spec/14 §3, CMP-011..013).

CMP-011 (DSAR access export) is covered by apps/compliance/tests/test_statutory_export.py-style
ExportJob renderer tests in Task 2, reusing the CMP-016 PII-export pipeline directly — there is
no separate DataSubjectRequest bookkeeping for access requests. This file covers erasure
(CMP-012) and, via apps/attendance/tests/test_purge_gate_photos.py, retention (CMP-013).
"""
from django.test import TestCase

from apps.compliance.models import (
    DataSubjectRequest,
    DataSubjectRequestStatus,
    DataSubjectRequestSubjectType,
)
from apps.identity.models import Foundation
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class DataSubjectRequestModelTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji", brand_name="Uji", npwp="01.000.000.0-001.000",
        )
        set_current_foundation_id(self.foundation.id)

    def tearDown(self):
        clear_current_foundation_id()

    def test_create_refused_erasure_request(self):
        req = DataSubjectRequest.objects.create(
            foundation_id=self.foundation.id,
            subject_type=DataSubjectRequestSubjectType.STUDENT,
            subject_id=1,
            status=DataSubjectRequestStatus.REFUSED,
            requested_by="42",
            requested_by_name="Ketua Yayasan",
            refusal_reason="Status saat ini bukan status keluar/lulus.",
        )
        self.assertEqual(req.status, DataSubjectRequestStatus.REFUSED)
        self.assertTrue(req.refusal_reason)
