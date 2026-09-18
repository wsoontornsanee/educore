"""Tests for the backup_database cron command (AGENTS red line #1, deploy/crontab)."""
import base64
import os
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.management.commands import backup_database as backup_cmd
from apps.core.models import JobRun

FAKE_KEY = base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode()
FAKE_DUMP = b"-- MySQL dump 10.13  Distrib 8.0.46\n-- Host: localhost\nCREATE TABLE t (id INT);\n"


def _fake_mysqldump(dest_path, dump_runner=None):
    with open(dest_path, 'wb') as f:
        f.write(FAKE_DUMP)


@mock.patch.object(backup_cmd, '_run_mysqldump', side_effect=_fake_mysqldump)
class BackupDatabaseCommandTests(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.env_patcher = mock.patch.dict(os.environ, {'DATABASE_BACKUP_KEY': FAKE_KEY})
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_missing_key_fails_loud_before_touching_anything(self, _mock_dump):
        os.environ.pop('DATABASE_BACKUP_KEY', None)
        with self.assertRaises(CommandError):
            call_command('backup_database', destination=self.tmp_dir, force=True)
        self.assertEqual(len(os.listdir(self.tmp_dir)), 0)

    def test_writes_encrypted_backup_and_checksum(self, _mock_dump):
        call_command('backup_database', destination=self.tmp_dir, force=True)

        files = os.listdir(self.tmp_dir)
        enc_files = [f for f in files if f.endswith('.sql.gz.enc')]
        sha_files = [f for f in files if f.endswith('.sha256')]
        self.assertEqual(len(enc_files), 1)
        self.assertEqual(len(sha_files), 1)

        enc_path = os.path.join(self.tmp_dir, enc_files[0])
        checksum = backup_cmd._sha256_of(enc_path)
        sha_content = open(os.path.join(self.tmp_dir, sha_files[0])).read()
        self.assertIn(checksum, sha_content)

    def test_encryption_actually_hides_the_sql(self, _mock_dump):
        call_command('backup_database', destination=self.tmp_dir, force=True)
        enc_files = [f for f in os.listdir(self.tmp_dir) if f.endswith('.sql.gz.enc')]
        raw = open(os.path.join(self.tmp_dir, enc_files[0]), 'rb').read()
        self.assertNotIn(b'CREATE TABLE', raw)

    def test_verify_restore_round_trips_the_real_dump(self, _mock_dump):
        call_command('backup_database', destination=self.tmp_dir, force=True, verify_restore=True)
        job = JobRun.objects.get(job_name='backup_database')
        self.assertEqual(job.status, JobRun.STATUS_SUCCESS)

    def test_verify_restore_raises_on_tampered_backup(self, _mock_dump):
        call_command('backup_database', destination=self.tmp_dir, force=True)
        enc_files = [f for f in os.listdir(self.tmp_dir) if f.endswith('.sql.gz.enc')]
        enc_path = os.path.join(self.tmp_dir, enc_files[0])
        with open(enc_path, 'r+b') as f:
            f.seek(0)
            f.write(b'\x00' * 16)  # corrupt the nonce/ciphertext

        with self.assertRaises(Exception):
            backup_cmd._verify_restore(enc_path, base64.urlsafe_b64decode(FAKE_KEY))

    def test_flat_retention_prunes_files_older_than_cutoff(self, _mock_dump):
        now = timezone.now()
        db_name = backup_cmd._safe_db_name(backup_cmd._db_params()['name'])
        old_ts = (now - timedelta(days=30)).strftime(backup_cmd.TIMESTAMP_FMT)
        recent_ts = (now - timedelta(days=1)).strftime(backup_cmd.TIMESTAMP_FMT)
        for ts in (old_ts, recent_ts):
            name = f"backup_{db_name}_{ts}.sql.gz.enc"
            open(os.path.join(self.tmp_dir, name), 'wb').write(b'x')
            open(os.path.join(self.tmp_dir, f"{name}.sha256"), 'w').write('x')

        call_command('backup_database', destination=self.tmp_dir, force=True, retention_days=14)

        remaining = [f for f in os.listdir(self.tmp_dir) if f.endswith('.sql.gz.enc')]
        # the old pre-seeded file is pruned; the recent pre-seeded one and today's new backup remain
        self.assertFalse(any(old_ts in f for f in remaining))
        self.assertTrue(any(recent_ts in f for f in remaining))

    def test_job_run_records_failure_on_dump_error(self, mock_dump):
        mock_dump.side_effect = CommandError("mysqldump failed (exit 1): access denied")
        with self.assertRaises(CommandError):
            call_command('backup_database', destination=self.tmp_dir, force=True)

        job = JobRun.objects.get(job_name='backup_database')
        self.assertEqual(job.status, JobRun.STATUS_FAILED)
        self.assertIn('access denied', job.error_text)
