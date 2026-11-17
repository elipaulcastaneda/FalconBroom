"""Passphrase-protected desktop vault key recovery."""

import os
import hashlib
import hmac
import json
import sqlite3
import struct
import uuid
from contextlib import closing
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import desktop_audit, desktop_vault
from .desktop_profiles import list_profiles, profile_directory


_MAGIC = b"FBRECOV1"
_SALT_SIZE = 16
_NONCE_SIZE = 12
_BUNDLE_SIZE = len(_MAGIC) + _SALT_SIZE + _NONCE_SIZE + 48
_MARKER_MAGIC = b"FBEXPORT1\0"


def _passphrase_key(passphrase: str, salt: bytes) -> bytes:
    if not isinstance(passphrase, str) or not 20 <= len(passphrase) <= 1024:
        raise desktop_vault.DesktopVaultError("Use a passphrase of 20 to 1024 characters")
    return Scrypt(salt=salt, length=32, n=2**16, r=8, p=1).derive(passphrase.encode("utf-8"))


def create_recovery_file(passphrase: str) -> bytes:
    _, master_key = desktop_vault._require_initialized()
    salt = os.urandom(_SALT_SIZE)
    nonce = os.urandom(_NONCE_SIZE)
    header = _MAGIC + salt + nonce
    return header + AESGCM(_passphrase_key(passphrase, salt)).encrypt(nonce, master_key, header)


def _open_recovery_file(bundle: bytes, passphrase: str) -> bytes:
    if len(bundle) != _BUNDLE_SIZE or not bundle.startswith(_MAGIC):
        raise desktop_vault.DesktopVaultError("Unrecognized recovery file")
    salt = bundle[len(_MAGIC):len(_MAGIC) + _SALT_SIZE]
    nonce = bundle[len(_MAGIC) + _SALT_SIZE:len(_MAGIC) + _SALT_SIZE + _NONCE_SIZE]
    try:
        key = AESGCM(_passphrase_key(passphrase, salt)).decrypt(
            nonce, bundle[len(_MAGIC) + _SALT_SIZE + _NONCE_SIZE:],
            bundle[:len(_MAGIC) + _SALT_SIZE + _NONCE_SIZE],
        )
    except InvalidTag as error:
        raise desktop_vault.DesktopVaultError("Recovery file or passphrase is incorrect") from error
    if len(key) != 32:
        raise desktop_vault.DesktopVaultError("Invalid recovery key")
    return key


def _redirected(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _verify_vault(root: Path, key: bytes) -> None:
    if _redirected(root):
        raise desktop_vault.DesktopVaultError("Vault directory is redirected")
    found = False

    def fail_walk(error):
        raise desktop_vault.DesktopVaultError("Cannot inspect the existing vault") from error

    for folder, dirs, files in os.walk(root, onerror=fail_walk, followlinks=False):
        for name in dirs:
            if _redirected(Path(folder) / name):
                raise desktop_vault.DesktopVaultError("Vault contains a redirected directory")
        for name in files:
            if not name.endswith(".fbenc"):
                continue
            path = Path(folder) / name
            if _redirected(path):
                raise desktop_vault.DesktopVaultError("Vault contains a redirected encrypted record")
            with path.open("rb") as file:
                header = desktop_vault._read_exact(file, desktop_vault._HEADER_SIZE)
            if not header.startswith(desktop_vault._MAGIC):
                raise desktop_vault.DesktopVaultError("Unrecognized encrypted record")
            cursor = len(desktop_vault._MAGIC)
            asset_id = header[cursor:cursor + 16]
            cursor += 16
            if struct.unpack(">I", header[cursor:cursor + 4])[0] != desktop_vault._CHUNK_SIZE:
                raise desktop_vault.DesktopVaultError("Unsupported encrypted record")
            cursor += 4
            nonce = header[cursor:cursor + 12]
            try:
                AESGCM(key).decrypt(nonce, header[cursor + 12:], desktop_vault._MAGIC + asset_id)
            except InvalidTag as error:
                raise desktop_vault.DesktopVaultError("Recovery key does not match the existing vault") from error
            found = True

    audit = root / ".vault" / "audit.sqlite3"
    if audit.exists():
        if any(_redirected(Path(str(audit) + suffix)) for suffix in ("", "-wal", "-shm", "-journal")):
            raise desktop_vault.DesktopVaultError("Audit storage is redirected")
        derived = HKDF(
            algorithm=hashes.SHA256(), length=96, salt=None,
            info=b"FalconBroom desktop audit v1",
        ).derive(key)
        try:
            with closing(sqlite3.connect(audit.as_uri() + "?mode=ro", uri=True)) as connection:
                desktop_audit._verify(connection, derived[:32], derived[32:64])
        except sqlite3.Error as error:
            raise desktop_vault.DesktopVaultError("Cannot verify encrypted audit storage") from error
        found = True
    if not found:
        raise desktop_vault.DesktopVaultError("No encrypted vault records are available to verify this key")


def restore_key(root: Path, bundle: bytes, passphrase: str) -> None:
    root = Path(root)
    if _redirected(root) or _redirected(root / ".vault"):
        raise desktop_vault.DesktopVaultError("Vault directory is redirected")
    root = root.resolve()
    key_path = root / ".vault" / "master.dpapi"
    if _redirected(key_path):
        raise desktop_vault.DesktopVaultError("Vault key is redirected")
    replace_unreadable = False
    if key_path.exists():
        try:
            desktop_vault._load_master_key(key_path)
        except desktop_vault.DesktopVaultUnlockError:
            replace_unreadable = True
        else:
            raise desktop_vault.DesktopVaultError("An existing usable vault key will not be replaced")
    key = _open_recovery_file(bundle, passphrase)
    _verify_vault(root, key)
    wrapped = desktop_vault._KEY_MAGIC + desktop_vault._dpapi(key, decrypt=False)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = key_path.with_name(f".{key_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("xb") as file:
            file.write(wrapped)
            file.flush()
            os.fsync(file.fileno())
        if replace_unreadable:
            backup = key_path.with_name(f"master.dpapi.unreadable-{uuid.uuid4().hex}")
            os.link(key_path, backup)
            os.replace(tmp, key_path)
        else:
            try:
                os.link(tmp, key_path)
            except FileExistsError as error:
                raise desktop_vault.DesktopVaultError("A vault key appeared; no key was replaced") from error
    finally:
        tmp.unlink(missing_ok=True)


def export_recovery_file(destination: Path, passphrase: str, excluded_roots: tuple[Path, ...]) -> None:
    root, key = desktop_vault._require_initialized()
    destination = Path(destination)
    if not destination.is_absolute() or _redirected(destination) or _redirected(destination.parent):
        raise desktop_vault.DesktopVaultError("Choose a non-redirected recovery file outside app data")
    destination = destination.resolve()
    if any(destination.is_relative_to(path.resolve()) for path in excluded_roots):
        raise desktop_vault.DesktopVaultError("Save the recovery file outside FalconBroom app data")
    bundle = create_recovery_file(passphrase)
    if _open_recovery_file(bundle, passphrase) != key:
        raise desktop_vault.DesktopVaultError("Recovery file verification failed")
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as file:
            file.write(bundle)
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError as error:
            raise desktop_vault.DesktopVaultError("Recovery destination already exists; choose a new name") from error
    finally:
        temporary.unlink(missing_ok=True)
    if destination.read_bytes() != bundle:
        raise desktop_vault.DesktopVaultError("Saved recovery file could not be verified")
    marker = root / ".vault" / "recovery-export.dpapi"
    if _redirected(marker):
        raise desktop_vault.DesktopVaultError("Recovery export record is redirected")
    metadata = json.dumps({
        "path": str(destination),
        "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
        "key_sha256": hashlib.sha256(key).hexdigest(),
    }, separators=(",", ":")).encode("utf-8")
    wrapped = _MARKER_MAGIC + desktop_vault._dpapi(metadata, decrypt=False)
    tmp = marker.with_name(f".{marker.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("xb") as file:
            file.write(wrapped)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, marker)
    finally:
        tmp.unlink(missing_ok=True)


def verify_export(root: Path, excluded_roots: tuple[Path, ...]) -> None:
    key_path = root / ".vault" / "master.dpapi"
    marker = root / ".vault" / "recovery-export.dpapi"
    if _redirected(root) or _redirected(root / ".vault") or _redirected(key_path) or _redirected(marker):
        raise desktop_vault.DesktopVaultError("Vault recovery record is redirected")
    if not key_path.is_file():
        def fail_walk(error):
            raise desktop_vault.DesktopVaultError("Cannot inspect the existing vault") from error

        for folder, dirs, files in os.walk(root, onerror=fail_walk, followlinks=False):
            if any(_redirected(Path(folder) / name) for name in dirs + files):
                raise desktop_vault.DesktopVaultError("Vault contains a redirected path")
            if any(name.endswith(".fbenc") for name in files):
                raise desktop_vault.DesktopVaultError("An existing vault key is missing")
        if (root / ".vault" / "audit.sqlite3").exists():
            raise desktop_vault.DesktopVaultError("An existing vault key is missing")
        return
    key = desktop_vault._load_master_key(key_path)
    try:
        wrapped = marker.read_bytes()
        if not wrapped.startswith(_MARKER_MAGIC) or len(wrapped) > 16384:
            raise ValueError("Invalid export record")
        record = json.loads(desktop_vault._dpapi(wrapped[len(_MARKER_MAGIC):], decrypt=True))
        if not isinstance(record, dict) or set(record) != {"path", "bundle_sha256", "key_sha256"}:
            raise ValueError("Invalid export record")
        if not all(isinstance(value, str) for value in record.values()):
            raise ValueError("Invalid export record")
        destination = Path(record["path"])
        if not destination.is_absolute() or _redirected(destination) or _redirected(destination.parent):
            raise ValueError("Invalid recovery destination")
        destination = destination.resolve()
        if any(destination.is_relative_to(path.resolve()) for path in excluded_roots):
            raise ValueError("Recovery destination is inside app data")
        with destination.open("rb") as file:
            bundle = file.read(_BUNDLE_SIZE + 1)
        if len(bundle) != _BUNDLE_SIZE or not bundle.startswith(_MAGIC):
            raise ValueError("Invalid recovery file")
        if not hmac.compare_digest(hashlib.sha256(bundle).hexdigest(), record["bundle_sha256"]):
            raise ValueError("Recovery file changed")
        if not hmac.compare_digest(hashlib.sha256(key).hexdigest(), record["key_sha256"]):
            raise ValueError("Vault key changed")
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise desktop_vault.DesktopVaultError("Recovery export is missing or cannot be verified") from error


def verify_uninstall_exports(base: Path, local_data: Path) -> None:
    base = Path(base)
    local_data = Path(local_data)
    excluded = (base, local_data)

    def fail_walk(error):
        raise desktop_vault.DesktopVaultError("Cannot inspect app data before deletion") from error

    for root in excluded:
        if _redirected(root):
            raise desktop_vault.DesktopVaultError("App data is redirected")
        if not root.exists():
            continue
        for folder, dirs, files in os.walk(root, onerror=fail_walk, followlinks=False):
            if any(_redirected(Path(folder) / name) for name in dirs + files):
                raise desktop_vault.DesktopVaultError("App data contains a redirected path")
    for profile in list_profiles(base):
        verify_export(profile_directory(base, profile) / "data", excluded)
