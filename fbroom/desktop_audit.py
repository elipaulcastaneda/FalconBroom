"""Encrypted, transactional desktop audit entries."""

import hashlib
import hmac
import json
import os
import sqlite3
import struct
from contextlib import closing
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import desktop_vault


_GENESIS = bytes(32)
_IDENTIFIERS = {"id", "user_id", "requester_user_id", "job_id", "dsar_id"}


def _database_path():
    path = Path("data") / ".vault" / "audit.sqlite3"
    if path.parent.is_symlink() or any(Path(str(path) + suffix).is_symlink() for suffix in ("", "-wal", "-shm", "-journal")):
        raise desktop_vault.DesktopVaultError("Audit storage is redirected")
    root, _ = desktop_vault._require_initialized()
    if path.parent.resolve() != root / ".vault":
        raise desktop_vault.DesktopVaultError("Audit storage is outside the desktop vault")
    return path


def _open():
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    connection.execute("PRAGMA synchronous=FULL")
    return connection


def _mac(key, sequence, previous, nonce, ciphertext):
    return hmac.new(key, struct.pack(">Q", sequence) + previous + nonce + ciphertext, hashlib.sha256).digest()


def _head_mac(key, sequence, head):
    return hmac.new(key, b"head" + struct.pack(">Q", sequence) + head, hashlib.sha256).digest()


def _verify(connection, encryption_key, chain_key):
    head_row = connection.execute("SELECT sequence, digest, signature FROM head WHERE id = 1").fetchone()
    if head_row is None:
        raise desktop_vault.DesktopVaultError("Audit chain head is missing")
    head_sequence, head, signature = head_row
    if not hmac.compare_digest(signature, _head_mac(chain_key, head_sequence, head)):
        raise desktop_vault.DesktopVaultError("Audit chain head is invalid")
    previous = _GENESIS
    entries = []
    for sequence, nonce, ciphertext, digest in connection.execute(
        "SELECT sequence, nonce, ciphertext, digest FROM entries ORDER BY sequence"
    ):
        if sequence != len(entries) + 1 or not hmac.compare_digest(digest, _mac(chain_key, sequence, previous, nonce, ciphertext)):
            raise desktop_vault.DesktopVaultError("Audit chain is invalid")
        try:
            raw = AESGCM(encryption_key).decrypt(nonce, ciphertext, struct.pack(">Q", sequence) + previous)
            entry = json.loads(raw)
        except (InvalidTag, ValueError, UnicodeDecodeError) as error:
            raise desktop_vault.DesktopVaultError("Audit entry cannot be authenticated") from error
        if not isinstance(entry, dict):
            raise desktop_vault.DesktopVaultError("Invalid audit entry")
        entries.append(entry)
        previous = digest
    if head_sequence != len(entries) or not hmac.compare_digest(head, previous):
        raise desktop_vault.DesktopVaultError("Audit chain is incomplete")
    return entries, previous


def _prepare(connection, chain_key):
    connection.execute("CREATE TABLE IF NOT EXISTS entries (sequence INTEGER PRIMARY KEY, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, digest BLOB NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS head (id INTEGER PRIMARY KEY CHECK(id=1), sequence INTEGER NOT NULL, digest BLOB NOT NULL, signature BLOB NOT NULL)")
    if connection.execute("SELECT COUNT(*) FROM head").fetchone()[0] == 0:
        if connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0] != 0:
            raise desktop_vault.DesktopVaultError("Audit chain head is missing")
        connection.execute("INSERT INTO head VALUES (1, 0, ?, ?)", (_GENESIS, _head_mac(chain_key, 0, _GENESIS)))


def append(source: str, event: str, payload: dict, timestamp: str):
    encryption_key, chain_key, identity_key = desktop_vault.audit_keys()
    identifiers = {
        name: hmac.new(identity_key, str(value).encode("utf-8"), hashlib.sha256).hexdigest()
        for name, value in (payload or {}).items()
        if name in _IDENTIFIERS and isinstance(value, (str, int)) and value != ""
    }
    entry = {"source": source, "event": event, "ts": timestamp, "identifiers": identifiers}
    encoded = json.dumps(entry, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    with closing(_open()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _prepare(connection, chain_key)
        entries, previous = _verify(connection, encryption_key, chain_key)
        sequence = len(entries) + 1
        nonce = os.urandom(12)
        ciphertext = AESGCM(encryption_key).encrypt(nonce, encoded, struct.pack(">Q", sequence) + previous)
        digest = _mac(chain_key, sequence, previous, nonce, ciphertext)
        connection.execute("INSERT INTO entries VALUES (?, ?, ?, ?)", (sequence, nonce, ciphertext, digest))
        connection.execute("UPDATE head SET sequence=?, digest=?, signature=? WHERE id=1", (sequence, digest, _head_mac(chain_key, sequence, digest)))
        connection.commit()


def read_all():
    encryption_key, chain_key, _ = desktop_vault.audit_keys()
    if not _database_path().exists():
        return []
    with closing(_open()) as connection:
        connection.execute("BEGIN")
        entries, _ = _verify(connection, encryption_key, chain_key)
        return entries
