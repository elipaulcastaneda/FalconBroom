import os
from pathlib import Path
import importlib

import pytest

fb = importlib.import_module('fbroom.main')

try:
    from cryptography.fernet import Fernet
except Exception:
    Fernet = None


def test_save_and_load_sensitive_json_encrypted(tmp_path, monkeypatch):
    if Fernet is None:
        pytest.skip('cryptography not installed')
    key = Fernet.generate_key()
    monkeypatch.setenv('DATA_ENC_KEY', key.decode())
    p = tmp_path / 'secret.json'
    obj = {'hello': 'world', 'n': 1}
    saved = fb.save_sensitive_json(p, obj)
    assert saved.exists()
    # ensure file has .enc when encryption enabled
    assert str(saved).endswith('.enc')
    loaded = fb.load_sensitive_json(saved)
    assert loaded == obj
def test_save_and_load_sensitive_json_plain(tmp_path, monkeypatch):
    monkeypatch.delenv('DATA_ENC_KEY', raising=False)
    p = tmp_path / 'plain.json'
    obj = {'a': 2}
    saved = fb.save_sensitive_json(p, obj)
    assert saved.exists()
    # when not encrypted, saved path should be the original
    assert str(saved).endswith('plain.json')
    loaded = fb.load_sensitive_json(saved)
    assert loaded == obj
