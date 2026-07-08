This document explains how to install and enable the systemd unit for FalconBroom.

1) Copy the unit file to /etc/systemd/system

```bash
sudo cp deploy/falconbroom.service /etc/systemd/system/falconbroom.service
```

2) Edit the unit to match your installation paths:
- `User`/`Group`: the account that should run the app (do NOT run as root).
- `WorkingDirectory`: directory where the repo is located (e.g. `/opt/falconbroom`).
- `ExecStart`: point to the python in your venv and the desired host/port.

Example `ExecStart` (if your venv is at `/opt/falconbroom/.venv`):

```text
ExecStart=/opt/falconbroom/.venv/bin/python -m uvicorn fbroom.main:app --host 127.0.0.1 --port 8000 --workers 2 --log-level info
```

3) (Optional) Create an environment file if you need to export secrets or flags:

```bash
sudo tee /etc/default/falconbroom > /dev/null <<'EOF'
# Example environment entries
# DATABASE_URL=postgres://user:pass@127.0.0.1/db
# LIFESPAN_OFF=1
EOF
```

4) Reload systemd and enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now falconbroom.service
sudo journalctl -u falconbroom.service -f
```

5) Verify the service is listening on the configured port and healthy.

Notes:
- For public-facing deployments, place a reverse proxy (nginx, Caddy) in front of the service and terminate TLS there.
- Use `gunicorn -k uvicorn.workers.UvicornWorker` if you prefer Gunicorn as the manager; ensure `gunicorn` is installed in your venv.
