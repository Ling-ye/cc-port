from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from lpm.infrastructure import git_ops


def test_remote_branches_parses_default_and_named_branches(monkeypatch) -> None:
    output = "\n".join(
        [
            "ref: refs/heads/main\tHEAD",
            "abc123\tHEAD",
            "def456\trefs/heads/dev",
            "abc123\trefs/heads/main",
        ]
    )

    def fake_run(*args, **kwargs):
        assert Path(args[0][0]).name in {"git", "git.exe"}
        assert args[0][1:4] == ["ls-remote", "--symref", "https://example.test/repo.git"]
        assert kwargs["timeout"] == 9
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert kwargs["env"]["GCM_INTERACTIVE"] == "never"
        return subprocess.CompletedProcess(args[0], 0, stdout=output, stderr="")

    monkeypatch.setattr(git_ops.subprocess, "run", fake_run)

    default_branch, branches = git_ops.remote_branches(
        "https://example.test/repo.git",
        timeout=9,
    )

    assert default_branch == "main"
    assert branches == ["dev", "main"]


def test_remote_branches_uses_ssh_for_github_without_token(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        assert args[0][3] == "git@github.com:Ling-ye/LingyeAIResources.git"
        assert kwargs["env"]["GIT_SSH_COMMAND"].startswith("ssh -o BatchMode=yes")
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="ref: refs/heads/main\tHEAD\nabc\trefs/heads/main\n",
            stderr="",
        )

    monkeypatch.setattr(git_ops.subprocess, "run", fake_run)

    default_branch, branches = git_ops.remote_branches(
        "https://github.com/Ling-ye/LingyeAIResources.git"
    )

    assert default_branch == "main"
    assert branches == ["main"]


def test_pull_with_ref_fetches_and_fast_forwards_without_hard_reset(monkeypatch, tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_run(args, cwd=None, check=True, extra_env=None):
        calls.append(args)
        if args[:2] == ["rev-parse", "--verify"]:
            return subprocess.CompletedProcess(args, 0, stdout="abc\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(git_ops, "_run", fake_run)

    git_ops.pull(tmp_path, ref="main")

    assert ["fetch", "origin", "main"] in calls
    assert ["checkout", "main"] in calls
    assert ["merge", "--ff-only", "origin/main"] in calls
    assert not any("reset" in call for call in calls)


def test_token_transport_uses_process_environment_without_raw_token_or_temp_file(
    monkeypatch,
) -> None:
    token = "ghp space&pipe|percent%quote'\""
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)

    env = git_ops._token_env(token)

    assert token not in json.dumps(env)
    assert "GIT_ASKPASS" not in env
    assert "_LPM_ASKPASS_TMP" not in env
    assert env["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    assert env["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")


def test_probe_remote_keeps_token_out_of_arguments(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs["env"]))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(git_ops.subprocess, "run", fake_run)
    token = "private-token&with-specials"

    assert git_ops.probe_remote(
        "https://user:old-secret@example.test/repo.git",
        "main",
        token=token,
    )

    args, env = calls[0]
    assert args[3] == "https://example.test/repo.git"
    assert all(token not in arg and "old-secret" not in arg for arg in args)
    assert token not in json.dumps(env)


def test_set_remote_strips_url_credentials(monkeypatch, tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_run(args, cwd=None, check=True, extra_env=None):
        calls.append(args)
        if args == ["remote"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(git_ops, "_run", fake_run)

    git_ops.set_remote(
        tmp_path,
        "origin",
        "https://user:secret@example.test/repo.git",
    )

    assert calls[-1] == [
        "remote",
        "add",
        "origin",
        "https://example.test/repo.git",
    ]


def test_git_error_redacts_url_userinfo(monkeypatch, tmp_path) -> None:
    def fake_run(*_args, **_kwargs):
        raise subprocess.CalledProcessError(
            1,
            ["git"],
            stderr="fatal: https://user:secret@example.test/repo.git failed",
        )

    monkeypatch.setattr(git_ops.subprocess, "run", fake_run)

    with pytest.raises(git_ops.GitError) as error:
        git_ops._run(
            ["clone", "https://user:secret@example.test/repo.git"],
            cwd=tmp_path,
        )

    assert "secret" not in str(error.value)
    assert "https://***@example.test/repo.git" in str(error.value)


def test_git_runtime_uses_configured_path_before_path_lookup(
    tmp_path,
    monkeypatch,
) -> None:
    executable = tmp_path / "git.exe"
    executable.write_bytes(b"")
    monkeypatch.setattr(git_ops.shutil, "which", lambda _name: None)

    runtime = git_ops.discover_git_executable(str(executable))

    assert runtime.path == executable.resolve()
    assert runtime.source == "configured"


def test_git_runtime_reports_missing_configured_path(monkeypatch) -> None:
    monkeypatch.setattr(git_ops.shutil, "which", lambda _name: None)

    runtime = git_ops.discover_git_executable("Z:/missing/git.exe")

    assert runtime.path is None
    assert runtime.source == "configured-missing"
