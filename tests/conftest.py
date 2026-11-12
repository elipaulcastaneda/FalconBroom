import sys
import os
import pytest
# Ensure repository root is on sys.path so tests can import fbroom package
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def desktop_mode_for_existing_tests(monkeypatch):
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
