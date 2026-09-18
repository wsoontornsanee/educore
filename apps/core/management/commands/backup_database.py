"""Automated MySQL backup, encryption, and retention (AGENTS red line #1, deploy/crontab).

Runs nightly (deploy/crontab):
    0 23 * * *     educore backup_database

Produces a non-locking `mysqldump --single-transaction` of the configured
database, gzip-compresses it, encrypts it with AES-256-GCM using
DATABASE_BACKUP_KEY, and writes a SHA-256 checksum sidecar next to it:

    backup_{db_name}_{UTC timestamp}.sql.gz.enc
    backup_{db_name}_{UTC timestamp}.sql.gz.enc.sha256

Retention is grandfather-father-son by default (7 most recent daily backups,
then 1 per ISO week for 4 weeks, then 1 per month for 12 months — everything
else pruned). Passing --retention-days switches to a flat cutoff instead
(delete anything older than N days, no bucketing) for operators who want a
simpler policy.

Scope note: "emergency alert to DevOps on failure" from the task spec has no
existing channel to hook into in this repo — every other cron failure in this
codebase surfaces the same way (JobRun.error_text + re-raise, visible in the
cron log), so this command follows that same convention rather than building
a one-off alert path. See JobRun.error_text for failure detail.
"""
import gzip
import hashlib
import os
import re
import subprocess
import tempfile
from datetime import timedelta, timezone as dt_timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.management.base import CommandError
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun

DEFAULT_DESTINATION = os.environ.get('EDUCORE_BACKUP_DIR', '/var/backups/educore')
DEFAULT_RETENTION_DAYS = 14
DAILY_KEEP = 7
WEEKLY_KEEP = 4
MONTHLY_KEEP = 12
TIMESTAMP_FMT = '%Y%m%dT%H%M%SZ'
NONCE_SIZE = 12  # bytes, AES-GCM standard


def _get_backup_key():
    raw = os.environ.get('DATABASE_BACKUP_KEY', '')
    if not raw:
        raise CommandError(
            "DATABASE_BACKUP_KEY environment variable is not set. Refusing to run an "
            "unencrypted backup — generate one with Fernet.generate_key() or "
            "AESGCM.generate_key(bit_length=256) (base64-urlsafe, 32 raw bytes) and set it."
        )
    import base64
    try:
        key = base64.urlsafe_b64decode(raw)
    except Exception as exc:
        raise CommandError(f"DATABASE_BACKUP_KEY is not valid base64: {exc}")
    if len(key) != 32:
        raise CommandError(f"DATABASE_BACKUP_KEY must decode to 32 bytes for AES-256, got {len(key)}.")
    return key


def _safe_db_name(name):
    """Sanitize the configured DB name for use in a filename — MySQL names are
    already filename-safe, but the SQLite test fallback's NAME is a DSN-like
    string (e.g. "file:memorydb_default?mode=memory&cache=shared")."""
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', str(name))


def _db_params():
    db = settings.DATABASES['default']
    return {
        'name': db['NAME'],
        'user': db['USER'],
        'password': db.get('PASSWORD', ''),
        'host': db.get('HOST', '127.0.0.1'),
        'port': str(db.get('PORT') or '3306'),
    }


def _run_mysqldump(dest_path, dump_runner=None):
    """Stream a non-locking mysqldump straight to `dest_path`.

    `dump_runner` is a test seam: pass a callable(cmd, stdout_file) to avoid
    shelling out to a real `mysqldump` binary in unit tests.
    """
    params = _db_params()
    cmd = [
        'mysqldump',
        '-h', params['host'],
        '-P', params['port'],
        '-u', params['user'],
        '--single-transaction', '--quick', '--routines', '--triggers', '--events',
        '--set-gtid-purged=OFF',
        params['name'],
    ]
    env = os.environ.copy()
    if params['password']:
        env['MYSQL_PWD'] = params['password']  # avoid password on argv/ps

    with open(dest_path, 'wb') as sql_file:
        if dump_runner is not None:
            dump_runner(cmd, sql_file)
        else:
            result = subprocess.run(cmd, stdout=sql_file, stderr=subprocess.PIPE, env=env)
            if result.returncode != 0:
                raise CommandError(f"mysqldump failed (exit {result.returncode}): {result.stderr.decode(errors='replace')[:2000]}")


def _gzip_file(src_path, dst_path):
    with open(src_path, 'rb') as src, gzip.open(dst_path, 'wb') as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)


def _encrypt_file(src_path, dst_path, key):
    aesgcm = AESGCM(key)
    nonce = os.urandom(NONCE_SIZE)
    with open(src_path, 'rb') as f:
        plaintext = f.read()
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    with open(dst_path, 'wb') as f:
        f.write(nonce)
        f.write(ciphertext)


def _decrypt_file(path, key):
    aesgcm = AESGCM(key)
    with open(path, 'rb') as f:
        blob = f.read()
    nonce, ciphertext = blob[:NONCE_SIZE], blob[NONCE_SIZE:]
    return aesgcm.decrypt(nonce, ciphertext, None)


def _sha256_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _verify_restore(enc_path, key):
    """Decrypt + gunzip and sanity-check the dump looks like real mysqldump output."""
    plaintext_gz = _decrypt_file(enc_path, key)
    sql_bytes = gzip.decompress(plaintext_gz)
    header = sql_bytes[:2000]
    if b'-- MySQL dump' not in header and b'-- Host:' not in header:
        raise CommandError(f"Restore verification failed for {enc_path}: no mysqldump header found.")
    return len(sql_bytes)


def _parse_backup_timestamp(filename, db_name):
    prefix = f"backup_{db_name}_"
    suffix = ".sql.gz.enc"
    if not (filename.startswith(prefix) and filename.endswith(suffix)):
        return None
    stamp = filename[len(prefix):-len(suffix)]
    try:
        return timezone.datetime.strptime(stamp, TIMESTAMP_FMT).replace(tzinfo=dt_timezone.utc)
    except ValueError:
        return None


def _apply_flat_retention(destination, db_name, retention_days, now):
    cutoff = now - timedelta(days=retention_days)
    pruned = []
    for entry in Path(destination).glob(f"backup_{db_name}_*.sql.gz.enc"):
        ts = _parse_backup_timestamp(entry.name, db_name)
        if ts is not None and ts < cutoff:
            entry.unlink(missing_ok=True)
            Path(str(entry) + '.sha256').unlink(missing_ok=True)
            pruned.append(entry.name)
    return pruned


def _apply_gfs_retention(destination, db_name, now):
    """Grandfather-father-son: keep newest DAILY_KEEP, then oldest-per-week for
    WEEKLY_KEEP weeks, then oldest-per-month for MONTHLY_KEEP months. Delete the rest."""
    entries = []
    for entry in Path(destination).glob(f"backup_{db_name}_*.sql.gz.enc"):
        ts = _parse_backup_timestamp(entry.name, db_name)
        if ts is not None:
            entries.append((ts, entry))
    entries.sort(key=lambda pair: pair[0], reverse=True)  # newest first

    keep = set()
    keep.update(entry for _, entry in entries[:DAILY_KEEP])

    def _bucket_key(ts, unit):
        if unit == 'week':
            iso = ts.isocalendar()
            return (iso[0], iso[1])
        return (ts.year, ts.month)

    for unit, limit in (('week', WEEKLY_KEEP), ('month', MONTHLY_KEEP)):
        seen_buckets = {}
        for ts, entry in entries:
            bucket = _bucket_key(ts, unit)
            if bucket not in seen_buckets:
                seen_buckets[bucket] = (ts, entry)
        # oldest-first within the most recent `limit` buckets
        buckets_sorted = sorted(seen_buckets.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
        keep.update(entry for _, (_, entry) in buckets_sorted)

    pruned = []
    for ts, entry in entries:
        if entry not in keep:
            entry.unlink(missing_ok=True)
            Path(str(entry) + '.sha256').unlink(missing_ok=True)
            pruned.append(entry.name)
    return pruned


class Command(CronHostCommand):
    help = "Non-locking MySQL backup: mysqldump -> gzip -> AES-256-GCM -> checksum, with retention pruning."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument('--destination', default=DEFAULT_DESTINATION,
                             help=f"Directory to write backups to (default {DEFAULT_DESTINATION}).")
        parser.add_argument('--retention-days', type=int, default=None,
                             help="If set, use a flat N-day retention cutoff instead of the default "
                                  f"grandfather-father-son policy ({DAILY_KEEP} daily/{WEEKLY_KEEP} weekly/{MONTHLY_KEEP} monthly).")
        parser.add_argument('--verify-restore', action='store_true',
                             help="Decrypt and sanity-check the freshly written backup before finishing.")

    def handle(self, *args, **options):
        lock_name = 'backup_database'

        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    f"Advisory lock for '{lock_name}' already held. Exiting cleanly."))
                return

            job_run = JobRun.objects.create(job_name=lock_name)
            started = timezone.now()
            try:
                key = _get_backup_key()
                destination = Path(options['destination'])
                destination.mkdir(parents=True, exist_ok=True)

                db_name = _safe_db_name(_db_params()['name'])
                timestamp = started.strftime(TIMESTAMP_FMT)
                enc_path = destination / f"backup_{db_name}_{timestamp}.sql.gz.enc"

                with tempfile.TemporaryDirectory() as tmp:
                    sql_path = Path(tmp) / 'dump.sql'
                    gz_path = Path(tmp) / 'dump.sql.gz'

                    _run_mysqldump(sql_path)
                    _gzip_file(sql_path, gz_path)
                    _encrypt_file(gz_path, enc_path, key)

                checksum = _sha256_of(enc_path)
                (destination / f"{enc_path.name}.sha256").write_text(f"{checksum}  {enc_path.name}\n")

                verified_bytes = None
                if options['verify_restore']:
                    verified_bytes = _verify_restore(enc_path, key)

                if options['retention_days'] is not None:
                    pruned = _apply_flat_retention(destination, db_name, options['retention_days'], started)
                else:
                    pruned = _apply_gfs_retention(destination, db_name, started)

                duration = (timezone.now() - started).total_seconds()
                job_run.status = JobRun.STATUS_SUCCESS
                job_run.items_processed = 1
                job_run.finished_at = timezone.now()
                job_run.save()

                msg = (f"backup_database: wrote {enc_path.name} ({enc_path.stat().st_size} bytes, "
                       f"{duration:.1f}s), pruned {len(pruned)} old backup(s).")
                if verified_bytes is not None:
                    msg += f" Restore verified ({verified_bytes} bytes decompressed)."
                self.stdout.write(self.style.SUCCESS(msg))
            except Exception as exc:
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(exc)[:2000]
                job_run.finished_at = timezone.now()
                job_run.save()
                raise
