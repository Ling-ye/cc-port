from __future__ import annotations

from pathlib import Path


def test_tauri_bridge_uses_stdin_and_does_not_echo_raw_sidecar_output() -> None:
    source = (
        Path(__file__).parents[1] / "desktop" / "src-tauri" / "src" / "lib.rs"
    ).read_text(encoding="utf-8")

    assert ".stdin(Stdio::piped())" in source
    assert '.env("LPM_DESKTOP_API_PAYLOAD", payload)' in source
    assert "let api_args = vec![action.to_string()]" in source
    assert "payload.to_string()" not in source
    assert "Raw output" not in source
    assert "raw:" not in source


def test_tauri_response_has_no_raw_field() -> None:
    source = (
        Path(__file__).parents[1] / "desktop" / "src-tauri" / "src" / "lib.rs"
    ).read_text(encoding="utf-8")
    response_block = source.split("struct LpmActionResponse", 1)[1].split("}", 1)[0]

    assert "raw" not in response_block
