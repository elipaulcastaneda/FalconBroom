Render deployment guide — quick steps

1) Push your repo to GitHub (branch `main` is fine).

2) Build-time vs runtime notes
- This repo includes a Dockerfile at the project root. Render can build the Docker image directly from it (recommended). The container runs `uvicorn fbroom.main:app` and respects the `PORT` env var Render provides.

3) Create the service on Render
- Sign in to https://render.com and click "New" → "Web Service".
- Connect your GitHub account and select this repository and the `main` branch.
- For "Environment", choose "Docker" (Render will run your Dockerfile). If you instead prefer Render's native Python build, choose "Web Service" (Python) and set build/start commands (see below).

4) Environment & health
- Render provides a `PORT` environment variable — the Dockerfile uses `${PORT}`. No additional config needed for basic operation.
- If you need a fixed hostname, configure a custom domain in Render.

5) Optional: Native (non-Docker) deploy on Render
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn fbroom.main:app --host 0.0.0.0 --port $PORT`

6) Important: Desktop app file upload behaviour
- Current desktop code (`pick_file_and_profile()` in `src-tauri/src/main.rs`) opens a native file dialog and sends the *local file path* to the backend JSON body. That works with a local backend but not a remote one — a remote backend cannot read files on the user's disk by path.
- For a remote backend, you must change the desktop app to upload the selected file's contents (multipart/form-data) instead of sending just the path. Example approach in Rust (Tauri):

  - Read the file bytes on the native side:
    - `let bytes = std::fs::read(&path).map_err(|e| format!("read error: {}", e))?;`
  - Create a multipart form and post it to the remote `/profile` endpoint with reqwest:
    - `let part = reqwest::blocking::multipart::Part::bytes(bytes).file_name(filename);`
    - `let form = reqwest::blocking::multipart::Form::new().part("file", part);`
    - `client.post(&url).multipart(form).send()`

7) After deploy: update the desktop app to point to the deployed backend
- Set `FALCONBROOM_BACKEND_URL` in your packaged app or installer to the deployed URL (e.g. `https://api.yourdomain.com`). The app already prefers that env var if set.

8) Testing locally with Docker
- Build image:
  `docker build -t falconbroom-backend .`
- Run container exposing port 8080:
  `docker run -e PORT=8080 -p 8080:8080 falconbroom-backend`
- Then point your desktop app (or `FALCONBROOM_BACKEND_URL`) to `http://localhost:8080` for integration testing.

9) Security and production considerations
- Use HTTPS and a domain for production. Configure TLS on Render or use Render's automatic TLS.
- Add authentication/rate limiting if the API will be public.
