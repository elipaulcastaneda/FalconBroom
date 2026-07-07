RELEASE CHECKLIST — FalconBroom (Limited partner testing)

Purpose: short list of steps to prepare a repo snapshot for sharing with design partners / first clients.

1) Verify secrets & env
- Ensure `.env` is NOT committed. Use `.env.sample` as a template.
- To create a local env, copy:

  cp .env.sample .env
  # or on Windows PowerShell:
  Copy-Item .env.sample .env

- Fill required values: `JWT_SECRET`, `DATA_ENC_KEY`, `SESSION_ENCRYPTION_KEY`, `SMTP_*`, `APP_URL`.

2) Sanitize sample data
- All demo/test datasets should live under `samples/` (moved from `data/demo`).
- Verify `samples/` contains only synthetic or scrubbed data before sharing.

3) Untrack runtime files
- `data/` was removed from git index locally. Verify no sensitive files remain tracked:

  git ls-files | grep '^data/'

- If sensitive data was pushed historically, rewrite history using `git filter-repo` or BFG and rotate any exposed keys.

4) Startup & health check
- Create venv and install requirements:

  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  pip install -r requirements.txt

- Start backend (Windows dev runner recommended):

  .\.venv\Scripts\python.exe scripts\run_uvicorn_single.py

- Health check:

  curl http://127.0.0.1:3009/health

5) Invite/email flow
- Set `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, and `FROM_EMAIL` in `.env` for real delivery.
- If SMTP is not configured, invites are saved to `data/emails/` (extract token from files).
- To manually retrieve an invite token from an invite file:

  cat data/invites/invite_*.json | jq -r '.token'

6) Logs & observability
- Ensure logs do not contain PII. Configure `LOG_LEVEL` in `.env`.
- Optional: set `SENTRY_DSN` for error tracking during partner tests.

7) Quick checklist before sharing a repo snapshot
- [ ] `.env` is not present in repo
- [ ] `.env.sample` is present and accurate
- [ ] `data/` runtime artifacts are untracked
- [ ] `samples/` contains scrubbed demo data
- [ ] README quickstart updated for partner testing
- [ ] Consider history rewrite if PII was ever pushed

Notes and links
- To rewrite history and remove files from remote permanently, use `git filter-repo` (recommended) or BFG. This is destructive — back up first.
- For production-like testing, see `deploy/falconbroom.service` and `deploy/INSTALL_SYSTEMD.md`.
