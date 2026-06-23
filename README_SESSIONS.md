SESSION_ENCRYPTION_KEY and deploying encrypted session files

This project supports optional encryption of session files under `data/sessions/`.
When `SESSION_ENCRYPTION_KEY` is provided, the server will attempt to use `cryptography.fernet.Fernet` to encrypt/decrypt session JSON files.

Generating a key (recommended):

```bash
python - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
```

Or using the repository `Makefile`:

```bash
make gen-session-key
```

This prints a base64 urlsafe key such as:

- `b"0G6..."` (without the b"" — copy the inner string)

Set this value as the `SESSION_ENCRYPTION_KEY` environment variable in your deployment environment. Example for systemd unit or Docker env:

- systemd service file (Environment=SESSION_ENCRYPTION_KEY=...)
- Dockerfile / compose: set `SESSION_ENCRYPTION_KEY` in the environment; prefer using a secret store in production.

Production notes and best practices
- Use a secrets manager (HashiCorp Vault, AWS SSM/Secrets Manager, Azure Key Vault) rather than embedding the key in images or plaintext in config files.
- Rotate the key periodically and plan for re-encrypting session files when rotating (backup current session store, decrypt with old key, re-encrypt with new key).
- Ensure the deployment sets `ENV=production` so that cookies are set with `Secure` flag.
- Limit access to `data/` directory via filesystem ACLs and run the app under a dedicated service account.
- Monitor for refresh-token reuse (rotated tokens) and revoke sessions on suspected compromise.

Quick test (local)

1. Install dependencies (in your venv):

```bash
python -m pip install -r requirements.txt
```

2. Generate a key and export it for local dev (bash example):

```bash
export SESSION_ENCRYPTION_KEY="<paste-key-here>"
export ENV=development
python -m uvicorn fbroom.main:app --reload --port 3009
```

3. Confirm session files under `data/sessions/` are encrypted (binary content) when `SESSION_ENCRYPTION_KEY` is set.
