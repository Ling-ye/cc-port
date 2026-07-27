from __future__ import annotations

from pathlib import Path

from cc_port.core.config import (
    DEFAULT_RESOURCE_REPO_NAME,
    LEGACY_RESOURCE_REPO_NAME,
    Config,
    GitConfig,
    GithubConfig,
    PluginProjectConfig,
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


def test_plugin_projects_round_trip_without_entering_registry(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    write_config(
        Config(
            plugin_projects=[
                PluginProjectConfig(
                    id="project-123",
                    path="D:/Code/demo",
                    repo="https://github.com/example/demo.git",
                    subdir="apps/web",
                )
            ]
        ),
        path,
    )

    loaded = load_config(path)

    assert loaded.plugin_projects == [
        PluginProjectConfig(
            id="project-123",
            path="D:/Code/demo",
            repo="https://github.com/example/demo.git",
            subdir="apps/web",
        )
    ]


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
    assert loaded.github.repo_prefix == "cursor-skill-"
    assert loaded.github.default_private is False
    assert [(profile.name, profile.enabled) for profile in loaded.platforms.profiles] == [
        ("cursor", True)
    ]


def test_existing_config_without_resource_name_keeps_legacy_default(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[github]\nowner = \"example\"\n", encoding="utf-8")

    loaded = load_config(path)

    assert loaded.resources.repo_name == LEGACY_RESOURCE_REPO_NAME


def test_new_config_uses_private_cc_port_defaults_and_enables_complete_presets(
    tmp_path: Path,
) -> None:
    loaded = load_config(tmp_path / "missing.toml")

    assert loaded.github.owner == ""
    assert loaded.github.repo_prefix == "cc-port-"
    assert loaded.github.default_private is True
    assert loaded.resources.repo_name == DEFAULT_RESOURCE_REPO_NAME == "cc-port-resources"
    assert loaded.resources.branch == "main"
    assert loaded.resources.credential_mode == "native"
    assert {profile.name for profile in loaded.platforms.enabled()} == {
        "codex",
        "claude-code",
        "cursor",
        "windsurf",
        "opencode",
    }
    assert all(
        profile.skills_dir or profile.mcp_json or profile.rules_dir or profile.plugins_dir
        for profile in loaded.platforms.enabled()
    )
