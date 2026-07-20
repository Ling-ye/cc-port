from __future__ import annotations

from pathlib import Path

from lpm.core.config import (
    Config,
    GitConfig,
    GithubConfig,
    ResourcesConfig,
    StateConfig,
    load_config,
    resource_repo_auth_token,
    write_config,
)


def test_state_policy_round_trips_through_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    write_config(
        Config(
            git=GitConfig(executable="D:/Git/cmd/git.exe"),
            state=StateConfig(
                lock_timeout_seconds=2.5,
                retention_days=14,
                keep_latest_operations=7,
                max_backup_mb=512,
            )
        ),
        path,
    )

    loaded = load_config(path)

    assert loaded.git.executable == "D:/Git/cmd/git.exe"
    assert loaded.state.lock_timeout_seconds == 2.5
    assert loaded.state.retention_days == 14
    assert loaded.state.keep_latest_operations == 7
    assert loaded.state.max_backup_mb == 512


def test_resource_credential_mode_round_trips_and_native_ignores_global_token(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    write_config(
        Config(
            github=GithubConfig(token="expired-global-token"),
            resources=ResourcesConfig(credential_mode="native"),
        ),
        path,
    )

    loaded = load_config(path)

    assert loaded.resources.credential_mode == "native"
    assert resource_repo_auth_token(loaded) is None


def test_legacy_resource_credential_mode_defaults_to_auto(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[github]\n"
        'token = "legacy-token"\n'
        "[resources]\n"
        'repo_name = "resources"\n',
        encoding="utf-8",
    )

    loaded = load_config(path)

    assert loaded.resources.credential_mode == "auto"
    assert resource_repo_auth_token(loaded) == "legacy-token"
