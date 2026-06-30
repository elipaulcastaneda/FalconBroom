#!/usr/bin/env python3
"""Re-encrypt sensitive JSON files from an old Fernet key to a new one.

Usage:
  OLD_DATA_ENC_KEY=... NEW_DATA_ENC_KEY=... python scripts/rekey.py
  or provide --old-key and --new-key

This script will walk conservative data directories and re-encrypt `.json` and `.json.enc` files.
It will skip audit.log and other non-json artifacts.
"""
from pathlib import Path
import os
import argparse
import sys
import json

try:
    from cryptography.fernet import Fernet
except Exception:
    print("cryptography not available; install cryptography to run this tool")
    sys.exit(2)


def load_fernet(key_str):
    if not key_str:
        return None
    if isinstance(key_str, bytes):
        k = key_str
    else:
        k = key_str.encode('utf-8')
    try:
        return Fernet(k)
    except Exception:
        # try raw
        try:
            return Fernet(key_str)
        except Exception:
            return None


def reencrypt_file(path: Path, old_f, new_f):
    try:
        data = path.read_bytes()
    except Exception as e:
        print(f"skip (read error): {path}: {e}")
        return False
    # try decrypt with old if available
    plain = None
    if old_f:
        try:
            plain = old_f.decrypt(data)
        except Exception:
            # maybe file is plaintext
            pass
    if plain is None:
        # attempt to decode as utf-8 plaintext
        try:
            plain = data.decode('utf-8').encode('utf-8')
        except Exception:
            print(f"skip (unable to decrypt or decode): {path}")
            return False
    # write with new fernet if provided
    if new_f:
        try:
            out = new_f.encrypt(plain)
            path.write_bytes(out)
            print(f"rewrote (encrypted): {path}")
            return True
        except Exception as e:
            print(f"failed to write encrypted for {path}: {e}")
            return False
    else:
        # write plaintext
        try:
            path.write_text(plain.decode('utf-8'), encoding='utf-8')
            print(f"rewrote (plaintext): {path}")
            return True
        except Exception as e:
            print(f"failed to write plaintext for {path}: {e}")
            return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--old-key', help='Old DATA_ENC_KEY (base64)')
    p.add_argument('--new-key', help='New DATA_ENC_KEY (base64)')
    p.add_argument('--dirs', help='Comma-separated list of data dirs to process', default='data/dsar,data/queue,data/privacy,data/audit_exports,data/users')
    args = p.parse_args()

    old_key = args.old_key or os.environ.get('OLD_DATA_ENC_KEY') or os.environ.get('DATA_ENC_KEY')
    new_key = args.new_key or os.environ.get('NEW_DATA_ENC_KEY') or os.environ.get('DATA_ENC_KEY')

    old_f = load_fernet(old_key)
    new_f = load_fernet(new_key)

    dirs = [Path(d.strip()) for d in args.dirs.split(',') if d.strip()]
    for d in dirs:
        if not d.exists():
            print(f"skip missing dir: {d}")
            continue
        for path in d.rglob('*.json'):
            # skip audit logs or large non-record files
            if path.name in ('audit.log', 'audit.prev'):
                continue
            reencrypt_file(path, old_f, new_f)
        for path in d.rglob('*.json.enc'):
            reencrypt_file(path, old_f, new_f)


if __name__ == '__main__':
    main()
