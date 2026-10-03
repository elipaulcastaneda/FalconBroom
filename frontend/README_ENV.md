# Frontend environment file (`.env`)

This file contains local environment variables used by the frontend during development. It is intentionally not tracked by git.

Quick steps:

- Copy `frontend/.env.example` to `frontend/.env` (or edit `frontend/.env` directly).
- Fill in `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` with values from your Supabase project.
- Restart the Vite dev server / Tauri dev process so the variables are picked up.

Security recommendations:

- Do not commit `frontend/.env` to source control.
- Restrict file permissions locally (on Unix/macOS): `chmod 600 frontend/.env`.
- Treat the `ANON_KEY` like a credential: avoid sharing it in screenshots or logs.
- For CI or production builds, provide secrets via secure CI variables or the Tauri build pipeline rather than committing them.
