use serde_json::Value;
use std::collections::HashSet;
use std::env;
use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::ipc::Channel;

static ACTIVE_STREAM_CHILDREN: OnceLock<Mutex<HashSet<u32>>> = OnceLock::new();

#[tauri::command]
async fn start_session(payload: Value) -> Result<Value, String> {
    run_bridge("start-session", payload)
}

#[tauri::command]
async fn read_aloud(payload: Value) -> Result<Value, String> {
    run_bridge("read-aloud", payload)
}

#[tauri::command]
async fn read_aloud_stream(payload: Value, on_event: Channel<Value>) -> Result<Value, String> {
    run_bridge_stream("read-aloud-stream", payload, on_event)
}

#[tauri::command]
async fn cancel_read_aloud(payload: Value) -> Result<Value, String> {
    run_bridge("cancel-read-aloud", payload)
}

#[tauri::command]
async fn discard_read_aloud_audio(payload: Value) -> Result<Value, String> {
    run_bridge("discard-read-aloud-audio", payload)
}

#[tauri::command]
async fn send_message(payload: Value) -> Result<Value, String> {
    run_bridge("send-message", payload)
}

#[tauri::command]
async fn send_message_stream(payload: Value, on_event: Channel<Value>) -> Result<Value, String> {
    run_bridge_stream("send-message-stream", payload, on_event)
}

#[tauri::command]
async fn cancel_message(payload: Value) -> Result<Value, String> {
    run_bridge("cancel-message", payload)
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
async fn get_personalization(payload: Value) -> Result<Value, String> {
    run_bridge("get-personalization", payload)
}

#[tauri::command]
async fn update_personalization(payload: Value) -> Result<Value, String> {
    run_bridge("update-personalization", payload)
}

#[tauri::command]
async fn get_memory_summary(payload: Value) -> Result<Value, String> {
    run_bridge("get-memory-summary", payload)
}

#[tauri::command]
async fn get_notion_connection(payload: Value) -> Result<Value, String> {
    run_bridge("get-notion-connection", payload)
}

#[tauri::command]
async fn finalize_session(payload: Value) -> Result<Value, String> {
    run_bridge("finalize-session", payload)
}

#[tauri::command]
async fn start_finalize_job(payload: Value) -> Result<Value, String> {
    run_bridge("start-finalize-job", payload)
}

#[tauri::command]
async fn get_finalize_job(payload: Value) -> Result<Value, String> {
    run_bridge("get-finalize-job", payload)
}

#[tauri::command]
async fn cancel_finalize_job(payload: Value) -> Result<Value, String> {
    run_bridge("cancel-finalize-job", payload)
}

#[tauri::command]
async fn start_organize_sessions_job(payload: Value) -> Result<Value, String> {
    run_bridge("start-organize-sessions-job", payload)
}

#[tauri::command]
async fn get_organize_sessions_job(payload: Value) -> Result<Value, String> {
    run_bridge("get-organize-sessions-job", payload)
}

#[tauri::command]
async fn cancel_organize_sessions_job(payload: Value) -> Result<Value, String> {
    run_bridge("cancel-organize-sessions-job", payload)
}

#[tauri::command]
async fn local_data_report(payload: Value) -> Result<Value, String> {
    run_bridge("local-data-report", payload)
}

#[tauri::command]
async fn open_local_data_folder(payload: Value) -> Result<Value, String> {
    run_bridge("open-local-data-folder", payload)
}

#[tauri::command]
async fn preview_chatgpt_import(payload: Value) -> Result<Value, String> {
    run_bridge("preview-chatgpt-import", payload)
}

#[tauri::command]
async fn apply_chatgpt_import(payload: Value) -> Result<Value, String> {
    run_bridge("apply-chatgpt-import", payload)
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            start_session,
            read_aloud,
            read_aloud_stream,
            cancel_read_aloud,
            discard_read_aloud_audio,
            send_message,
            send_message_stream,
            cancel_message,
            save_session,
            list_resumable_sessions,
            resume_session,
            get_personalization,
            update_personalization,
            get_memory_summary,
            get_notion_connection,
            finalize_session,
            start_finalize_job,
            get_finalize_job,
            cancel_finalize_job,
            start_organize_sessions_job,
            get_organize_sessions_job,
            cancel_organize_sessions_job,
            local_data_report,
            open_local_data_folder,
            preview_chatgpt_import,
            apply_chatgpt_import
        ])
        .build(tauri::generate_context!())
        .expect("error while building AI-LifeOS desktop app");

    app.run(|_app_handle, event| {
        if matches!(
            event,
            tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
        ) {
            terminate_active_stream_children();
        }
    });
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

    let mut child = Command::new(python_command(&root))
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

fn run_bridge_stream(
    command_name: &str,
    payload: Value,
    on_event: Channel<Value>,
) -> Result<Value, String> {
    let root = project_root()?;
    tauri_log(&root, &format!("bridge.start command={command_name}"));
    let script = root.join("scripts").join("chat_gui_bridge.py");
    if !script.exists() {
        return Err(format!("Python bridge not found: {}", script.display()));
    }

    let mut child = Command::new(python_command(&root))
        .arg(&script)
        .arg(command_name)
        .current_dir(&root)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("Failed to start Python bridge: {error}"))?;

    let child_pid = child.id();
    register_stream_child(child_pid);
    let stderr_stream = match child.stderr.take() {
        Some(stream) => stream,
        None => {
            terminate_child(&mut child);
            return Err("Failed to open Python bridge stderr.".to_string());
        }
    };
    let stderr_thread = thread::spawn(move || {
        let mut stderr = String::new();
        let mut stream = stderr_stream;
        let _ = stream.read_to_string(&mut stderr);
        stderr
    });

    let operation = (|| -> Result<(ExitStatus, Option<Value>), String> {
        let stdin = child
            .stdin
            .as_mut()
            .ok_or_else(|| "Failed to open Python bridge stdin.".to_string())?;
        stdin
            .write_all(payload.to_string().as_bytes())
            .map_err(|error| format!("Failed to write Python bridge payload: {error}"))?;
        drop(child.stdin.take());

        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "Failed to open Python bridge stdout.".to_string())?;
        let mut result: Option<Value> = None;
        for line in BufReader::new(stdout).lines() {
            let line =
                line.map_err(|error| format!("Failed to read Python bridge output: {error}"))?;
            if line.trim().is_empty() {
                continue;
            }
            let event: Value = serde_json::from_str(&line)
                .map_err(|error| format!("Python bridge returned invalid stream JSON: {error}"))?;
            match event.get("type").and_then(Value::as_str) {
                Some("delta") | Some("audio") => on_event
                    .send(event)
                    .map_err(|error| format!("Failed to send stream event to GUI: {error}"))?,
                Some("result") => result = event.get("data").cloned(),
                _ => {}
            }
        }

        let status = child
            .wait()
            .map_err(|error| format!("Failed to wait for Python bridge: {error}"))?;
        Ok((status, result))
    })();

    if operation.is_err() {
        terminate_child(&mut child);
    } else {
        unregister_stream_child(child_pid);
    }
    let stderr = stderr_thread
        .join()
        .unwrap_or_else(|_| "Failed to collect Python bridge stderr.".to_string());
    let (status, result) = operation?;
    let value = result.ok_or_else(|| {
        if stderr.trim().is_empty() {
            "Python bridge returned no stream result.".to_string()
        } else {
            stderr.trim().to_string()
        }
    })?;
    if !status.success() || value.get("ok").and_then(Value::as_bool) == Some(false) {
        return Err(bridge_error(&value, stderr.trim()));
    }
    tauri_log(&root, &format!("bridge.success command={command_name}"));
    Ok(value)
}

fn active_stream_children() -> &'static Mutex<HashSet<u32>> {
    ACTIVE_STREAM_CHILDREN.get_or_init(|| Mutex::new(HashSet::new()))
}

fn register_stream_child(pid: u32) {
    if let Ok(mut children) = active_stream_children().lock() {
        children.insert(pid);
    }
}

fn unregister_stream_child(pid: u32) {
    if let Ok(mut children) = active_stream_children().lock() {
        children.remove(&pid);
    }
}

fn terminate_child(child: &mut Child) {
    let pid = child.id();
    match child.try_wait() {
        Ok(Some(_)) => {}
        _ => {
            terminate_process_tree(pid);
            let _ = child.kill();
            let _ = child.wait();
        }
    }
    unregister_stream_child(pid);
}

fn terminate_active_stream_children() {
    let pids = active_stream_children()
        .lock()
        .map(|children| children.iter().copied().collect::<Vec<_>>())
        .unwrap_or_default();
    for pid in pids {
        terminate_process_tree(pid);
        unregister_stream_child(pid);
    }
}

#[cfg(windows)]
fn terminate_process_tree(pid: u32) {
    let _ = Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

#[cfg(not(windows))]
fn terminate_process_tree(pid: u32) {
    let _ = Command::new("kill")
        .args(["-TERM", &pid.to_string()])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
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

fn python_command(root: &Path) -> String {
    if let Ok(command) = env::var("AI_LIFEOS_PYTHON") {
        return command;
    }

    let project_venv_python = root.join(".venv").join("Scripts").join("python.exe");
    if project_venv_python.is_file() {
        return project_venv_python.to_string_lossy().into_owned();
    }

    "python".to_string()
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
