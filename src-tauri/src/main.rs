#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::Value;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::Manager;
use keyring::Entry;

struct BackendChild(Arc<Mutex<Option<Child>>>);

struct BackendPort(u16);
struct BackendSecret(String);
struct BackendMode {
    base: String,
    remote: bool,
    interaction: Arc<Mutex<bool>>,
}

const REMOTE_SERVICES: [&str; 8] = [
    "remote_backend", "google_drive", "geocoding", "exchangerate_host",
    "openexchangerates", "http_lookup", "audit_export", "smtp",
];

fn read_remote_policy(path: &std::path::Path) -> Result<Value, String> {
    let text = match std::fs::read_to_string(path) {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return Ok(Value::Object(REMOTE_SERVICES.iter().map(|name| (name.to_string(), Value::Bool(true))).collect()));
        }
        Err(e) => return Err(format!("Cannot read remote-service preferences: {e}")),
    };
    let policy: Value = serde_json::from_str(&text)
        .map_err(|e| format!("Invalid remote-service preferences: {e}"))?;
    let object = policy.as_object().ok_or("Invalid remote-service preferences")?;
    if object.len() != REMOTE_SERVICES.len()
        || REMOTE_SERVICES.iter().any(|name| !object.get(*name).is_some_and(Value::is_boolean))
    {
        return Err("Invalid remote-service preferences".into());
    }
    Ok(policy)
}

#[tauri::command]
async fn set_remote_services(
    policy: Value,
    expected: Value,
    port: tauri::State<'_, BackendPort>,
    secret: tauri::State<'_, BackendSecret>,
    mode: tauri::State<'_, BackendMode>,
) -> Result<Value, String> {
    let port = port.0;
    let secret = secret.0.clone();
    let interaction = mode.interaction.clone();
    let remote = mode.remote;
    tauri::async_runtime::spawn_blocking(move || {
        let mut paused = interaction.lock().map_err(|_| "Remote request coordination failed")?;
        let client = reqwest::blocking::Client::builder().build().map_err(|e| e.to_string())?;
        let response = client.post(format!("http://127.0.0.1:{port}/desktop/remote-services"))
            .header("X-FalconBroom-Desktop-Token", &secret)
            .json(&serde_json::json!({"policy": policy, "expected": expected}))
            .send().map_err(|e| format!("Cannot save remote-service preferences: {e}"))?;
        if !response.status().is_success() {
            let status = response.status();
            let detail = response.text().map_err(|e| format!("Cannot read preferences error: {e}"))?;
            return Err(format!("Cannot save remote-service preferences ({status}): {detail}"));
        }
        if remote {
            *paused = REMOTE_SERVICES.iter().any(|name| policy.get(*name).and_then(Value::as_bool) != Some(true));
        }
        response.json().map_err(|e| format!("Invalid preferences response: {e}"))
    }).await.map_err(|e| format!("Remote-service settings task failed: {e}"))?
}

impl BackendChild {
    fn empty() -> Self {
        BackendChild(Arc::new(Mutex::new(None)))
    }

    fn set(&self, child: Child) {
        *self.0.lock().unwrap() = Some(child);
    }

    fn kill(&self) {
        if let Some(mut c) = self.0.lock().unwrap().take() {
            let _ = c.kill();
        }
    }
}

impl Drop for BackendChild {
    fn drop(&mut self) {
        self.kill();
    }
}

fn project_root() -> Option<PathBuf> {
    let cwd = std::env::current_dir().ok()?;
    let src_tauri = cwd.file_name().and_then(|n| n.to_str()).unwrap_or_default();
    if src_tauri.eq_ignore_ascii_case("src-tauri") {
        return cwd.parent().map(|p| p.to_path_buf());
    }

    let mut candidate = cwd.clone();
    candidate.push("src-tauri");
    if candidate.exists() {
        return Some(cwd);
    }

    None
}

fn find_project_venv_python() -> Option<PathBuf> {
    let mut root = project_root()?;
    root.push(".venv");
    if cfg!(target_os = "windows") {
        root.push("Scripts");
        root.push("python.exe");
        if root.exists() {
            return Some(root);
        }
    } else {
        let mut p = root.clone();
        p.push("bin");
        p.push("python3");
        if p.exists() {
            return Some(p);
        }
        let mut p2 = root.clone();
        p2.push("bin");
        p2.push("python");
        if p2.exists() {
            return Some(p2);
        }
    }
    None
}

fn find_python_executable() -> Result<PathBuf, String> {
    find_project_venv_python().ok_or_else(|| {
        "No bundled backend found next to the app and no .venv Python at the project root (dev only).".into()
    })
}

fn free_port() -> Result<u16, String> {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").map_err(|e| format!("No free port: {}", e))?;
    listener.local_addr().map(|a| a.port()).map_err(|e| e.to_string())
}

// The installed app ships the backend as a sidecar executable next to the main binary.
fn find_bundled_backend() -> Option<PathBuf> {
    let dir = std::env::current_exe().ok()?.parent()?.to_path_buf();
    let name = if cfg!(target_os = "windows") { "falconbroom-backend.exe" } else { "falconbroom-backend" };
    let p = dir.join(name);
    if p.exists() { Some(p) } else { None }
}

fn spawn_backend(port: u16, data_dir: &std::path::Path, secret: &str, control_only: bool) -> Result<Child, String> {
    let parent_pid = std::process::id().to_string();
    let port_s = port.to_string();

    let mut cmd = if let Some(exe) = find_bundled_backend() {
        std::fs::create_dir_all(data_dir).map_err(|e| format!("Cannot create data dir: {}", e))?;
        let mut c = Command::new(exe);
        c.arg("--port").arg(&port_s)
            .arg("--data-dir").arg(data_dir)
            .arg("--parent-pid").arg(&parent_pid);
        c
    } else {
        // Development: run the backend from the repo's virtualenv.
        let python = find_python_executable()?;
        let workdir = project_root().unwrap_or_else(|| std::env::current_dir().unwrap());
        let mut c = Command::new(&python);
        c.args(&["-m", "fbroom.sidecar", "--port"]).arg(&port_s)
            .arg("--parent-pid").arg(&parent_pid)
            .current_dir(workdir);
        c
    };

    cmd.env("FALCONBROOM_DESKTOP_SECRET", secret);
    cmd.env("FALCONBROOM_REMOTE_POLICY_FILE", data_dir.join("data").join("remote-services.json"));
    cmd.env("FALCONBROOM_CONTROL_ONLY", if control_only { "1" } else { "0" });
    cmd.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    cmd.spawn().map_err(|e| format!("Failed to spawn backend: {}", e))
}

// Command exposed to frontend: opens native file dialog and posts the selected path to the backend /profile endpoint.
#[tauri::command]
fn pick_file_and_profile(port: tauri::State<'_, BackendPort>, secret: tauri::State<'_, BackendSecret>, mode: tauri::State<'_, BackendMode>) -> Result<Value, String> {
    let paused = mode.interaction.lock().map_err(|_| "Remote request coordination failed")?;
    if *paused {
        return Err("Remote backend is disabled. Restart the app to use the local backend.".into());
    }
    let path = rfd::FileDialog::new()
        .add_filter("CSV", &["csv"])
        .pick_file();

    let path = match path {
        Some(p) => p.to_string_lossy().to_string(),
        None => return Err("No file selected".into()),
    };

    // Call backend /profile. Prefer remote backend URL if provided via env var.
    let client = reqwest::blocking::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .build().map_err(|e| e.to_string())?;
    let _lease = if mode.remote {
        let response = client.get(format!("http://127.0.0.1:{}/desktop/remote-lease", port.0))
            .header("X-FalconBroom-Desktop-Token", &secret.0)
            .send().map_err(|e| format!("Cannot coordinate remote request: {e}"))?;
        if !response.status().is_success() {
            return Err(format!("Remote backend is unavailable: {}", response.status()));
        }
        Some(response)
    } else {
        None
    };
    let base = &mode.base;
    let url = format!("{}/profile", base.trim_end_matches('/'));

    let resp = if mode.remote {
        // Remote backend: upload the file contents as multipart/form-data
        let p = std::path::PathBuf::from(&path);
        let filename = p
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("file")
            .to_string();

        let bytes = std::fs::read(&p).map_err(|e| format!("Failed to read selected file: {}", e))?;

        let part = reqwest::blocking::multipart::Part::bytes(bytes).file_name(filename);
        let form = reqwest::blocking::multipart::Form::new().part("file", part);

        client
            .post(url)
            .multipart(form)
            .send()
            .map_err(|e| format!("Failed to call remote backend: {}", e))?
    } else {
        // Local backend: send the path in JSON (existing behavior)
        let body = serde_json::json!({"path": path});
        client
            .post(url)
            .header("X-FalconBroom-Desktop-Token", &secret.0)
            .json(&body)
            .send()
            .map_err(|e| format!("Failed to call backend: {}", e))?
    };

    let j: Value = resp
        .json()
        .map_err(|e| format!("Failed to parse backend response: {}", e))?;
    Ok(j)
}

// Secure store commands for frontend tokenStore.js
#[tauri::command]
fn secure_store_get(key: String) -> Result<Option<String>, String> {
    // use keyring crate to retrieve a stored secret
    let service = "falconbroom";
    let kr = Entry::new(service, &key);
    match kr.get_password() {
        Ok(pw) => Ok(Some(pw)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("secure_store_get error: {}", e)),
    }
}

#[tauri::command]
fn secure_store_set(key: String, value: String) -> Result<bool, String> {
    let service = "falconbroom";
    let kr = Entry::new(service, &key);
    match kr.set_password(&value) {
        Ok(()) => Ok(true),
        Err(e) => Err(format!("secure_store_set error: {}", e)),
    }
}

#[tauri::command]
fn secure_store_delete(key: String) -> Result<bool, String> {
    let service = "falconbroom";
    let kr = Entry::new(service, &key);
    match kr.delete_password() {
        Ok(()) => Ok(true),
        Err(keyring::Error::NoEntry) => Ok(true),
        Err(e) => Err(format!("secure_store_delete error: {}", e)),
    }
}

// Command for frontend to report early logs. Appends to a local file for debugging.
#[tauri::command]
fn frontend_log(payload: String) -> Result<bool, String> {
    use std::fs::OpenOptions;
    use std::io::Write;
    let path = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path.join("frontend_early_failures.log"))
        .map_err(|e| format!("Failed to open frontend log file: {}", e))?;
    let ts = chrono::Utc::now().to_rfc3339();
    writeln!(file, "{} - {}", ts, payload).map_err(|e| format!("Failed to write frontend log: {}", e))?;
    Ok(true)
}

fn main() {
    let mut context = tauri::generate_context!();
    let data_dir = dirs::data_dir().expect("Application data directory is unavailable")
        .join(&context.config().identifier);
    let policy = read_remote_policy(&data_dir.join("data").join("remote-services.json"))
        .expect("Cannot load remote-service preferences");
    let configured = std::env::var("FALCONBROOM_BACKEND_URL").ok().filter(|s| !s.trim().is_empty());
    let mut warning = None;
    let remote = if let Some(value) = configured {
        if REMOTE_SERVICES.iter().any(|name| policy.get(*name).and_then(Value::as_bool) != Some(true)) {
            warning = Some("The configured remote backend is blocked because a remote service is disabled.");
            None
        } else {
            match reqwest::Url::parse(&value) {
                Ok(url) if url.scheme() == "https" && url.username().is_empty()
                    && url.password().is_none() && url.host_str().is_some()
                    && url.fragment().is_none() && url.query().is_none() => {
                    let origin = url.origin().ascii_serialization();
                    let already_allowed = match &context.config().app.security.csp {
                        Some(tauri::utils::config::Csp::DirectiveMap(directives)) => directives.get("connect-src")
                            .map(|sources| Vec::<String>::from(sources.clone()).contains(&origin))
                            .unwrap_or(false),
                        _ => false,
                    };
                    if already_allowed {
                        warning = Some("The remote backend must use a different origin from the sign-in service.");
                        None
                    } else {
                        Some(url)
                    }
                },
                _ => {
                    warning = Some("The configured remote backend is invalid; HTTPS is required.");
                    None
                }
            }
        }
    } else {
        None
    };
    if let Some(url) = &remote {
        if let Some(tauri::utils::config::Csp::DirectiveMap(ref mut directives)) = context.config_mut().app.security.csp {
            directives.entry("connect-src".to_string()).or_default().push(url.origin().ascii_serialization());
        } else {
            panic!("Remote backend requires an explicit content security policy");
        }
    }
    let port = free_port().expect("Cannot select a local backend port");
    let local_base = format!("http://127.0.0.1:{}", port);
    let base = remote.as_ref().map(|url| url.as_str().trim_end_matches('/').to_string())
        .unwrap_or_else(|| local_base.clone());
    let mut random = [0u8; 32];
    getrandom::getrandom(&mut random).expect("Could not generate desktop session secret");
    let secret: String = random.iter().map(|b| format!("{:02x}", b)).collect();

    // Set the backend URL before any page script runs.
    let init_js = format!("((base, local, secret, remote, warning) => {{\n{}\n}})({base}, {local}, {secret}, {remote}, {warning});",
        include_str!("remote_init.js"),
        base = serde_json::to_string(&base).unwrap(),
        local = serde_json::to_string(&local_base).unwrap(),
        secret = serde_json::to_string(&secret).unwrap(),
        remote = remote.is_some(),
        warning = serde_json::to_string(&warning).unwrap());
    let start_page = "loading.html";

    let backend_child = BackendChild::empty();
    let exit_handle = BackendChild(backend_child.0.clone());

    let builder = tauri::Builder::default()
        .manage(backend_child)
        .manage(BackendPort(port))
        .manage(BackendSecret(secret.clone()))
        .manage(BackendMode { base: base.clone(), remote: remote.is_some(), interaction: Arc::new(Mutex::new(false)) })
        .invoke_handler(tauri::generate_handler![pick_file_and_profile, set_remote_services, secure_store_get, secure_store_set, secure_store_delete])
        .setup(move |app| {
            match spawn_backend(port, &data_dir, &secret, remote.is_some()) {
                Ok(c) => app.state::<BackendChild>().set(c),
                Err(e) => return Err(format!("Could not start backend: {e}").into()),
            }

            // Create or reuse the main window and attach a webview that runs
            // the initialization script before any page scripts execute. If
            // a window named "main" already exists (e.g. created by the
            // runtime or a previous dev-run), reuse it instead of creating
            // a duplicate which would panic.
            let window = match app.get_window("main") {
                Some(w) => w,
                None => tauri::window::WindowBuilder::new(app, "main").build()?,
            };

            // Use a distinct label for the webview to avoid conflicting with
            // the window's label. The loading page waits for the backend's
            // /health endpoint, then opens index.html.
            let webview_builder = tauri::webview::WebviewBuilder::new(
                "main-webview",
                tauri::WebviewUrl::App(start_page.into()),
            )
            .initialization_script(&init_js);

            // Add the webview as a child to the window at position 0,0 and
            // the window's current inner size.
            let size = window.inner_size()?;
            let webview = window.add_child(webview_builder, tauri::LogicalPosition::new(0.0, 0.0), size)?;

            // A child webview does not follow the window, so keep it filling the window on resize/maximize.
            let follower = webview.clone();
            window.on_window_event(move |event| {
                if let tauri::WindowEvent::Resized(new_size) = event {
                    let _ = follower.set_size(*new_size);
                }
            });

            Ok(())
        });

    let app = builder.build(context).expect("error while building tauri application");

    app.run(move |_app_handle, event| {
        if let tauri::RunEvent::Exit = event {
            exit_handle.kill();
        }
        // Application event loop
    });
}
