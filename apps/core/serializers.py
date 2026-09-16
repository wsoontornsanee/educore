from rest_framework import serializers

from apps.core.models import StoredFile


class InitiateUploadSerializer(serializers.Serializer):
    purpose = serializers.CharField()
    filename = serializers.CharField()
    content_type = serializers.CharField()
    size = serializers.IntegerField(min_value=1)


class StoredFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoredFile
        fields = [
            'id', 'purpose', 'key', 'content_type', 'size', 'checksum',
            'uploaded_by', 'confirmed_at', 'created_at',
        ]
