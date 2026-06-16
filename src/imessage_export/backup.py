"""Locate the Messages database inside an iTunes/Finder iPhone backup.

A desktop backup stores every file under an opaque SHA-1 name. The mapping from
real path to stored name lives in ``Manifest.db``. The Messages database is the
file with domain ``HomeDomain`` and relative path ``Library/SMS/sms.db``.

Encrypted backups keep ``Manifest.db`` itself encrypted; this module detects
that case and reports it rather than guessing, since decryption requires the
backup password and is out of scope for v1.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3

SMS_DOMAIN = "HomeDomain"
SMS_RELATIVE_PATH = "Library/SMS/sms.db"


class BackupError(RuntimeError):
    pass


def _is_encrypted(backup_dir: str) -> bool:
    manifest_plist = os.path.join(backup_dir, "Manifest.plist")
    if not os.path.exists(manifest_plist):
        return False
    try:
        import plistlib

        with open(manifest_plist, "rb") as f:
            data = plistlib.load(f)
        return bool(data.get("IsEncrypted"))
    except Exception:
        return False


def locate_sms_db(backup_dir: str) -> str:
    """Return the on-disk path to ``sms.db`` within *backup_dir*."""
    backup_dir = os.path.abspath(backup_dir)
    manifest_db = os.path.join(backup_dir, "Manifest.db")
    if not os.path.exists(manifest_db):
        raise BackupError(
            f"no Manifest.db in {backup_dir!r}; this is not an iTunes/Finder "
            "backup folder (point --backup at e.g. an entry under "
            "'~/Library/Application Support/MobileSync/Backup/<id>')."
        )
    if _is_encrypted(backup_dir):
        raise BackupError(
            "this backup is encrypted; v1 cannot read encrypted backups. "
            "Either disable backup encryption and re-back-up, or export from a "
            "Mac's live database (~/Library/Messages/chat.db)."
        )

    # Manifest.db is itself plain SQLite for unencrypted backups.
    uri = f"file:{manifest_db}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        row = conn.execute(
            "SELECT fileID FROM Files WHERE domain=? AND relativePath=?",
            (SMS_DOMAIN, SMS_RELATIVE_PATH),
        ).fetchone()
    except sqlite3.DatabaseError as e:
        raise BackupError(
            f"could not read Manifest.db ({e}); the backup may be encrypted "
            "or corrupt."
        ) from e
    finally:
        conn.close()

    if not row:
        # Fall back to deriving the legacy hashed name (pre-iOS 10 layout).
        file_id = hashlib.sha1(
            f"{SMS_DOMAIN}-{SMS_RELATIVE_PATH}".encode()
        ).hexdigest()
    else:
        file_id = row[0]

    candidate = os.path.join(backup_dir, file_id[:2], file_id)
    if not os.path.exists(candidate):
        candidate_flat = os.path.join(backup_dir, file_id)
        if os.path.exists(candidate_flat):
            return candidate_flat
        raise BackupError(
            f"sms.db entry found in manifest (fileID {file_id}) but the file is "
            f"missing from the backup at {candidate!r}."
        )
    return candidate
