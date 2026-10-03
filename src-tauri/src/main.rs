#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::Value;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::Manager;
use keyring::Entry;

struct BackendChild(Arc<Mutex<Option<Child>>>);

impl BackendChild {
    fn new(child: Child) -> Self {
        BackendChild(Arc::new(Mutex::new(Some(child))))
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
        "No .venv Python found at the project root. Expected C:/Users/Elijah/FalconBroom/.venv/Scripts/python.exe".into()
    })
}

fn spawn_backend() -> Result<Child, String> {
    let python = find_python_executable()?;

    let workdir = project_root().unwrap_or_else(|| std::env::current_dir().unwrap());

    // Prefer running our safe, in-repo runner which contains the lifespan-off
    // defaults, wait-for-listen logic and diagnostics. If the script is not
    // present for any reason, fall back to `-m uvicorn` as before.
    let mut cmd = Command::new(&python);
    let script_path = workdir.join("scripts").join("run_uvicorn_single.py");
    if script_path.exists() {
        cmd.arg(script_path);
    } else {
        cmd.args(&["-m", "uvicorn", "fbroom.main:app", "--port", "3009", "--host", "127.0.0.1"]);
    }

    // For local packaged/dev runs, prefer disabling ASGI lifespan to avoid
    // the observed race. Also preserve a clean background execution.
    cmd.current_dir(workdir)
        .env("LIFESPAN_OFF", "1")
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    let child = cmd.spawn().map_err(|e| format!("Failed to spawn backend: {}", e))?;
    Ok(child)
}

// Command exposed to frontend: opens native file dialog and posts the selected path to the backend /profile endpoint.
#[tauri::command]
fn pick_file_and_profile() -> Result<Value, String> {
    let path = rfd::FileDialog::new()
        .add_filter("CSV", &["csv"])
        .pick_file();

    let path = match path {
        Some(p) => p.to_string_lossy().to_string(),
        None => return Err("No file selected".into()),
    };

    // Call backend /profile. Prefer remote backend URL if provided via env var.
    let client = reqwest::blocking::Client::new();
    let env_base = std::env::var("FALCONBROOM_BACKEND_URL");
    let base = env_base.clone().unwrap_or_else(|_| "http://127.0.0.1:3009".to_string());
    let url = format!("{}/profile", base.trim_end_matches('/'));

    let resp = if env_base.is_ok() {
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
        Err(e) => {
            // If no entry found, return Ok(None). For other errors, return Err with message.
            let s = format!("{}", e);
            if s.contains("No entry") || s.contains("no entry") {
                Ok(None)
            } else {
                Err(format!("secure_store_get error: {}", s))
            }
        }
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
        Err(e) => {
            let s = format!("{}", e);
            if s.contains("No entry") || s.contains("no entry") {
                // already not present
                Ok(true)
            } else {
                Err(format!("secure_store_delete error: {}", s))
            }
        }
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
        // Start backend process and keep handle in state.
        // If `FALCONBROOM_BACKEND_URL` is set we assume a remote backend and skip spawning a local one.
        let backend_child = if std::env::var("FALCONBROOM_BACKEND_URL").is_ok() {
            None
        } else {
            match spawn_backend() {
                Ok(c) => {
                    // After spawning the backend, perform a short health-check loop
                    // to ensure the local server is accepting connections before
                    // the webview loads. This prevents transient ERR_CONNECTION_REFUSED
                    // in embedded webviews that may execute network requests immediately.
                    let health_url = "http://127.0.0.1:3009/health";
                    let mut healthy = false;
                    for _ in 0..20 {
                        // try a quick blocking request with short timeout
                        if let Ok(resp) = reqwest::blocking::get(health_url) {
                            if resp.status().is_success() {
                                healthy = true;
                                break;
                            }
                        }
                        std::thread::sleep(std::time::Duration::from_millis(250));
                    }
                    if !healthy {
                        eprintln!("Warning: backend health check failed or timed out; continuing anyway");
                    }
                    Some(BackendChild::new(c))
                }
                Err(e) => {
                    eprintln!("Warning: could not start backend: {}", e);
                    None
                }
            }
        };

    // Prepare a synchronous initialization script to set the backend URL before any
    // web content executes. Use env override if present, otherwise default to
    // the local backend URL we spawn.
    let env_base = std::env::var("FALCONBROOM_BACKEND_URL").ok();
    let base = env_base.clone().unwrap_or_else(|| "http://127.0.0.1:3009".to_string());
    // Escape single quotes in base just in case
    let base_escaped = base.replace("'", "\\'");
    let init_js = format!("(function(){{try{{window.__FALCONBROOM_BACKEND_URL = '{}' ;}}catch(e){{}}}})();", base_escaped);

    let builder = tauri::Builder::default()
        .manage(backend_child)
        .invoke_handler(tauri::generate_handler![pick_file_and_profile, secure_store_get, secure_store_set, secure_store_delete])
        .setup(move |app| {
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
            // the window's label.
            let webview_builder = tauri::webview::WebviewBuilder::new(
                "main-webview",
                tauri::WebviewUrl::App("index.html".into()),
            )
            .initialization_script(&init_js);

            // Add the webview as a child to the window at position 0,0 and
            // the window's current inner size.
            let size = window.inner_size()?;
            let _webview = window.add_child(webview_builder, tauri::LogicalPosition::new(0.0, 0.0), size)?;

            Ok(())
        });

    let app = builder.build(tauri::generate_context!()).expect("error while building tauri application");

    app.run(|_app_handle, _event| {
        // Application event loop
    });
}
