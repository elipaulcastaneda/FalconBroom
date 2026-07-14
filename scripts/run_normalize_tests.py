import pytest
import sys
rc = pytest.main(['-q', 'tests/test_normalize_behaviors.py'])
print('pytest rc=', rc)
sys.exit(rc)
