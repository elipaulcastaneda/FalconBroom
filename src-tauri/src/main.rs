#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::Value;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};

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

    let mut cmd = Command::new(python);
    cmd.args(&["-m", "uvicorn", "fbroom.main:app", "--port", "3008", "--host", "127.0.0.1"])
        .current_dir(workdir)
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
    let base = std::env::var("FALCONBROOM_BACKEND_URL").unwrap_or_else(|_| "http://127.0.0.1:3008".to_string());
    let url = format!("{}/profile", base.trim_end_matches('/'));
    let body = serde_json::json!({"path": path});
    let resp = client
        .post(url)
        .json(&body)
        .send()
        .map_err(|e| format!("Failed to call backend: {}", e))?;
    let j: Value = resp
        .json()
        .map_err(|e| format!("Failed to parse backend response: {}", e))?;
    Ok(j)
}

fn main() {
        // Start backend process and keep handle in state.
        // If `FALCONBROOM_BACKEND_URL` is set we assume a remote backend and skip spawning a local one.
        let backend_child = if std::env::var("FALCONBROOM_BACKEND_URL").is_ok() {
            None
        } else {
            match spawn_backend() {
                Ok(c) => Some(BackendChild::new(c)),
                Err(e) => {
                    eprintln!("Warning: could not start backend: {}", e);
                    None
                }
            }
        };

    let builder = tauri::Builder::default()
        .manage(backend_child)
        .invoke_handler(tauri::generate_handler![pick_file_and_profile]);

    let app = builder.build(tauri::generate_context!()).expect("error while building tauri application");

    app.run(|_app_handle, _event| {
        // Application event loop
    });
}
