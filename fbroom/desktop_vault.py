"""Authenticated, current-user encrypted storage for the Windows desktop app."""

import contextlib
import contextvars
import ctypes
import hashlib
import io
import logging
import os
import shutil
import struct
import threading
import time
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_MAGIC = b"FBVAULT2"
_KEY_MAGIC = b"FBKEY1\0"
_CHUNK_SIZE = 1024 * 1024
_MAX_CIPHERTEXT_CHUNK = _CHUNK_SIZE + 16
_HEADER_SIZE = len(_MAGIC) + 16 + 4 + 12 + 48
_lock = threading.RLock()
_root: Path | None = None
_master_key: bytes | None = None
_workspace: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "falconbroom_desktop_vault_workspace", default=None
)
_scratch_lock = threading.RLock()
logger = logging.getLogger(__name__)


class DesktopVaultError(RuntimeError):
    pass


def _load_master_key(key_path: Path) -> bytes:
    try:
        wrapped = key_path.read_bytes()
        if not wrapped.startswith(_KEY_MAGIC):
            raise ValueError("Invalid key file header")
        key = _dpapi(wrapped[len(_KEY_MAGIC):], decrypt=True)
        if len(key) != 32:
            raise ValueError("Invalid master key length")
        return key
    except (OSError, ValueError, DesktopVaultError) as error:
        raise DesktopVaultError(
            "Could not unlock FalconBroom's desktop data key; encrypted data was left unchanged"
        ) from error


def _dpapi(data: bytes, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise DesktopVaultError("Desktop encrypted storage requires Windows DPAPI")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), source_buffer)
    target = Blob()

    if decrypt:
        fn = crypt32.CryptUnprotectData
        fn.argtypes = [
            ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob),
        ]
        args = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        fn = crypt32.CryptProtectData
        fn.argtypes = [
            ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob),
        ]
        args = (
            ctypes.byref(source), "FalconBroom desktop data key",
            None, None, None, 1, ctypes.byref(target),
        )
    fn.restype = wintypes.BOOL
    if not fn(*args):
        raise DesktopVaultError(
            f"Windows user-bound key protection failed (error {ctypes.get_last_error()})"
        )
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(target.data)


def initialize(root: Path | str) -> None:
    """Load or create the DPAPI-protected vault key; never replace a lost key."""
    global _root, _master_key
    root = Path(root).resolve()
    if os.name != "nt":
        raise DesktopVaultError("Encrypted desktop storage is currently supported on Windows only")

    key_path = root / ".vault" / "master.dpapi"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        if key_path.exists():
            key = _load_master_key(key_path)
        else:
            if any(root.rglob("*.fbenc")) or (root / ".vault" / "audit.sqlite3").exists():
                raise DesktopVaultError(
                    "Encrypted FalconBroom data exists but its protected key is missing"
                )
            key = os.urandom(32)
            wrapped = _KEY_MAGIC + _dpapi(key, decrypt=False)
            tmp = key_path.with_name(f".{key_path.name}.{uuid.uuid4().hex}.tmp")
            try:
                with tmp.open("xb") as file:
                    file.write(wrapped)
                    file.flush()
                    os.fsync(file.fileno())
                try:
                    os.link(tmp, key_path)
                except FileExistsError:
                    key = _load_master_key(key_path)
            finally:
                tmp.unlink(missing_ok=True)
        _root = root
        _master_key = key


def _require_initialized() -> tuple[Path, bytes]:
    if _root is None or _master_key is None:
        raise DesktopVaultError("Desktop encrypted storage has not been initialized")
    return _root, _master_key


def is_desktop_enabled() -> bool:
    return bool(os.environ.get("FALCONBROOM_DESKTOP_SECRET"))


def is_initialized() -> bool:
    return _root is not None and _master_key is not None


def audit_keys() -> tuple[bytes, bytes, bytes]:
    _, master_key = _require_initialized()
    derived = HKDF(
        algorithm=hashes.SHA256(), length=96, salt=None,
        info=b"FalconBroom desktop audit v1",
    ).derive(master_key)
    return derived[:32], derived[32:64], derived[64:]


def current_workspace() -> Path:
    workspace = _workspace.get()
    if workspace is None:
        raise DesktopVaultError("No desktop plaintext workspace is active")
    return workspace


def _is_managed_output(path: Path) -> bool:
    path = Path(path)
    if not is_initialized():
        return False
    root, _ = _require_initialized()
    absolute = Path(os.path.abspath(path))
    managed_roots = [root / name for name in ("outputs", "uploads")]
    managed = next((base for base in managed_roots if absolute.is_relative_to(base)), None)
    if managed is None:
        return False
    if path.is_symlink() or encrypted_path(path).is_symlink():
        raise DesktopVaultError("Refusing to write a redirected output")
    if managed.is_symlink() or not path.resolve().is_relative_to(managed):
        raise DesktopVaultError("Refusing to write outside managed output storage")
    return True


def stage_managed_output(path: Path) -> Path:
    """Keep plaintext for managed outputs inside the request workspace."""
    path = Path(path)
    if not _is_managed_output(path):
        return path
    folder = current_workspace() / uuid.uuid4().hex
    folder.mkdir()
    return folder / path.name


def publish_staged_output(staged: Path, destination: Path) -> None:
    staged, destination = Path(staged), Path(destination)
    if staged == destination:
        return
    seal_file(staged, destination)
    destination.unlink(missing_ok=True)


def copy_to_managed_output(source: Path, destination: Path) -> None:
    source, destination = Path(source), Path(destination)
    if not _is_managed_output(destination):
        shutil.copyfile(source, destination)
    else:
        seal_file(source, destination, remove_source=False)
        destination.unlink(missing_ok=True)


def encrypted_path(path: Path) -> Path:
    path = Path(path)
    return path if path.name.endswith(".fbenc") else path.with_name(path.name + ".fbenc")


def logical_path(path: Path) -> Path:
    path = Path(path)
    if path.name.endswith(".fbenc"):
        return path.with_name(path.name[:-len(".fbenc")])
    return path


def exists(path: Path) -> bool:
    path = Path(path)
    return path.is_file() or encrypted_path(path).is_file()


def logical_files(directory: Path):
    directory = Path(directory)
    if not directory.exists():
        return []
    files = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        if path.name.endswith(".fbenc"):
            files.append(logical_path(path))
        elif not encrypted_path(path).exists():
            files.append(path)
    return files


def read_bytes(path: Path) -> bytes:
    path = Path(path)
    if path.is_file():
        return path.read_bytes()
    encrypted = encrypted_path(path)
    if not encrypted.is_file():
        raise FileNotFoundError(path)
    root, key = _require_initialized()
    if not encrypted.resolve().is_relative_to(root):
        raise DesktopVaultError("Encrypted source is outside app data")
    output = io.BytesIO()
    with encrypted.open("rb") as source:
        _decrypt_stream(source, output, key)
    return output.getvalue()


def read_text(path: Path, encoding: str = "utf-8") -> str:
    return read_bytes(path).decode(encoding)


def write_encrypted_bytes(path: Path, data: bytes) -> Path:
    root, key = _require_initialized()
    path = Path(path)
    destination = encrypted_path(path)
    if not destination.parent.resolve().is_relative_to(root) or destination.is_symlink() or path.is_symlink():
        raise DesktopVaultError("Invalid encrypted record destination")
    with _lock:
        _write_encrypted(io.BytesIO(data), destination, key)
        path.unlink(missing_ok=True)
    return destination


def delete(path: Path) -> None:
    path = Path(path)
    path.unlink(missing_ok=True)
    encrypted_path(path).unlink(missing_ok=True)


def _associated_data(asset_id: bytes, chunk_index: int) -> bytes:
    return _MAGIC + asset_id + struct.pack(">Q", chunk_index)


def _write_encrypted(source, destination: Path, master_key: bytes) -> None:
    asset_id = os.urandom(16)
    data_key = os.urandom(32)
    wrap_nonce = os.urandom(12)
    wrapped_key = AESGCM(master_key).encrypt(
        wrap_nonce, data_key, _MAGIC + asset_id
    )
    header = (
        _MAGIC + asset_id + struct.pack(">I", _CHUNK_SIZE)
        + wrap_nonce + wrapped_key
    )
    tmp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        digest_before = hashlib.sha256()
        with tmp.open("xb") as output_file:
            output_file.write(header)
            chunk_index = 0
            while chunk := source.read(_CHUNK_SIZE):
                digest_before.update(chunk)
                nonce = os.urandom(12)
                encrypted = AESGCM(data_key).encrypt(
                    nonce, chunk, _associated_data(asset_id, chunk_index)
                )
                output_file.write(struct.pack(">I", len(encrypted)))
                output_file.write(nonce)
                output_file.write(encrypted)
                chunk_index += 1
            nonce = os.urandom(12)
            final_tag = AESGCM(data_key).encrypt(
                nonce, b"", _associated_data(asset_id, chunk_index)
            )
            output_file.write(struct.pack(">I", len(final_tag)))
            output_file.write(nonce)
            output_file.write(final_tag)
            output_file.flush()
            os.fsync(output_file.fileno())
        digest_after = hashlib.sha256()
        with tmp.open("rb") as encrypted_file:
            _decrypt_stream(encrypted_file, _DiscardWriter(digest_after), master_key)
        if digest_before.digest() != digest_after.digest():
            raise DesktopVaultError("Encrypted file verification failed")
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)


class _DiscardWriter:
    def __init__(self, digest):
        self.digest = digest

    def write(self, data: bytes) -> int:
        self.digest.update(data)
        return len(data)


def _read_exact(file, count: int) -> bytes:
    data = file.read(count)
    if len(data) != count:
        raise DesktopVaultError("Encrypted file is truncated")
    return data


def _iter_decrypted(file, master_key: bytes):
    header = _read_exact(file, _HEADER_SIZE)
    if not header.startswith(_MAGIC):
        if header.startswith(b"FBVAULT1"):
            raise DesktopVaultError("Unsupported pre-release vault format; restore data from a backup")
        raise DesktopVaultError("Unrecognized encrypted file format")
    cursor = len(_MAGIC)
    asset_id = header[cursor:cursor + 16]
    cursor += 16
    chunk_size = struct.unpack(">I", header[cursor:cursor + 4])[0]
    cursor += 4
    if chunk_size != _CHUNK_SIZE:
        raise DesktopVaultError("Unsupported encrypted file chunk size")
    wrap_nonce = header[cursor:cursor + 12]
    cursor += 12
    wrapped_key = header[cursor:]
    try:
        data_key = AESGCM(master_key).decrypt(
            wrap_nonce, wrapped_key, _MAGIC + asset_id
        )
    except InvalidTag as error:
        raise DesktopVaultError("Could not authenticate encrypted file key") from error
    chunk_index = 0
    while True:
        length_bytes = file.read(4)
        if not length_bytes:
            raise DesktopVaultError("Encrypted file is missing its authenticated end marker")
        if len(length_bytes) != 4:
            raise DesktopVaultError("Encrypted file has an incomplete chunk header")
        encrypted_length = struct.unpack(">I", length_bytes)[0]
        if encrypted_length < 16 or encrypted_length > _MAX_CIPHERTEXT_CHUNK:
            raise DesktopVaultError("Encrypted file has an invalid chunk length")
        nonce = _read_exact(file, 12)
        encrypted = _read_exact(file, encrypted_length)
        try:
            plain = AESGCM(data_key).decrypt(
                nonce, encrypted, _associated_data(asset_id, chunk_index)
            )
        except InvalidTag as error:
            raise DesktopVaultError(
                f"Encrypted file chunk {chunk_index} failed authentication"
            ) from error
        if not plain:
            if file.read(1):
                raise DesktopVaultError("Encrypted file has data after its end marker")
            return
        yield plain
        chunk_index += 1


def _decrypt_stream(file, output, master_key: bytes) -> None:
    for chunk in _iter_decrypted(file, master_key):
        output.write(chunk)


def verified_download_snapshot(path: Path):
    """Authenticate an encrypted snapshot before returning a streaming response."""
    encrypted = encrypted_path(path)
    root, key = _require_initialized()
    if not encrypted.is_file() or encrypted.is_symlink() or not encrypted.resolve().is_relative_to(root):
        raise DesktopVaultError("Invalid encrypted download source")
    scratch, lock = create_scratch()
    try:
        snapshot = scratch / "download.fbenc"
        shutil.copyfile(encrypted, snapshot)
        stream = snapshot.open("rb")
        try:
            length = sum(len(chunk) for chunk in _iter_decrypted(stream, key))
            stream.seek(0)
        except BaseException:
            stream.close()
            raise
        return stream, key, length, scratch, lock
    except BaseException:
        release_scratch(scratch, lock)
        raise


def seal_file(path: Path, destination: Path | None = None, remove_source: bool = True) -> Path:
    """Encrypt a file atomically and optionally remove its plaintext source."""
    root, key = _require_initialized()
    path = Path(path)
    destination = encrypted_path(destination or path)
    try:
        destination.parent.resolve().relative_to(root)
    except ValueError as error:
        raise DesktopVaultError("Encrypted files must remain inside app data") from error
    if path.is_symlink() or destination.is_symlink():
        raise DesktopVaultError("Refusing to encrypt a redirected file")
    with _lock:
        with path.open("rb") as source:
            _write_encrypted(source, destination, key)
        if remove_source and path.resolve() != destination.resolve():
            path.unlink()
    return destination


def _decrypt_to(path: Path, destination: Path) -> Path:
    _, key = _require_initialized()
    temp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with Path(path).open("rb") as encrypted_file, temp.open("xb") as plain_file:
            _decrypt_stream(encrypted_file, plain_file, key)
            plain_file.flush()
            os.fsync(plain_file.fileno())
        os.replace(temp, destination)
        return destination
    finally:
        temp.unlink(missing_ok=True)


def materialize_to(path: Path, destination: Path) -> Path:
    """Decrypt an encrypted asset to a caller-managed temporary destination."""
    path = Path(path)
    if path.is_file():
        shutil.copyfile(path, destination)
        return destination
    encrypted = encrypted_path(path)
    if not encrypted.is_file():
        raise FileNotFoundError(path)
    root, _ = _require_initialized()
    try:
        encrypted.resolve().relative_to(root)
    except ValueError as error:
        raise DesktopVaultError("Encrypted source is outside app data") from error
    return _decrypt_to(encrypted, Path(destination))


def copy_plaintext_to(path: Path, output) -> None:
    """Stream an asset to a caller-provided writer without a plaintext file."""
    path = Path(path)
    encrypted = encrypted_path(path)
    if encrypted.is_file():
        root, key = _require_initialized()
        if not encrypted.resolve().is_relative_to(root):
            raise DesktopVaultError("Encrypted source is outside app data")
        with encrypted.open("rb") as source:
            _decrypt_stream(source, output, key)
    else:
        with path.open("rb") as source:
            shutil.copyfileobj(source, output, _CHUNK_SIZE)


def materialize(path: Path) -> Path:
    """Return a short-lived plaintext file for existing path-based processors."""
    path = Path(path)
    if path.is_file():
        return path
    encrypted = encrypted_path(path)
    if not encrypted.is_file():
        return path
    root, _ = _require_initialized()
    workspace = _workspace.get()
    if workspace is None:
        raise DesktopVaultError("Encrypted data can only be opened within a request workspace")
    resolved = encrypted.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise DesktopVaultError("Encrypted source is outside app data") from error
    destination = workspace / uuid.uuid4().hex / path.name
    destination.parent.mkdir(parents=True, exist_ok=False)
    return _decrypt_to(encrypted, destination)


@contextlib.contextmanager
def _scratch_gate():
    import msvcrt

    root, _ = _require_initialized()
    temp_root = root.parent / "temporary"
    if temp_root.is_symlink():
        raise DesktopVaultError("Temporary directory is redirected")
    temp_root.mkdir(parents=True, exist_ok=True)
    gate = temp_root / ".fb-scratch-gate"
    if gate.is_symlink():
        raise DesktopVaultError("Temporary directory lock is redirected")
    with _scratch_lock, gate.open("a+b") as lock_file:
        lock_file.seek(0)
        if not lock_file.read(1):
            lock_file.write(b"\0")
            lock_file.flush()
        deadline = time.monotonic() + 30
        while True:
            try:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise DesktopVaultError("Timed out locking temporary storage")
                time.sleep(0.05)
        try:
            yield temp_root
        finally:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def _create_private_scratch(temp_root: Path) -> Path:
    from ctypes import wintypes

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.DWORD),
            ("descriptor", ctypes.c_void_p),
            ("inherit_handle", wintypes.BOOL),
        ]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(SecurityAttributes)]
    kernel.CreateDirectoryW.restype = wintypes.BOOL
    kernel.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumePathNameW.restype = wintypes.BOOL
    kernel.GetVolumeInformationW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD,
    ]
    kernel.GetVolumeInformationW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    volume = ctypes.create_unicode_buffer(32768)
    flags = wintypes.DWORD()
    if not kernel.GetVolumePathNameW(str(temp_root.resolve()), volume, len(volume)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.GetVolumeInformationW(volume.value, None, 0, None, None, ctypes.byref(flags), None, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    if not flags.value & 0x00000008:
        raise DesktopVaultError("Temporary storage does not support Windows access controls")
    descriptor = ctypes.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        "D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)", 1, ctypes.byref(descriptor), None
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        for _ in range(10):
            folder = temp_root / f"fb-v2-{uuid.uuid4().hex}"
            if kernel.CreateDirectoryW(str(folder), ctypes.byref(attributes)):
                return folder
            error = ctypes.get_last_error()
            if error != 183:
                raise ctypes.WinError(error)
        raise DesktopVaultError("Could not create a unique temporary workspace")
    finally:
        kernel.LocalFree(descriptor)


def create_scratch():
    import msvcrt

    with _scratch_gate() as temp_root:
        folder = _create_private_scratch(temp_root)
        try:
            lock_file = (folder / ".lock").open("x+b")
            try:
                lock_file.write(b"\0")
                lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except BaseException:
                lock_file.close()
                raise
        except BaseException:
            shutil.rmtree(folder)
            raise
        return folder, lock_file


def _remove_scratch(folder: Path, lock_file) -> None:
    import msvcrt

    for path in folder.rglob("*"):
        if path.is_symlink():
            raise DesktopVaultError("Temporary workspace contains a redirected path")
    for path in folder.iterdir():
        if path.name == ".lock":
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    lock_file.seek(0)
    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    lock_file.close()
    (folder / ".lock").unlink()
    folder.rmdir()


def release_scratch(folder: Path, lock_file) -> None:
    try:
        with _scratch_gate() as temp_root:
            if folder.parent != temp_root or not folder.name.startswith("fb-v2-") or folder.is_symlink():
                raise DesktopVaultError("Invalid temporary workspace")
            _remove_scratch(folder, lock_file)
    except (OSError, DesktopVaultError):
        logger.exception("Could not remove a desktop temporary workspace; retry cleanup on startup")
        if not lock_file.closed:
            try:
                lock_file.close()
            except OSError:
                logger.exception("Could not release the temporary workspace lock")
        raise


def cleanup_abandoned_scratch() -> int:
    import msvcrt

    removed = 0
    with _scratch_gate() as temp_root:
        for folder in temp_root.iterdir():
            if not folder.name.startswith("fb-v2-"):
                continue
            if folder.is_symlink() or not folder.is_dir():
                raise DesktopVaultError("Temporary workspace is redirected")
            marker = folder / ".lock"
            if marker.is_symlink() or not marker.is_file():
                raise DesktopVaultError("Temporary workspace lock is missing or redirected")
            lock_file = marker.open("r+b")
            try:
                lock_file.seek(0)
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    continue
                _remove_scratch(folder, lock_file)
                removed += 1
            finally:
                if not lock_file.closed:
                    lock_file.close()
    return removed


@contextlib.contextmanager
def request_workspace():
    """Create and remove request-scoped plaintext staging storage."""
    workspace, lock_file = create_scratch()
    token = _workspace.set(workspace)
    try:
        yield workspace
    finally:
        _workspace.reset(token)
        release_scratch(workspace, lock_file)


def migrate_plaintext_file(path: Path, destination: Path | None = None) -> None:
    root, key = _require_initialized()
    path = Path(path)
    encrypted = encrypted_path(destination or path)
    if path.is_symlink() or encrypted.is_symlink():
        raise DesktopVaultError("Refusing to migrate a redirected data file")
    if not encrypted.parent.resolve().is_relative_to(root):
        raise DesktopVaultError("Migration destination must remain inside app data")
    with _lock:
        if encrypted.exists():
            plain_digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
                    plain_digest.update(chunk)
            encrypted_digest = hashlib.sha256()
            with encrypted.open("rb") as source:
                _decrypt_stream(source, _DiscardWriter(encrypted_digest), key)
            if plain_digest.digest() != encrypted_digest.digest():
                raise DesktopVaultError(f"Conflicting plaintext and encrypted copies exist for {path.name}")
            path.unlink()
        else:
            seal_file(path, encrypted)


def migrate_plaintext_files(roots: tuple[Path, ...]) -> int:
    """Encrypt existing regular files under selected app-data roots."""
    root, _ = _require_initialized()
    migrated = 0
    with _lock:
        for directory in roots:
            directory = Path(directory)
            try:
                directory.resolve().relative_to(root)
            except ValueError as error:
                raise DesktopVaultError("Migration paths must remain inside app data") from error
            if directory.is_symlink():
                raise DesktopVaultError("Refusing to migrate a redirected data directory")
            if not directory.exists():
                continue
            for path in directory.rglob("*"):
                if path.is_symlink():
                    raise DesktopVaultError("Refusing to migrate a redirected data file")
                if not path.is_file() or path.name.endswith(".fbenc"):
                    continue
                migrate_plaintext_file(path)
                migrated += 1
    return migrated
