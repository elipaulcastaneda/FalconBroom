from __future__ import annotations

import csv
import os
import shutil
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

try:
    import boto3
except Exception:  # pragma: no cover - optional dependency
    boto3 = None


@dataclass(frozen=True)
class ResolvedSource:
    original: str
    connector: str
    source_kind: str
    materialized_path: str
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["notes"] = list(self.notes)
        return data


def _cache_root() -> Path:
    root = Path(os.environ.get("FALCONBROOM_CACHE_DIR", "data/cache"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_stem(value: str) -> str:
    stem = Path(value).stem.strip() or "source"
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in stem)
    return cleaned or "source"


def _new_cache_path(prefix: str, name: str, suffix: str) -> Path:
    from uuid import uuid4

    cache_dir = _cache_root() / prefix
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{_safe_stem(name)}_{uuid4().hex[:8]}{suffix}"


def _download_http_source(uri: str) -> ResolvedSource:
    parsed = urlparse(uri)
    target = _new_cache_path("http", Path(parsed.path).name or "download", Path(parsed.path).suffix or ".bin")

    import urllib.request

    with urllib.request.urlopen(uri) as response, target.open("wb") as handle:
        shutil.copyfileobj(response, handle)

    return ResolvedSource(
        original=uri,
        connector=parsed.scheme.lower(),
        source_kind=Path(target).suffix.lstrip(".") or "binary",
        materialized_path=str(target),
        notes=("downloaded",),
    )


def _download_s3_source(uri: str) -> ResolvedSource:
    if boto3 is None:
        raise RuntimeError("S3 support requires boto3. Install dependencies from requirements.txt.")

    parsed = urlparse(uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        raise ValueError(f"Invalid S3 URI: {uri}")

    suffix = Path(key).suffix or ".bin"
    target = _new_cache_path("s3", key, suffix)
    boto3.client("s3").download_file(bucket, key, str(target))
    return ResolvedSource(
        original=uri,
        connector="s3",
        source_kind=Path(target).suffix.lstrip(".") or "binary",
        materialized_path=str(target),
        notes=(f"s3://{bucket}/{key}", "downloaded"),
    )


def _query_param(parsed, *names: str) -> str:
    params = parse_qs(parsed.query)
    for name in names:
        values = params.get(name)
        if values and values[0].strip():
            return values[0].strip()
    return ""


def _write_sqlite_query_to_csv(database_path: Path, query: str, target: Path) -> None:
    connection = sqlite3.connect(str(database_path))
    try:
        cursor = connection.execute(query)
        headers = [column[0] for column in (cursor.description or [])]
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if headers:
                writer.writerow(headers)
            for row in cursor:
                writer.writerow(list(row))
    finally:
        connection.close()


def _write_duckdb_query_to_csv(database_path: Path, query: str, target: Path) -> None:
    try:
        import duckdb
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("DuckDB support requires duckdb to be installed.") from exc

    connection = duckdb.connect(str(database_path))
    try:
        relation = connection.sql(query)
        relation.write_csv(str(target))
    finally:
        connection.close()


def _resolve_warehouse_uri(uri: str) -> ResolvedSource:
    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()
    if scheme not in {"sqlite", "duckdb"}:
        raise ValueError(f"Unsupported warehouse scheme: {scheme}")

    raw_path = unquote(parsed.path or "")
    database_path = Path(raw_path.lstrip("/")) if raw_path else None
    if database_path is None or not str(database_path):
        raise ValueError(f"Warehouse URI must include a database path: {uri}")
    if not database_path.exists():
        raise FileNotFoundError(f"Warehouse database not found: {database_path}")

    query = _query_param(parsed, "query", "sql")
    table = _query_param(parsed, "table")
    if not query and table:
        query = f"SELECT * FROM {table}"
    if not query:
        raise ValueError("Warehouse URIs must include a query= or table= parameter.")

    target = _new_cache_path("warehouse", database_path.name, ".csv")
    if scheme == "sqlite":
        _write_sqlite_query_to_csv(database_path, query, target)
    else:
        _write_duckdb_query_to_csv(database_path, query, target)

    return ResolvedSource(
        original=uri,
        connector=scheme,
        source_kind="csv",
        materialized_path=str(target),
        notes=(f"query={query}", "materialized_to_csv"),
    )


def _path_from_file_uri(parsed) -> Path:
    raw_path = unquote(parsed.path or "")
    if parsed.netloc:
        return Path(f"{parsed.netloc}{raw_path}")
    if raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
        return Path(raw_path.lstrip("/"))
    return Path(raw_path)


def resolve_source(source: str | Path) -> ResolvedSource:
    raw_value = str(source)
    if isinstance(source, Path):
        if not source.exists():
            raise FileNotFoundError(f"Source not found: {source}")
        return ResolvedSource(
            original=raw_value,
            connector="local",
            source_kind=source.suffix.lstrip(".") or "file",
            materialized_path=str(source),
        )

    parsed = urlparse(raw_value)
    scheme = parsed.scheme.lower()

    if not scheme:
        path = Path(raw_value)
        if not path.exists():
            raise FileNotFoundError(f"Source not found: {raw_value}")
        return ResolvedSource(
            original=raw_value,
            connector="local",
            source_kind=path.suffix.lstrip(".") or "file",
            materialized_path=str(path),
        )

    if scheme == "file":
        path = _path_from_file_uri(parsed)
        if not path.exists():
            raise FileNotFoundError(f"Source not found: {path}")
        return ResolvedSource(
            original=raw_value,
            connector="file",
            source_kind=path.suffix.lstrip(".") or "file",
            materialized_path=str(path),
        )

    if scheme == "s3":
        return _download_s3_source(raw_value)

    if scheme in {"sqlite", "duckdb"}:
        return _resolve_warehouse_uri(raw_value)

    if scheme in {"http", "https"}:
        return _download_http_source(raw_value)

    raise ValueError(f"Unsupported source scheme: {scheme}")
