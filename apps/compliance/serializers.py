"""Serializers for compliance models (spec/14)."""
from rest_framework import serializers

from apps.compliance.models import PiiExportAccessLog


class PiiExportAccessLogSerializer(serializers.ModelSerializer):
    """Read-only serializer for PII export access audit log entries (CMP-016, RPT-004)."""
    export_job_id = serializers.IntegerField(source='export_job.id', read_only=True)

    class Meta:
        model = PiiExportAccessLog
        fields = [
            'id',
            'foundation_id',
            'export_job_id',
            'report_key',
            'format',
            'exported_by_id',
            'exported_by_name',
            'school_id',
            'filters',
            'record_count',
            'watermark_text',
            'file_name',
            'file_size',
            'download_count',
            'last_downloaded_at',
            'created_at',
        ]
        read_only_fields = fields
