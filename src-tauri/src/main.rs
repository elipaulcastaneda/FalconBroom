#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::Value;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::{Manager, State};

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

fn resource_dir() -> Option<PathBuf> {
    tauri::api::path::resource_dir()
}

fn find_bundled_python() -> Option<PathBuf> {
    // Look for `resource_dir()/py` venv
    resource_dir().and_then(|mut p| {
        p.push("py");
        if cfg!(target_os = "windows") {
            p.push("Scripts");
            p.push("python.exe");
            if p.exists() {
                return Some(p);
            }
        } else {
            let mut p2 = p.clone();
            p2.push("bin");
            p2.push("python3");
            if p2.exists() {
                return Some(p2);
            }
            let mut p3 = p.clone();
            p3.push("bin");
            p3.push("python");
            if p3.exists() {
                return Some(p3);
            }
        }
        None
    })
}

fn find_system_python() -> Option<PathBuf> {
    let candidates = if cfg!(target_os = "windows") {
        vec!["python", "py"]
    } else {
        vec!["python3", "python"]
    };
    for c in candidates {
        if let Ok(output) = Command::new(c).arg("--version").output() {
            if output.status.success() {
                if let Ok(full) = which::which(c) {
                    return Some(full);
                }
            }
        }
    }
    None
}

fn find_python_executable() -> Result<PathBuf, String> {
    // Prefer bundled venv
    if let Some(p) = find_bundled_python() {
        return Ok(p);
    }
    // Then system python
    if let Some(p) = find_system_python() {
        return Ok(p);
    }
    Err("No suitable Python executable found. Install Python or bundle a venv into resources/py".into())
}

fn spawn_backend() -> Result<Child, String> {
    let python = find_python_executable()?;

    // Determine working directory for Python app: resource_dir()/python_app when bundled, else current dir
    let workdir = resource_dir().map(|mut p| { p.push("python_app"); p }).unwrap_or_else(|| std::env::current_dir().unwrap());

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
    // Open a native blocking file dialog (returns Option<std::path::PathBuf>)
    let path = tauri::api::dialog::blocking::FileDialogBuilder::new()
        .add_filter("CSV", &["csv"])
        .pick_file();

    let path = match path {
        Some(p) => p.to_string_lossy().to_string(),
        None => return Err("No file selected".into()),
    };

    // Call local backend /profile
    let client = reqwest::blocking::Client::new();
    let url = "http://127.0.0.1:3008/profile";
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
    // Start backend process and keep handle in state
    let backend_child = match spawn_backend() {
        Ok(c) => Some(BackendChild::new(c)),
        Err(e) => {
            eprintln!("Warning: could not start backend: {}", e);
            None
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
