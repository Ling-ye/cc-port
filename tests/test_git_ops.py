from __future__ import annotations

import subprocess

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
        assert args[0][:4] == ["git", "ls-remote", "--symref", "https://example.test/repo.git"]
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
