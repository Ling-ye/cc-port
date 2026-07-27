from __future__ import annotations

from pathlib import Path


def test_tauri_bridge_uses_stdin_and_does_not_echo_raw_sidecar_output() -> None:
    source = (
        Path(__file__).parents[1] / "desktop" / "src-tauri" / "src" / "lib.rs"
    ).read_text(encoding="utf-8")

    assert ".stdin(Stdio::piped())" in source
    assert '.env("CC_PORT_DESKTOP_API_PAYLOAD", payload)' in source
    assert "let api_args = vec![action.to_string()]" in source
    assert "payload.to_string()" not in source
    assert "Raw output" not in source
    assert "raw:" not in source


def test_tauri_response_has_no_raw_field() -> None:
    source = (
        Path(__file__).parents[1] / "desktop" / "src-tauri" / "src" / "lib.rs"
    ).read_text(encoding="utf-8")
    response_block = source.split("struct CcPortActionResponse", 1)[1].split("}", 1)[0]

    assert "raw" not in response_block


def test_tauri_keeps_single_instance_without_oauth_deep_link_permissions() -> None:
    root = Path(__file__).parents[1]
    source = (root / "desktop" / "src-tauri" / "src" / "lib.rs").read_text(
        encoding="utf-8"
    )
    config = (root / "desktop" / "src-tauri" / "tauri.conf.json").read_text(
        encoding="utf-8"
    )
    capability = (
        root / "desktop" / "src-tauri" / "capabilities" / "default.json"
    ).read_text(encoding="utf-8")

    assert "tauri_plugin_single_instance::init" in source
    assert "tauri_plugin_deep_link" not in source
    assert '"deep-link"' not in config
    assert '"deep-link:default"' not in capability
    assert "https://github.com/login/oauth/authorize*" not in capability
    assert "https://git-scm.com/download/win" in capability
    assert "https://github.com/git-ecosystem/git-credential-manager*" in capability
