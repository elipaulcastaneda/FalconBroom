"""Coordinate desktop HTTP requests with destructive local cleanup."""
import asyncio
import contextlib
import ctypes
import logging
import os
import time
from pathlib import Path

from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

_CLEANUP_PATHS = {"/account/delete", "/dsar/delete", "/desktop/clear-workspace", "/desktop/test-deletions", "/desktop/remote-services", "/desktop/protect-consents", "/desktop/protect-consent-jobs", "/desktop/protect-audit"}
_DELETION_ALLOWED_PATHS = {"/health", "/account/delete", "/desktop/test-deletions"}


def _gate_path():
    policy_path = os.environ.get("FALCONBROOM_REMOTE_POLICY_FILE")
    root = Path(policy_path).parent if policy_path else Path("data")
    return root / ".desktop-request-gate"


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("internal", ctypes.c_size_t),
        ("internal_high", ctypes.c_size_t),
        ("offset", ctypes.c_uint32),
        ("offset_high", ctypes.c_uint32),
        ("event", ctypes.c_void_p),
    ]


class _WindowsFileGate:
    def __init__(self, path: Path):
        import msvcrt

        if path.is_symlink() or path.parent.is_symlink():
            raise OSError("Desktop request gate is redirected")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.LockFileEx.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(_Overlapped),
        ]
        self._kernel.LockFileEx.restype = ctypes.c_int
        self._kernel.UnlockFileEx.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_uint32, ctypes.POINTER(_Overlapped),
        ]
        self._kernel.UnlockFileEx.restype = ctypes.c_int
        self._file = path.open("a+b")
        self._handle = msvcrt.get_osfhandle(self._file.fileno())
        self._held = []

    async def lock(self, offset: int, exclusive: bool, deadline: float):
        flags = 1 | (2 if exclusive else 0)
        while True:
            overlapped = _Overlapped(offset=offset)
            if self._kernel.LockFileEx(self._handle, flags, 0, 1, 0, ctypes.byref(overlapped)):
                self._held.append(offset)
                return
            error = ctypes.get_last_error()
            if error != 33:
                raise ctypes.WinError(error)
            if time.monotonic() >= deadline:
                raise TimeoutError("Desktop cleanup is waiting for another operation")
            await asyncio.sleep(0.05)

    def lock_sync(self, offset: int, exclusive: bool, deadline: float):
        flags = 1 | (2 if exclusive else 0)
        while True:
            overlapped = _Overlapped(offset=offset)
            if self._kernel.LockFileEx(self._handle, flags, 0, 1, 0, ctypes.byref(overlapped)):
                self._held.append(offset)
                return
            error = ctypes.get_last_error()
            if error != 33:
                raise ctypes.WinError(error)
            if time.monotonic() >= deadline:
                raise TimeoutError("Desktop operation is waiting for another operation")
            time.sleep(0.05)

    def unlock(self, offset: int):
        overlapped = _Overlapped(offset=offset)
        if not self._kernel.UnlockFileEx(self._handle, 0, 1, 0, ctypes.byref(overlapped)):
            raise ctypes.WinError(ctypes.get_last_error())
        self._held.remove(offset)

    def close(self):
        try:
            for offset in reversed(self._held[:]):
                self.unlock(offset)
        finally:
            self._file.close()


async def _enter_gate(gate: _WindowsFileGate, exclusive: bool, timeout: float):
    deadline = time.monotonic() + timeout
    await gate.lock(0, exclusive, deadline)
    await gate.lock(1, exclusive, deadline)
    if not exclusive:
        gate.unlock(0)


@contextlib.contextmanager
def desktop_startup_gate():
    gate = _WindowsFileGate(_gate_path())
    try:
        asyncio.run(_enter_gate(gate, False, 30))
        yield
    finally:
        gate.close()


class DesktopRequestGate:
    def __init__(self, app, timeout: float = 30):
        self.app = app
        self.timeout = timeout

    async def __call__(self, scope, receive, send):
        desktop = (
            os.name == "nt"
            and os.environ.get("FALCONBROOM_DESKTOP_SECRET")
            and os.environ.get("FALCONBROOM_LOCAL_MODE", "").strip().lower() in {"1", "true", "yes"}
        )
        if scope["type"] != "http" or not desktop:
            await self.app(scope, receive, send)
            return

        exclusive = scope["method"] == "POST" and (
            scope["path"] in _CLEANUP_PATHS
            or (
                scope["path"] == "/join-export"
                and os.environ.get("FALCONBROOM_TEST_ACCOUNT_DELETION") == "1"
            )
        )
        gate = None
        try:
            gate = _WindowsFileGate(_gate_path())
            await _enter_gate(gate, exclusive, self.timeout)
        except TimeoutError:
            if gate:
                gate.close()
            await JSONResponse(
                {"detail": "Another desktop operation is still running; please retry."},
                status_code=423,
            )(scope, receive, send)
            return
        except OSError:
            if gate:
                gate.close()
            logger.exception("Desktop request gate failed")
            await JSONResponse(
                {"detail": "Unable to coordinate desktop data access."}, status_code=503
            )(scope, receive, send)
            return
        except BaseException:
            if gate:
                gate.close()
            raise

        try:
            if (
                scope["path"] not in _DELETION_ALLOWED_PATHS
                and not (scope["path"] == "/me" and scope["method"] in {"GET", "OPTIONS"})
                and (Path("data") / ".test-deletion-pending").exists()
            ):
                await JSONResponse(
                    {"detail": "A test account deletion is pending. Check its status or retry cleanup."},
                    status_code=423,
                )(scope, receive, send)
                return
            await self.app(scope, receive, send)
        finally:
            gate.close()
