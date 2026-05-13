use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::PathBuf;
use std::process::Command;

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
    raw: String,
}

#[tauri::command]
async fn lpm_action(request: LpmActionRequest) -> Result<LpmActionResponse, String> {
    let action = request.action;
    let payload = serde_json::to_string(&request.payload).map_err(|err| err.to_string())?;
    let output = tauri::async_runtime::spawn_blocking(move || run_lpm_ui_api(&action, &payload))
        .await
        .map_err(|err| format!("lpm-desktop-api task failed: {err}"))??;
    let raw = String::from_utf8_lossy(&output).trim().to_string();

    let parsed: Value = serde_json::from_str(&raw).map_err(|err| {
        format!("lpm-desktop-api returned invalid JSON: {err}. Raw output: {raw}")
    })?;

    Ok(LpmActionResponse {
        ok: parsed.get("ok").and_then(Value::as_bool).unwrap_or(false),
        data: parsed.get("data").cloned(),
        error: parsed.get("error").cloned(),
        raw,
    })
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

fn build_candidates(action: &str, payload: &str) -> Vec<Candidate> {
    let mut out: Vec<Candidate> = Vec::new();
    let api_args = vec![action.to_string(), payload.to_string()];

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
    let candidates = build_candidates(action, payload);
    let mut errors: Vec<String> = Vec::new();

    for candidate in &candidates {
        match candidate.to_command().output() {
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![lpm_action])
        .run(tauri::generate_context!())
        .expect("error while running LPM Desktop");
}
