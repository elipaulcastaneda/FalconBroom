"""Desktop preferences for optional outbound services."""
import contextlib
import json
import os
import tempfile
import time
from pathlib import Path

from .desktop_request_gate import _WindowsFileGate

SERVICES = (
    "remote_backend",
    "google_drive",
    "geocoding",
    "exchangerate_host",
    "openexchangerates",
    "http_lookup",
    "audit_export",
    "smtp",
)


class RemoteServiceError(RuntimeError):
    pass


class RemoteServiceDisabled(RemoteServiceError):
    pass


class RemoteServiceConflict(RemoteServiceError):
    pass


def desktop_mode():
    return bool(os.environ.get("FALCONBROOM_DESKTOP_SECRET")) and (
        os.environ.get("FALCONBROOM_LOCAL_MODE", "").strip().lower() in {"1", "true", "yes"}
    )


def policy_path():
    configured = os.environ.get("FALCONBROOM_REMOTE_POLICY_FILE")
    return Path(configured) if configured else Path("data") / "remote-services.json"


def read_policy():
    path = policy_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {key: True for key in SERVICES}
    policy = json.loads(text)
    if not isinstance(policy, dict) or set(policy) != set(SERVICES) or any(
        type(policy[key]) is not bool for key in SERVICES
    ):
        raise ValueError("Invalid desktop remote-service preferences")
    return policy


@contextlib.contextmanager
def _policy_lock(exclusive):
    try:
        gate = _WindowsFileGate(policy_path().parent / ".remote-services-gate")
    except OSError as error:
        raise RemoteServiceError("Could not coordinate remote-service access") from error
    try:
        try:
            deadline = time.monotonic() + 30
            gate.lock_sync(0, exclusive, deadline)
            gate.lock_sync(1, exclusive, deadline)
            if not exclusive:
                gate.unlock(0)
        except (OSError, TimeoutError) as error:
            raise RemoteServiceError("Could not coordinate remote-service access") from error
        yield
    finally:
        gate.close()


def write_policy(policy, expected=None):
    if not isinstance(policy, dict) or set(policy) != set(SERVICES) or any(
        type(policy[key]) is not bool for key in SERVICES
    ):
        raise ValueError("Invalid desktop remote-service preferences")
    with _policy_lock(True):
        if expected is not None and read_policy() != expected:
            raise RemoteServiceConflict("Remote-service preferences changed elsewhere; reload before saving.")
        path = policy_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.parent.is_symlink():
            raise OSError("Remote-service preferences are redirected")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(policy, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


def require(service):
    if service not in SERVICES:
        raise ValueError("Unknown remote service")
    if desktop_mode():
        try:
            enabled = read_policy()[service]
        except (OSError, ValueError) as error:
            raise RemoteServiceError("Could not read remote-service preferences") from error
        if not enabled:
            raise RemoteServiceDisabled(f"Remote service '{service}' is disabled in Settings.")


@contextlib.contextmanager
def network_access(service):
    if not desktop_mode():
        yield
        return
    with _policy_lock(False):
        require(service)
        yield
