"""Windows user-bound protection for desktop inspection records."""

import ctypes
import json
import os
import tempfile
import threading
from pathlib import Path

_MAGIC = b"FalconBroom DPAPI inspection v1\0"
_lock = threading.RLock()


class InspectionProtectionError(RuntimeError):
    pass


def _dpapi(data: bytes, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise InspectionProtectionError("Windows user-bound protection is unavailable")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), buf)
    target = Blob()
    if decrypt:
        fn = crypt32.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        fn = crypt32.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), "FalconBroom inspection", None, None, None, 1, ctypes.byref(target))
    fn.restype = wintypes.BOOL
    if not fn(*args):
        raise InspectionProtectionError(f"Windows credential protection failed (error {ctypes.get_last_error()})")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(target.data)


def _protected_desktop() -> bool:
    return bool(os.environ.get("FALCONBROOM_DESKTOP_SECRET"))


def read_inspection(path: Path):
    with _lock:
        if path.is_symlink():
            raise InspectionProtectionError("Inspection record is redirected")
        raw = path.read_bytes()
        if raw.startswith(_MAGIC):
            raw = _dpapi(raw[len(_MAGIC):], decrypt=True)
        return json.loads(raw.decode("utf-8"))


def _write_protected(path: Path, raw: bytes):
    encrypted = _MAGIC + _dpapi(raw, decrypt=False)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".fb-inspection-", delete=False) as file:
            tmp = Path(file.name)
            file.write(encrypted)
            file.flush()
            os.fsync(file.fileno())
        written = tmp.read_bytes()
        if not written.startswith(_MAGIC) or _dpapi(written[len(_MAGIC):], decrypt=True) != raw:
            raise InspectionProtectionError("Inspection encryption verification failed")
        os.replace(tmp, path)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def write_inspection(path: Path, record):
    raw = json.dumps(record, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    with _lock:
        if _protected_desktop():
            _write_protected(path, raw)
        else:
            path.write_bytes(raw)


def migrate_inspections(root: Path) -> int:
    if not _protected_desktop():
        raise InspectionProtectionError("Inspection migration requires the desktop app")
    migrated = 0
    with _lock:
        if root.is_symlink() or root.resolve().parent != (Path.cwd() / "data").resolve():
            raise InspectionProtectionError("Inspection directory is redirected")
        for path in root.glob("*.json"):
            if path.is_symlink() or not path.is_file():
                raise InspectionProtectionError("Inspection directory contains a redirected record")
            raw = path.read_bytes()
            if raw.startswith(_MAGIC):
                read_inspection(path)
                continue
            json.loads(raw.decode("utf-8"))
            _write_protected(path, raw)
            migrated += 1
    return migrated
