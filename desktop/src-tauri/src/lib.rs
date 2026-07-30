use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Output, Stdio};
use tauri::Manager;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

#[derive(Debug, Deserialize)]
struct CcPortActionRequest {
    action: String,
    payload: Value,
}

#[derive(Debug, Serialize)]
struct CcPortActionResponse {
    ok: bool,
    data: Option<Value>,
    error: Option<Value>,
}

#[derive(Debug, Serialize)]
struct CcPortBridgeError {
    code: String,
    detail: String,
}

impl CcPortBridgeError {
    fn new(code: &str, detail: impl Into<String>) -> Self {
        Self {
            code: code.to_string(),
            detail: detail.into(),
        }
    }
}

#[tauri::command]
async fn cc_port_action(
    request: CcPortActionRequest,
) -> Result<CcPortActionResponse, CcPortBridgeError> {
    let action = request.action;
    let payload = serde_json::to_string(&request.payload).map_err(|err| {
        CcPortBridgeError::new("bridge.request_serialize_failed", err.to_string())
    })?;
    let output =
        tauri::async_runtime::spawn_blocking(move || run_cc_port_ui_api(&action, &payload))
            .await
            .map_err(|err| CcPortBridgeError::new("bridge.sidecar_task_failed", err.to_string()))?
            .map_err(|err| CcPortBridgeError::new("bridge.sidecar_unavailable", err))?;
    let raw = String::from_utf8_lossy(&output).trim().to_string();

    let parsed: Value = serde_json::from_str(&raw).map_err(|err| {
        CcPortBridgeError::new("bridge.invalid_sidecar_response", err.to_string())
    })?;

    Ok(CcPortActionResponse {
        ok: parsed.get("ok").and_then(Value::as_bool).unwrap_or(false),
        data: parsed.get("data").cloned(),
        error: parsed.get("error").cloned(),
    })
}

#[tauri::command]
async fn open_path(path: String) -> Result<(), CcPortBridgeError> {
    tauri::async_runtime::spawn_blocking(move || open_path_with_system(&path))
        .await
        .map_err(|err| CcPortBridgeError::new("bridge.open_path_task_failed", err.to_string()))?
        .map_err(|err| CcPortBridgeError::new("bridge.open_path_failed", err))
}

/// One way to invoke cc-port-desktop-api.
struct Candidate {
    label: String,
    program: String,
    args: Vec<String>,
}

impl Candidate {
    fn new(label: impl Into<String>, program: impl Into<String>, args: Vec<String>) -> Self {
        Self {
            label: label.into(),
            program: program.into(),
            args,
        }
    }

    fn to_command(&self) -> Command {
        let mut cmd = Command::new(&self.program);
        cmd.args(&self.args);
        #[cfg(windows)]
        cmd.creation_flags(CREATE_NO_WINDOW);
        cmd
    }
}

fn build_candidates(action: &str) -> Vec<Candidate> {
    let mut out: Vec<Candidate> = Vec::new();
    let api_args = vec![action.to_string()];

    if let Ok(bin) = std::env::var("CC_PORT_DESKTOP_API_BIN") {
        if !bin.trim().is_empty() {
            out.push(Candidate::new(
                "$CC_PORT_DESKTOP_API_BIN",
                bin.trim(),
                api_args.clone(),
            ));
        }
    }

    if let Some(exe_dir) = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(PathBuf::from))
    {
        // Tauri renames `bundle.externalBin` files to plain `cc-port-desktop-api(.exe)` in
        // the final installer / release output, but keeps the
        // `cc-port-desktop-api-{target_triple}{.exe}` naming for `cargo run` / `tauri dev`.
        // Try both, plus a few common siblings (Resources/, _up_/, ...) that
        // various Tauri bundles use on different platforms.
        let triple = env!("TAURI_ENV_TARGET_TRIPLE");
        let names = [
            "cc-port-desktop-api.exe".to_string(),
            "cc-port-desktop-api".to_string(),
            format!("cc-port-desktop-api-{triple}.exe"),
            format!("cc-port-desktop-api-{triple}"),
        ];
        let search_dirs = [
            exe_dir.clone(),
            exe_dir.join("resources"),
            exe_dir.join("Resources"),
            exe_dir.join("_up_").join("binaries"),
        ];
        for dir in &search_dirs {
            for name in &names {
                let candidate_path = dir.join(name);
                if candidate_path.is_file() {
                    out.push(Candidate::new(
                        format!("sibling: {}", candidate_path.display()),
                        candidate_path.to_string_lossy().to_string(),
                        api_args.clone(),
                    ));
                }
            }
        }
    }

    out
}

fn run_cc_port_ui_api(action: &str, payload: &str) -> Result<Vec<u8>, String> {
    let candidates = build_candidates(action);
    let mut errors: Vec<String> = Vec::new();

    for candidate in &candidates {
        match run_candidate(candidate, payload) {
            Ok(output)
                if output.status.success() || is_structured_sidecar_response(&output.stdout) =>
            {
                return Ok(output.stdout);
            }
            Ok(output) => {
                let mut msg = String::from_utf8_lossy(&output.stderr).trim().to_string();
                if msg.is_empty() {
                    msg = String::from_utf8_lossy(&output.stdout).trim().to_string();
                }
                if msg.is_empty() {
                    msg = format!("exit code: {}", output.status);
                }
                errors.push(format!("[{}] {}", candidate.label, msg));
            }
            Err(err) => {
                errors.push(format!("[{}] spawn failed: {}", candidate.label, err));
            }
        }
    }

    let detail = if errors.is_empty() {
        "no candidate available".to_string()
    } else {
        errors.join("\n")
    };
    Err(format!(
        "Unable to run cc-port-desktop-api. Tried {} candidates:\n{}\n\nHints:\n  - Run the desktop build scripts so Tauri can bundle the sidecar.\n  - Or set CC_PORT_DESKTOP_API_BIN to the absolute path of cc-port-desktop-api(.exe).",
        candidates.len(),
        detail
    ))
}

fn is_structured_sidecar_response(stdout: &[u8]) -> bool {
    serde_json::from_slice::<Value>(stdout)
        .ok()
        .and_then(|value| value.get("ok").and_then(Value::as_bool))
        .is_some()
}

fn run_candidate(candidate: &Candidate, payload: &str) -> std::io::Result<Output> {
    let mut command = candidate.to_command();
    command
        .env("CC_PORT_DESKTOP_API_PAYLOAD", payload)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command.spawn()?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(payload.as_bytes())?;
    }
    child.wait_with_output()
}

fn open_path_with_system(path: &str) -> Result<(), String> {
    let target = PathBuf::from(path);
    if !target.exists() {
        return Err(format!("Path does not exist: {}", target.display()));
    }

    #[cfg(target_os = "windows")]
    {
        let mut cmd = Command::new("explorer");
        cmd.arg(&target);
        cmd.creation_flags(CREATE_NO_WINDOW);
        return cmd
            .spawn()
            .map(|_| ())
            .map_err(|err| format!("Unable to open {}: {}", target.display(), err));
    }

    #[cfg(not(target_os = "windows"))]
    {
        #[cfg(target_os = "macos")]
        let mut command = {
            let mut cmd = Command::new("open");
            cmd.arg(&target);
            cmd
        };

        #[cfg(all(unix, not(target_os = "macos")))]
        let mut command = {
            let mut cmd = Command::new("xdg-open");
            cmd.arg(&target);
            cmd
        };

        let status = command
            .status()
            .map_err(|err| format!("Unable to open {}: {}", target.display(), err))?;
        if status.success() {
            Ok(())
        } else {
            Err(format!("Open command failed with status: {status}"))
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default();

    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }));
    }

    builder
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![cc_port_action, open_path])
        .run(tauri::generate_context!())
        .expect("error while running CC Port");
}

#[cfg(test)]
mod tests {
    use super::{is_structured_sidecar_response, CcPortBridgeError};

    #[test]
    fn bridge_errors_serialize_stable_code_and_external_detail() {
        let error = CcPortBridgeError::new("bridge.open_path_failed", "Path does not exist: C:\\x");
        let json = serde_json::to_value(error).expect("bridge error must serialize");

        assert_eq!(json["code"], "bridge.open_path_failed");
        assert_eq!(json["detail"], "Path does not exist: C:\\x");
    }

    #[test]
    fn structured_backend_error_is_not_misclassified_as_sidecar_failure() {
        assert!(is_structured_sidecar_response(
            br#"{"ok":false,"error":{"code":"OSError","message":"access denied"}}"#
        ));
        assert!(is_structured_sidecar_response(
            br#"{"ok":true,"data":{"items":[]}}"#
        ));
        assert!(!is_structured_sidecar_response(b"not json"));
        assert!(!is_structured_sidecar_response(br#"{"error":"missing ok"}"#));
    }
}
