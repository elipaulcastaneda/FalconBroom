from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import desktop_vault


@dataclass
class ResolvedSource:
    path: str
    exists: bool
    kind: str
    name: str
    materialized_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "kind": self.kind,
            "name": self.name,
            "materialized_path": (
                None if desktop_vault.is_initialized() else self.materialized_path
            ),
        }


def resolve_source(path: str) -> ResolvedSource:
    """Lightweight resolver for a source path.

    Returns a small object with a `to_dict()` method so the API can safely
    return basic information about the requested source without pulling in
    heavier dependencies.
    """
    p = Path(path)
    materialized = desktop_vault.materialize(p) if desktop_vault.is_initialized() else p
    exists = materialized.exists()
    suffix = p.suffix.lower()
    kind = suffix.lstrip('.') if suffix else 'file'
    mat = str(materialized.resolve()) if exists else None
    return ResolvedSource(str(p), exists, kind, p.name, materialized_path=mat)
