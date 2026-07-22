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


def test_remote_branches_falls_back_to_ssh_for_github_without_native_https_credentials(
    monkeypatch,
) -> None:
    calls = 0

    def fake_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert args[0][3] == "https://github.com/Ling-ye/LingyeAIResources.git"
            assert kwargs["env"]["GCM_INTERACTIVE"] == "never"
            return subprocess.CompletedProcess(args[0], 1, stdout="", stderr="no credentials")
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
    assert calls == 2


def test_remote_branches_drops_rejected_token_before_native_fallback(monkeypatch) -> None:
    environments: list[dict[str, str]] = []

    def fake_run(args, **kwargs):
        environments.append(kwargs["env"])
        if len(environments) == 1:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="bad token")
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="ref: refs/heads/main\tHEAD\nabc\trefs/heads/main\n",
            stderr="",
        )

    monkeypatch.setattr(git_ops.subprocess, "run", fake_run)

    default_branch, branches = git_ops.remote_branches(
        "https://github.com/example/resources.git",
        token="expired-token",
    )

    assert default_branch == "main"
    assert branches == ["main"]
    assert "GIT_CONFIG_COUNT" in environments[0]
    assert "GIT_CONFIG_COUNT" not in environments[1]


def test_probe_remote_binding_allows_gcm_only_for_explicit_binding(monkeypatch) -> None:
    subprocess_calls: list[tuple[list[str], dict[str, str]]] = []
    local_calls: list[list[str]] = []

    def fake_subprocess_run(args, **kwargs):
        subprocess_calls.append((args, kwargs["env"]))
        if "ls-remote" in args:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=(
                    "ref: refs/heads/main\tHEAD\n"
                    "abc123\tHEAD\n"
                    "abc123\trefs/heads/main\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    def fake_run(args, **_kwargs):
        local_calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(git_ops.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(git_ops, "_run", fake_run)

    result = git_ops.probe_remote_binding(
        "https://github.com/example/resources.git",
        transport="https",
    )

    assert result.default_branch == "main"
    assert result.branches == ["main"]
    assert result.remote_empty is False
    assert subprocess_calls[0][1]["GCM_INTERACTIVE"] == "auto"
    assert subprocess_calls[0][1]["GIT_TERMINAL_PROMPT"] == "0"
    assert "ls-remote" in subprocess_calls[0][0]
    assert "--dry-run" in subprocess_calls[1][0]
    assert "push" in subprocess_calls[1][0]
    assert not any("clone" in call or "fetch" in call or "pull" in call for call in local_calls)


def test_probe_remote_binding_keeps_ssh_non_interactive(monkeypatch) -> None:
    environments: list[dict[str, str]] = []

    def fake_subprocess_run(args, **kwargs):
        environments.append(kwargs["env"])
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(git_ops.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(
        git_ops,
        "_run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
    )

    result = git_ops.probe_remote_binding(
        "git@github.com:example/resources.git",
        transport="ssh",
    )

    assert result.remote_empty is True
    assert all(env["GCM_INTERACTIVE"] == "never" for env in environments)
    assert all(env["GIT_SSH_COMMAND"].startswith("ssh -o BatchMode=yes") for env in environments)


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
    assert ["merge", "--ff-only", "FETCH_HEAD"] in calls
    assert not any("reset" in call for call in calls)


def test_pull_with_tag_fetches_and_checks_out_fetch_head_detached(monkeypatch, tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_run(args, cwd=None, check=True, extra_env=None):
        calls.append(args)
        if args[:3] == ["show-ref", "--verify", "--quiet"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(git_ops, "_run", fake_run)

    git_ops.pull(tmp_path, ref="v1.2.3")

    assert ["fetch", "origin", "v1.2.3"] in calls
    assert ["checkout", "--detach", "FETCH_HEAD"] in calls
    assert not any(call[:1] == ["merge"] for call in calls)


def test_pull_with_commit_fetches_exact_sha_and_checks_it_out_detached(
    monkeypatch,
    tmp_path,
) -> None:
    commit = "0123456789abcdef0123456789abcdef01234567"
    calls: list[list[str]] = []

    def fake_run(args, cwd=None, check=True, extra_env=None):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(git_ops, "_run", fake_run)

    git_ops.pull(tmp_path, ref=commit)

    assert calls == [
        ["fetch", "origin", commit],
        ["checkout", "--detach", commit],
    ]
    assert all("main" not in call for call in calls)


def test_clone_with_commit_fetches_only_exact_sha_without_branch_clone(
    monkeypatch,
    tmp_path,
) -> None:
    commit = "0123456789abcdef0123456789abcdef01234567"
    destination = tmp_path / "checkout"
    calls: list[tuple[list[str], Path | None]] = []

    def fake_run(args, cwd=None, check=True, extra_env=None):
        calls.append((args, cwd))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(git_ops, "_run", fake_run)

    git_ops.clone(
        "https://github.com/example/demo.git",
        destination,
        ref=commit,
    )

    assert calls == [
        (["init", "--quiet", str(destination)], None),
        (
            ["remote", "add", "origin", "https://github.com/example/demo.git"],
            destination,
        ),
        (["fetch", "origin", commit], destination),
        (["checkout", "--detach", commit], destination),
    ]
    assert not any("clone" in args or "--branch" in args for args, _cwd in calls)


def test_clone_with_legacy_branch_keeps_branch_clone(monkeypatch, tmp_path) -> None:
    destination = tmp_path / "checkout"
    calls: list[list[str]] = []

    def fake_run(args, cwd=None, check=True, extra_env=None):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(git_ops, "_run", fake_run)

    git_ops.clone(
        "https://github.com/example/demo.git",
        destination,
        ref="release",
    )

    assert calls == [
        [
            "clone",
            "--branch",
            "release",
            "https://github.com/example/demo.git",
            str(destination),
        ]
    ]


def test_clone_with_missing_commit_fails_without_default_branch_fallback(
    monkeypatch,
    tmp_path,
) -> None:
    commit = "f" * 40
    destination = tmp_path / "checkout"
    calls: list[list[str]] = []

    def fake_run(args, cwd=None, check=True, extra_env=None):
        calls.append(args)
        if args[:1] == ["fetch"]:
            raise git_ops.GitError("requested commit is unavailable")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(git_ops, "_run", fake_run)

    with pytest.raises(git_ops.GitError, match="requested commit is unavailable"):
        git_ops.clone(
            "https://github.com/example/demo.git",
            destination,
            ref=commit,
        )

    assert ["fetch", "origin", commit] in calls
    assert not any(call[:1] == ["checkout"] for call in calls)
    assert not any("main" in call for call in calls)


def test_remote_url_commit_prefers_peeled_annotated_tag(monkeypatch) -> None:
    tag_object = "1" * 40
    tag_commit = "2" * 40
    calls: list[list[str]] = []

    def fake_run(args, cwd=None, check=True, extra_env=None):
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=(
                f"{tag_object}\trefs/tags/v1.2.3\n"
                f"{tag_commit}\trefs/tags/v1.2.3^{{}}\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(git_ops, "_run", fake_run)

    resolved = git_ops.remote_url_commit(
        "https://github.com/example/demo.git",
        "v1.2.3",
    )

    assert resolved == tag_commit
    assert calls == [
        [
            "ls-remote",
            "https://github.com/example/demo.git",
            "refs/heads/v1.2.3",
            "refs/tags/v1.2.3",
            "refs/tags/v1.2.3^{}",
        ]
    ]


def test_remote_url_commit_accepts_full_sha_without_ls_remote(monkeypatch) -> None:
    commit = "ABCDEF0123456789ABCDEF0123456789ABCDEF01"
    monkeypatch.setattr(
        git_ops,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a full commit SHA must not be used as an ls-remote ref")
        ),
    )

    assert git_ops.remote_url_commit("https://github.com/example/demo.git", commit) == commit.lower()


def test_remote_commit_status_uses_local_pinned_object_without_ls_remote(
    monkeypatch,
    tmp_path,
) -> None:
    commit = "a" * 40
    calls: list[list[str]] = []

    def fake_run(args, cwd=None, check=True, extra_env=None):
        calls.append(args)
        if args == ["rev-parse", "--verify", f"{commit}^{{commit}}"]:
            return subprocess.CompletedProcess(args, 0, stdout=f"{commit}\n", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(git_ops, "_run", fake_run)

    assert git_ops.remote_commit(tmp_path, ref=commit) == commit
    assert calls == [["rev-parse", "--verify", f"{commit}^{{commit}}"]]


def test_probe_remote_commit_fetches_exact_sha_without_default_branch(
    monkeypatch,
) -> None:
    commit = "0123456789abcdef0123456789abcdef01234567"
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(git_ops, "_git_executable", lambda: "git")
    monkeypatch.setattr(git_ops.subprocess, "run", fake_run)

    assert git_ops.probe_remote(
        "https://github.com/example/demo.git",
        commit,
    )

    assert any("fetch" in call and commit in call for call in calls)
    assert any("cat-file" in call and f"{commit}^{{commit}}" in call for call in calls)
    assert not any("ls-remote" in call or "main" in call for call in calls)


def test_real_git_branch_tag_and_pinned_commit_compatibility(tmp_path) -> None:
    if git_ops.discover_git_executable().path is None:
        pytest.skip("Git is not installed")

    source = tmp_path / "source"
    remote = tmp_path / "remote.git"
    branch_checkout = tmp_path / "branch-checkout"
    tag_checkout = tmp_path / "tag-checkout"
    pinned_checkout = tmp_path / "pinned-checkout"
    missing_checkout = tmp_path / "missing-checkout"

    git_ops.init_repo(source)
    payload = source / "payload.txt"
    payload.write_text("first", encoding="utf-8")
    git_ops.add_all(source)
    git_ops.commit(source, "first")
    first_commit = git_ops.head_commit(source)
    assert first_commit is not None
    git_ops._run(
        [
            "-c",
            "user.email=lpm@local",
            "-c",
            "user.name=LingyePluginMarketplace",
            "tag",
            "-a",
            "v1.0.0",
            "-m",
            "v1",
        ],
        cwd=source,
    )
    git_ops._run(["clone", "--bare", str(source), str(remote)])

    git_ops.clone(str(remote), branch_checkout, ref="main")
    payload.write_text("second", encoding="utf-8")
    git_ops.add_all(source)
    git_ops.commit(source, "second")
    second_commit = git_ops.head_commit(source)
    assert second_commit is not None and second_commit != first_commit
    git_ops._run(["push", str(remote), "main"], cwd=source)

    git_ops.pull(branch_checkout, ref="main")
    assert git_ops.head_commit(branch_checkout) == second_commit
    assert (branch_checkout / "payload.txt").read_text(encoding="utf-8") == "second"

    assert git_ops.remote_url_commit(str(remote), "v1.0.0") == first_commit
    git_ops.clone(str(remote), tag_checkout, ref="v1.0.0")
    git_ops.pull(tag_checkout, ref="v1.0.0")
    assert git_ops.head_commit(tag_checkout) == first_commit

    git_ops._run(["tag", "-d", "v1.0.0"], cwd=source)
    git_ops._run(["push", str(remote), ":refs/tags/v1.0.0"], cwd=source)
    git_ops.clone(str(remote), pinned_checkout, ref=first_commit)
    assert git_ops.head_commit(pinned_checkout) == first_commit
    assert (pinned_checkout / "payload.txt").read_text(encoding="utf-8") == "first"

    missing_commit = "0" * 40
    with pytest.raises(git_ops.GitError):
        git_ops.clone(str(remote), missing_checkout, ref=missing_commit)
    assert not (missing_checkout / "payload.txt").exists()


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
