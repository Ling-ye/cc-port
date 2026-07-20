use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Output, Stdio};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

#[derive(Debug, Deserialize)]
struct LpmActionRequest {
    action: String,
    payload: Value,
}

#[derive(Debug, Serialize)]
struct LpmActionResponse {
    ok: bool,
    data: Option<Value>,
    error: Option<Value>,
}

#[tauri::command]
async fn lpm_action(request: LpmActionRequest) -> Result<LpmActionResponse, String> {
    let action = request.action;
    let payload = serde_json::to_string(&request.payload).map_err(|err| err.to_string())?;
    let output = tauri::async_runtime::spawn_blocking(move || run_lpm_ui_api(&action, &payload))
        .await
        .map_err(|err| format!("lpm-desktop-api task failed: {err}"))??;
    let raw = String::from_utf8_lossy(&output).trim().to_string();

    let parsed: Value = serde_json::from_str(&raw)
        .map_err(|err| format!("lpm-desktop-api returned invalid JSON: {err}"))?;

    Ok(LpmActionResponse {
        ok: parsed.get("ok").and_then(Value::as_bool).unwrap_or(false),
        data: parsed.get("data").cloned(),
        error: parsed.get("error").cloned(),
    })
}

#[tauri::command]
async fn open_path(path: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || open_path_with_system(&path))
        .await
        .map_err(|err| format!("open_path task failed: {err}"))?
}

/// One way to invoke lpm-desktop-api.
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

    if let Ok(bin) = std::env::var("LPM_DESKTOP_API_BIN") {
        if !bin.trim().is_empty() {
            out.push(Candidate::new(
                "$LPM_DESKTOP_API_BIN",
                bin.trim(),
                api_args.clone(),
            ));
        }
    }

    if let Some(exe_dir) = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(PathBuf::from))
    {
        // Tauri renames `bundle.externalBin` files to plain `lpm-desktop-api(.exe)` in
        // the final installer / release output, but keeps the
        // `lpm-desktop-api-{target_triple}{.exe}` naming for `cargo run` / `tauri dev`.
        // Try both, plus a few common siblings (Resources/, _up_/, ...) that
        // various Tauri bundles use on different platforms.
        let triple = env!("TAURI_ENV_TARGET_TRIPLE");
        let names = [
            "lpm-desktop-api.exe".to_string(),
            "lpm-desktop-api".to_string(),
            format!("lpm-desktop-api-{triple}.exe"),
            format!("lpm-desktop-api-{triple}"),
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

fn run_lpm_ui_api(action: &str, payload: &str) -> Result<Vec<u8>, String> {
    let candidates = build_candidates(action);
    let mut errors: Vec<String> = Vec::new();

    for candidate in &candidates {
        match run_candidate(candidate, payload) {
            Ok(output) if output.status.success() => return Ok(output.stdout),
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
        "Unable to run lpm-desktop-api. Tried {} candidates:\n{}\n\nHints:\n  - Run the desktop build scripts so Tauri can bundle the sidecar.\n  - Or set LPM_DESKTOP_API_BIN to the absolute path of lpm-desktop-api(.exe).",
        candidates.len(),
        detail
    ))
}

fn run_candidate(candidate: &Candidate, payload: &str) -> std::io::Result<Output> {
    let mut command = candidate.to_command();
    command
        .env("LPM_DESKTOP_API_PAYLOAD", payload)
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
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .invoke_handler(tauri::generate_handler![lpm_action, open_path])
        .run(tauri::generate_context!())
        .expect("error while running LPM Desktop");
}
