from __future__ import annotations

from pathlib import Path

from lpm.core.config import Config, GitConfig, StateConfig, load_config, write_config


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
