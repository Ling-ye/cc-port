from __future__ import annotations

from lpm.git_ops import with_token


def test_with_token_https() -> None:
    out = with_token("https://github.com/foo/bar.git", "tok123")
    assert out == "https://x-access-token:tok123@github.com/foo/bar.git"


def test_with_token_skips_ssh() -> None:
    assert with_token("git@github.com:foo/bar.git", "tok") == "git@github.com:foo/bar.git"


def test_with_token_skips_when_empty() -> None:
    assert with_token("https://github.com/foo/bar.git", "") == "https://github.com/foo/bar.git"
