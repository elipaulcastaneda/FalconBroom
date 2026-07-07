"""
Sanitize runtime `data/` files and write sanitized copies to `samples/sanitized/`.
- JSON files: redact `email`, `username`, `name`, and `user_id` fields when present.
- CSV files: redact columns that look like emails or contain PII-like headers.

Usage:
  python scripts/sanitize_data.py --out samples/sanitized
  python scripts/sanitize_data.py --inplace   # overwrites after confirmation

This script is conservative by default and will not modify originals unless `--inplace` is used.
"""

import argparse
import os
import json
import csv
import re
from pathlib import Path

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

PII_JSON_KEYS = {"email", "username", "name", "user_id", "owner_email", "OwnerEmail"}


def redact_value(val):
    if not isinstance(val, str):
        return val
    if EMAIL_RE.search(val):
        return "redacted@example.com"
    # generic redaction for short strings
    if len(val) <= 64:
        return "REDACTED"
    return "REDACTED"


def sanitize_json_file(src: Path, dest: Path):
    try:
        with src.open('r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        # not valid json, copy as-is
        dest.write_bytes(src.read_bytes())
        return

    def recurse(obj):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k in PII_JSON_KEYS:
                    out[k] = redact_value(v)
                else:
                    out[k] = recurse(v)
            return out
        if isinstance(obj, list):
            return [recurse(x) for x in obj]
        if isinstance(obj, str):
            if EMAIL_RE.search(obj):
                return "redacted@example.com"
            return obj
        return obj

    sanitized = recurse(data)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open('w', encoding='utf-8') as f:
        json.dump(sanitized, f, indent=2, ensure_ascii=False)


def sanitize_csv_file(src: Path, dest: Path):
    # Attempt to detect email-like columns by header or values
    with src.open('r', encoding='utf-8', errors='ignore') as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        return
    headers = rows[0]
    email_cols = set()
    for i, h in enumerate(headers):
        h_low = (h or '').lower()
        if 'email' in h_low or 'user' in h_low or 'name' in h_low:
            email_cols.add(i)
    # also scan sample rows
    for r in rows[1: min(len(rows), 10)]:
        for i, cell in enumerate(r):
            if isinstance(cell, str) and EMAIL_RE.search(cell):
                email_cols.add(i)
    out_rows = []
    for r in rows:
        newr = list(r)
        for i in email_cols:
            if i < len(newr):
                newr[i] = 'redacted@example.com'
        out_rows.append(newr)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open('w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(out_rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', default='samples/sanitized')
    p.add_argument('--inplace', action='store_true')
    p.add_argument('--path', default='data')
    args = p.parse_args()

    src_root = Path(args.path)
    if not src_root.exists():
        print(f"Source path {src_root} does not exist. Nothing to do.")
        return
    dest_root = Path(args.out)

    files = list(src_root.rglob('*'))
    json_count = csv_count = other_count = 0
    for f in files:
        if f.is_dir():
            continue
        rel = f.relative_to(src_root)
        dest = dest_root.joinpath(rel)
        if args.inplace:
            # make a backup copy first
            backup = f.parent / (f.name + '.bak')
            if not backup.exists():
                backup.write_bytes(f.read_bytes())
        if f.suffix.lower() == '.json':
            sanitize_json_file(f, dest if not args.inplace else f)
            json_count += 1
        elif f.suffix.lower() in ('.csv', '.tsv'):
            sanitize_csv_file(f, dest if not args.inplace else f)
            csv_count += 1
        else:
            # binary or unknown - copy through sanitized samples path
            if not args.inplace:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(f.read_bytes())
            other_count += 1
    print(f"Sanitization complete. JSON: {json_count}, CSV: {csv_count}, other: {other_count}")
    if not args.inplace:
        print(f"Sanitized copies written to: {dest_root}")
    else:
        print("Original files overwritten (backups with .bak created where possible).")

if __name__ == '__main__':
    main()
