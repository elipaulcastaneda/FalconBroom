import pytest
from fastapi.testclient import TestClient
import fbroom.main as main


@pytest.fixture(autouse=True)
def patch_admin(monkeypatch):
    # stub auth and auditing to avoid filesystem writes
    monkeypatch.setattr(main, '_require_admin', lambda req: {'id': 'admin', 'is_admin': True})
    monkeypatch.setattr(main, '_consent_audit', lambda *a, **k: None)
    # provide a fake Redis client for rate limiting
    class FakeRedis:
        def __init__(self):
            self.store = {}

        def incr(self, key):
            v = int(self.store.get(key, 0)) + 1
            self.store[key] = v
            return v

        def expire(self, key, seconds):
            # no-op for tests
            return True

    fake = FakeRedis()
    monkeypatch.setattr(main, '_get_redis_client', lambda: fake)
    yield


def make_fake_response(status_code=200, payload=None):
    class R:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    return R(status_code, payload)


def test_list_supabase_users_success(monkeypatch):
    async def fake_call(path, method='GET', json_body=None):
        return make_fake_response(200, [{'id': 'u1', 'email': 'a@b.com', 'aud': 'authenticated', 'created_at': 't'}])

    monkeypatch.setattr(main, '_call_supabase_service_role', fake_call)
    client = TestClient(main.app)
    resp = client.post('/admin/supabase/users')
    assert resp.status_code == 200
    body = resp.json()
    assert body.get('ok') is True
    assert isinstance(body.get('users'), list)


def test_delete_supabase_user_success(monkeypatch):
    async def fake_call(path, method='DELETE', json_body=None):
        return make_fake_response(204, None)

    monkeypatch.setattr(main, '_call_supabase_service_role', fake_call)
    client = TestClient(main.app)
    resp = client.post('/admin/supabase/user/delete', json={'user_id': 'u1'})
    assert resp.status_code == 200
    assert resp.json().get('ok') is True


def test_admin_ip_allowlist_blocks(monkeypatch):
    # allowlist contains a different IP
    monkeypatch.setenv('SUPABASE_ADMIN_IP_ALLOWLIST', '1.2.3.4')
    # ensure module-level value is read at runtime by comparing behavior via header
    client = TestClient(main.app)
    # still patch _call_supabase_service_role to avoid outgoing calls
    async def fake_call(path, method='GET', json_body=None):
        return make_fake_response(200, [])

    monkeypatch.setattr(main, '_call_supabase_service_role', fake_call)
    # request from 9.9.9.9 should be blocked
    resp = client.post('/admin/supabase/users', headers={'X-Forwarded-For': '9.9.9.9'})
    assert resp.status_code == 403


def test_admin_rate_limit(monkeypatch):
    # set low rate limit
    main.ADMIN_RATE_LIMIT_PER_MIN = 1
    # Redis-backed fake client provided by fixture; nothing to clear

    async def fake_call(path, method='GET', json_body=None):
        return make_fake_response(200, [])

    monkeypatch.setattr(main, '_call_supabase_service_role', fake_call)
    client = TestClient(main.app)
    h = {'X-Forwarded-For': '2.2.2.2'}
    r1 = client.post('/admin/supabase/users', headers=h)
    assert r1.status_code == 200
    r2 = client.post('/admin/supabase/users', headers=h)
    assert r2.status_code == 429


def test_supabase_rpc_success(monkeypatch):
    async def fake_call(path, method='POST', json_body=None):
        return make_fake_response(200, {'message': 'ok', 'input': json_body})

    monkeypatch.setattr(main, '_call_supabase_service_role', fake_call)
    client = TestClient(main.app)
    resp = client.post('/admin/supabase/rpc', json={'function': 'test_fn', 'params': {'x': 1}})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get('ok') is True
    assert body.get('result', {}).get('message') == 'ok'


def test_supabase_rpc_missing_fn(monkeypatch):
    client = TestClient(main.app)
    resp = client.post('/admin/supabase/rpc', json={'params': {'x': 1}})
    assert resp.status_code == 400
