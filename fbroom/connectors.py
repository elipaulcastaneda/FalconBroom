from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
            "materialized_path": self.materialized_path,
        }


def resolve_source(path: str) -> ResolvedSource:
    """Lightweight resolver for a source path.

    Returns a small object with a `to_dict()` method so the API can safely
    return basic information about the requested source without pulling in
    heavier dependencies.
    """
    p = Path(path)
    exists = p.exists()
    suffix = p.suffix.lower()
    kind = suffix.lstrip('.') if suffix else 'file'
    # materialized_path is used by the engine to locate a readable CSV/Parquet.
    # If the provided path exists, use it; otherwise leave None so callers
    # can handle resolution/fallback externally.
    mat = str(p.resolve()) if exists else None
    return ResolvedSource(str(p), exists, kind, p.name, materialized_path=mat)
