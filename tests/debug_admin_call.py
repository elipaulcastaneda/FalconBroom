import sys, os
parent = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, parent)
from fastapi.testclient import TestClient
import fbroom.main as main

# monkeypatch _require_admin to bypass auth
main._require_admin = lambda req: {'id':'admin','is_admin':True}
main._consent_audit = lambda *a, **k: None
async def fake_call(path, method='GET', json_body=None):
	class R:
		status_code = 200
		def json(self):
			return [{'id':'u1','email':'a@b.com','aud':'authenticated','created_at':'t'}]
	return R()
main._call_supabase_service_role = fake_call

client = TestClient(main.app)
resp = client.post('/admin/supabase/users')
print('STATUS', resp.status_code)
print('TEXT', resp.text)
print('JSON', None if resp.text=='' else resp.json())
