#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::Value;
use std::path::PathBuf;
use tauri_plugin_shell::ShellExt;

const OPERATIONS: &[&str] = &[
    "info",
    "scan_devices",
    "probe_wireless",
    "get_models",
    "get_releases",
    "prepare_update",
    "get_job",
    "install_update",
    "check_recovery",
    "recover_update",
    "export_diagnostics",
    "open_official_updater",
    "get_recovery_access",
    "setup_recovery_access",
    "check_app_update",
    "apply_app_update",
    "recover_app_update",
];

fn request_arguments(operation: &str, parameters: &Value) -> Result<Vec<String>, String> {
    if !OPERATIONS.contains(&operation) {
        return Err("This operation is not available through the desktop application.".into());
    }
    if !parameters.is_object() {
        return Err("Updater parameters must be an object.".into());
    }
    let encoded = serde_json::to_string(parameters).map_err(|_| "Invalid updater parameters.")?;
    if encoded.len() > 65_536 {
        return Err("Updater parameters exceed the supported size.".into());
    }
    Ok(vec![
        "api".into(),
        operation.into(),
        "--params".into(),
        encoded,
        "--api-version".into(),
        "1".into(),
    ])
}

fn decode_response(stdout: &[u8]) -> Result<Value, String> {
    let response: Value = serde_json::from_slice(stdout)
        .map_err(|_| "The updater returned an unreadable response.")?;
    if response.get("api_version").and_then(Value::as_u64) != Some(1)
        || response.get("ok").and_then(Value::as_bool).is_none()
    {
        return Err("The desktop application and updater have incompatible APIs.".into());
    }
    Ok(response)
}

fn desktop_application_path() -> Option<PathBuf> {
    #[cfg(target_os = "linux")]
    {
        std::env::var_os("APPIMAGE").map(PathBuf::from)
    }
    #[cfg(target_os = "macos")]
    {
        std::env::current_exe()
            .ok()?
            .ancestors()
            .find(|path| path.extension().is_some_and(|extension| extension == "app"))
            .map(PathBuf::from)
    }
    #[cfg(target_os = "windows")]
    {
        std::env::current_exe().ok()?.parent().map(PathBuf::from)
    }
}

#[tauri::command]
async fn updater_request(
    app: tauri::AppHandle,
    operation: String,
    parameters: Value,
) -> Result<Value, String> {
    let arguments = request_arguments(&operation, &parameters)?;
    let mut command = app
        .shell()
        .sidecar("eightbitdo")
        .map_err(|_| "The packaged updater could not be found. Reinstall this application.")?
        .args(arguments)
        .env("EIGHTBITDO_FRONTEND", "desktop")
        .env("EIGHTBITDO_DESKTOP_PID", std::process::id().to_string());
    if let Some(path) = desktop_application_path() {
        command = command.env("EIGHTBITDO_DESKTOP_APP_PATH", path);
    } else {
        command = command.env("EIGHTBITDO_DESKTOP_APP_PATH", "");
    }
    let output = command
        .output()
        .await
        .map_err(|_| "The packaged updater could not be started.")?;
    if !matches!(output.status.code(), Some(0 | 1)) {
        return Err("The updater process stopped unexpectedly. Existing firmware jobs can be checked after reopening the application.".into());
    }
    decode_response(&output.stdout)
}

#[tauri::command]
fn quit_application(app: tauri::AppHandle) {
    app.exit(0);
}

fn main() {
    let smoke_test = std::env::args_os().any(|argument| argument == "--smoke-test");
    tauri::Builder::default()
        .setup(move |app| {
            if smoke_test {
                let handle = app.handle().clone();
                let expected_version = app
                    .package_info()
                    .version
                    .to_string()
                    .replace("-alpha.", "a")
                    .replace("-beta.", "b")
                    .replace("-rc.", "rc");
                tauri::async_runtime::spawn(async move {
                    let result =
                        updater_request(handle.clone(), "info".into(), serde_json::json!({})).await;
                    let exit_code = match result {
                        Ok(response)
                            if response["ok"] == true
                                && response["result"]["app_version"] == expected_version =>
                        {
                            println!("Desktop sidecar API smoke test passed.");
                            0
                        }
                        other => {
                            eprintln!("Desktop sidecar API smoke test failed: {other:?}");
                            1
                        }
                    };
                    handle.exit(exit_code);
                });
            } else {
                tauri::WebviewWindowBuilder::from_config(app, &app.config().app.windows[0])?
                    .build()?;
            }
            Ok(())
        })
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![updater_request, quit_application])
        .run(tauri::generate_context!())
        .expect("Could not start 8BitDo Updater");
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn shell_and_private_worker_operations_are_rejected() {
        for operation in [
            "--worker",
            "worker",
            "sh",
            "powershell",
            "getattr",
            "../eightbitdo",
        ] {
            assert!(request_arguments(operation, &json!({})).is_err());
        }
        assert!(request_arguments("info", &json!(["unexpected"])).is_err());
    }

    #[test]
    fn user_values_remain_one_json_argument() {
        let parameters = json!({"job_id": "x; echo unsafe $(command)", "confirmed": true});
        let args = request_arguments("install_update", &parameters).unwrap();
        assert_eq!(
            args,
            vec![
                "api",
                "install_update",
                "--params",
                &parameters.to_string(),
                "--api-version",
                "1"
            ]
        );
    }

    #[test]
    fn errors_are_returned_as_envelopes_and_versions_are_checked() {
        assert!(decode_response(br#"{"api_version":1,"ok":false,"error":{"code":"confirmation_required","message":"Review first"}}"#).is_ok());
        assert!(decode_response(br#"{"api_version":2,"ok":true,"result":{}}"#).is_err());
        assert!(decode_response(b"not json").is_err());
    }
}
