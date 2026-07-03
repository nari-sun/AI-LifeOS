use serde_json::Value;
use std::env;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

#[tauri::command]
async fn start_session(payload: Value) -> Result<Value, String> {
    run_bridge("start-session", payload)
}

#[tauri::command]
async fn send_message(payload: Value) -> Result<Value, String> {
    run_bridge("send-message", payload)
}

#[tauri::command]
async fn save_session(payload: Value) -> Result<Value, String> {
    run_bridge("save-session", payload)
}

#[tauri::command]
async fn list_resumable_sessions(payload: Value) -> Result<Value, String> {
    run_bridge("list-resumable", payload)
}

#[tauri::command]
async fn resume_session(payload: Value) -> Result<Value, String> {
    run_bridge("resume-session", payload)
}

#[tauri::command]
async fn finalize_session(payload: Value) -> Result<Value, String> {
    run_bridge("finalize-session", payload)
}

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            start_session,
            send_message,
            save_session,
            list_resumable_sessions,
            resume_session,
            finalize_session
        ])
        .run(tauri::generate_context!())
        .expect("error while running AI-LifeOS desktop app");
}

fn run_bridge(command_name: &str, payload: Value) -> Result<Value, String> {
    let root = project_root()?;
    tauri_log(&root, &format!("bridge.start command={command_name}"));
    let script = root.join("scripts").join("chat_gui_bridge.py");
    if !script.exists() {
        tauri_log(
            &root,
            &format!(
                "bridge.error command={command_name} reason=script_missing path={}",
                script.display()
            ),
        );
        return Err(format!("Python bridge not found: {}", script.display()));
    }

    let mut child = Command::new(python_command())
        .arg(&script)
        .arg(command_name)
        .current_dir(&root)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| {
            tauri_log(
                &root,
                &format!(
                    "bridge.error command={command_name} stage=spawn message={}",
                    safe_log_text(&error.to_string())
                ),
            );
            format!("Failed to start Python bridge: {error}")
        })?;

    {
        let stdin = child
            .stdin
            .as_mut()
            .ok_or_else(|| "Failed to open Python bridge stdin.".to_string())?;
        stdin
            .write_all(payload.to_string().as_bytes())
            .map_err(|error| {
                tauri_log(
                    &root,
                    &format!(
                        "bridge.error command={command_name} stage=stdin message={}",
                        safe_log_text(&error.to_string())
                    ),
                );
                format!("Failed to write Python bridge payload: {error}")
            })?;
    }

    let output = child.wait_with_output().map_err(|error| {
        tauri_log(
            &root,
            &format!(
                "bridge.error command={command_name} stage=wait message={}",
                safe_log_text(&error.to_string())
            ),
        );
        format!("Failed to read Python bridge output: {error}")
    })?;

    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let status = output
        .status
        .code()
        .map_or_else(|| "signal".to_string(), |code| code.to_string());
    tauri_log(
        &root,
        &format!("bridge.exit command={command_name} status={status}"),
    );
    let value = parse_bridge_json(&stdout, &stderr).map_err(|error| {
        tauri_log(
            &root,
            &format!(
                "bridge.error command={command_name} stage=parse message={}",
                safe_log_text(&error)
            ),
        );
        error
    })?;

    if !output.status.success() {
        let error = bridge_error(&value, &stderr);
        tauri_log(
            &root,
            &format!(
                "bridge.error command={command_name} stage=python_exit message={}",
                safe_log_text(&error)
            ),
        );
        return Err(error);
    }
    if value.get("ok").and_then(Value::as_bool) == Some(false) {
        let error = bridge_error(&value, &stderr);
        tauri_log(
            &root,
            &format!(
                "bridge.error command={command_name} stage=bridge_response message={}",
                safe_log_text(&error)
            ),
        );
        return Err(error);
    }

    tauri_log(&root, &format!("bridge.success command={command_name}"));
    Ok(value)
}

fn parse_bridge_json(stdout: &str, stderr: &str) -> Result<Value, String> {
    if stdout.is_empty() {
        if stderr.is_empty() {
            return Err("Python bridge returned no output.".to_string());
        }
        return Err(stderr.to_string());
    }

    serde_json::from_str(stdout).map_err(|error| {
        if stderr.is_empty() {
            format!("Python bridge returned invalid JSON: {error}")
        } else {
            format!("Python bridge returned invalid JSON: {error}\n{stderr}")
        }
    })
}

fn bridge_error(value: &Value, stderr: &str) -> String {
    value
        .get("error")
        .and_then(Value::as_str)
        .map(ToString::to_string)
        .filter(|message| !message.trim().is_empty())
        .unwrap_or_else(|| {
            if stderr.is_empty() {
                "Python bridge failed.".to_string()
            } else {
                stderr.to_string()
            }
        })
}

fn python_command() -> String {
    env::var("AI_LIFEOS_PYTHON").unwrap_or_else(|_| "python".to_string())
}

fn project_root() -> Result<PathBuf, String> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let root = manifest_dir
        .ancestors()
        .nth(3)
        .ok_or_else(|| "Could not resolve AI-LifeOS root from CARGO_MANIFEST_DIR.".to_string())?;

    canonicalize(root)
}

fn canonicalize(path: &Path) -> Result<PathBuf, String> {
    path.canonicalize()
        .map_err(|error| format!("Could not canonicalize {}: {error}", path.display()))
}

fn tauri_log(root: &Path, message: &str) {
    let path = env::var("AI_LIFEOS_TAURI_LOG")
        .map(PathBuf::from)
        .unwrap_or_else(|_| root.join("logs").join("chat_gui_tauri.log"));
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }

    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs().to_string())
        .unwrap_or_else(|_| "0".to_string());

    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{timestamp} pid={} {message}", std::process::id());
    }
}

fn safe_log_text(value: &str) -> String {
    let text = value.replace('\r', "\\r").replace('\n', "\\n");
    if text.chars().count() > 1000 {
        format!(
            "{}...[truncated]",
            text.chars().take(1000).collect::<String>()
        )
    } else {
        text
    }
}
