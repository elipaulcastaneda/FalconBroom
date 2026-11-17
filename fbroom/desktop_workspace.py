"""Request-local directories for verified desktop account workspaces."""

from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from pathlib import Path
from uuid import UUID


_account = ContextVar("desktop_workspace_account", default=None)


@contextmanager
def account_workspace(project_url: str, user_id: str):
    project = sha256(project_url.rstrip("/").lower().encode("utf-8")).hexdigest()
    account = str(UUID(user_id))
    token = _account.set((project, account))
    try:
        yield
    finally:
        _account.reset(token)


def current_account():
    return _account.get()


def workspace_directory(base: Path) -> Path:
    base = Path(base)
    account = current_account()
    if account is None:
        return base
    data = Path.cwd().resolve() / "data"
    base = Path.absolute(base)
    if base.parent != data or data.is_symlink() or base.is_symlink():
        raise ValueError("Desktop workspace root is redirected")
    if data.resolve() != data or base.resolve() != base:
        raise ValueError("Desktop workspace root is redirected")
    path = base
    for component in (".accounts", *account):
        path = path / component
        if path.is_symlink() or path.resolve() != path:
            raise ValueError("Desktop account workspace is redirected")
        path.mkdir(exist_ok=True)
    return path


def snapshot_directory(history_root: Path) -> Path:
    root = Path(history_root)
    path = root / "snapshots"
    if current_account() is not None and (path.is_symlink() or path.resolve().parent != root.resolve()):
        raise ValueError("Desktop snapshot storage is redirected")
    return path


class WorkspaceDirectory:
    """Resolve a workspace root within the current request's account."""

    def __init__(self, base: Path):
        self.base = Path(base)

    def __fspath__(self):
        return str(workspace_directory(self.base))

    def __str__(self):
        return self.__fspath__()

    def __truediv__(self, child):
        return workspace_directory(self.base) / child

    def __eq__(self, other):
        return workspace_directory(self.base) == other

    def __getattr__(self, name):
        return getattr(workspace_directory(self.base), name)
