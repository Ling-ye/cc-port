from __future__ import annotations

from pathlib import Path

import pytest

from lpm.core.config import Config, GithubConfig, ResourcesConfig, load_raw_config, write_config
from lpm.infrastructure import git_ops
from lpm.services import resource_binding


@pytest.mark.parametrize(
    ("value", "canonical", "transport"),
    [
        (
            "https://github.com/Example/resources",
            "https://github.com/Example/resources.git",
            "https",
        ),
        (
            "git@github.com:Example/resources.git",
            "git@github.com:Example/resources.git",
            "ssh",
        ),
        (
            "ssh://git@github.com/Example/resources.git",
            "git@github.com:Example/resources.git",
            "ssh",
        ),
    ],
)
def test_parse_github_repo_url_preserves_transport(
    value: str,
    canonical: str,
    transport: str,
) -> None:
    parsed = resource_binding.parse_github_repo_url(value)

    assert parsed.canonical_url == canonical
    assert parsed.transport == transport
    assert parsed.owner == "Example"
    assert parsed.name == "resources"


@pytest.mark.parametrize(
    "value",
    [
        "http://github.com/example/resources",
        "https://gitlab.com/example/resources",
        "https://user:secret@github.com/example/resources",
        "https://github.com/example/resources/tree/main",
        "https://github.com/example/resources?token=secret",
        "ssh://other@github.com/example/resources",
    ],
)
def test_parse_github_repo_url_rejects_unsupported_or_unsafe_urls(value: str) -> None:
    with pytest.raises(ValueError):
        resource_binding.parse_github_repo_url(value)


@pytest.mark.parametrize(
    "repo_url",
    [
        "https://github.com/BoundOwner/resources.git",
        "git@github.com:BoundOwner/resources.git",
    ],
)
def test_configured_github_owner_prefers_bound_repo_url(repo_url: str) -> None:
    cfg = Config(
        github=GithubConfig(owner="LegacyOwner"),
        resources=ResourcesConfig(repo_url=repo_url),
    )

    assert resource_binding.configured_github_owner(cfg) == "BoundOwner"


def test_configured_github_owner_falls_back_to_legacy_owner_when_unbound() -> None:
    cfg = Config(github=GithubConfig(owner="LegacyOwner"))

    assert resource_binding.configured_github_owner(cfg) == "LegacyOwner"


def test_bind_resource_repo_verifies_before_saving_and_resets_old_local_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.toml"
    old_url = "https://github.com/example/old.git"
    old_local = tmp_path / "old-local"
    old_local.mkdir()
    (old_local / "keep.txt").write_text("keep", encoding="utf-8")
    write_config(
        Config(
            resources=ResourcesConfig(
                repo_name="old",
                repo_url=old_url,
                local_path=str(old_local),
                branch="legacy",
            )
        ),
        config_path,
    )
    probes: list[tuple[str, str]] = []

    def fake_probe(url: str, *, transport: str):
        probes.append((url, transport))
        return git_ops.RemoteBindingProbe(
            default_branch="trunk",
            branches=["release", "trunk"],
            remote_empty=False,
        )

    monkeypatch.setattr(resource_binding.git_ops, "probe_remote_binding", fake_probe)

    result = resource_binding.bind_resource_repo(
        "git@github.com:Example/new-resources.git",
        expected_current_repo_url=old_url,
        config_path=config_path,
    )
    saved = load_raw_config(config_path)

    assert probes == [("git@github.com:Example/new-resources.git", "ssh")]
    assert result.repo_url == "git@github.com:Example/new-resources.git"
    assert result.branch == "trunk"
    assert result.replaced_repo_url == old_url
    assert saved.resources.repo_name == "new-resources"
    assert saved.resources.repo_url == result.repo_url
    assert saved.resources.branch == "trunk"
    assert saved.resources.credential_mode == "native"
    assert saved.resources.local_path == ""
    assert (old_local / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_reverify_same_repository_keeps_custom_local_path(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    local_path = tmp_path / "custom"
    current_url = "https://github.com/example/resources.git"
    write_config(
        Config(
            resources=ResourcesConfig(
                repo_name="resources",
                repo_url=current_url,
                local_path=str(local_path),
                credential_mode="native",
            )
        ),
        config_path,
    )
    monkeypatch.setattr(
        resource_binding.git_ops,
        "probe_remote_binding",
        lambda *_args, **_kwargs: git_ops.RemoteBindingProbe("main", ["main"], False),
    )

    resource_binding.bind_resource_repo(
        "https://github.com/example/resources",
        expected_current_repo_url=current_url,
        config_path=config_path,
    )

    assert load_raw_config(config_path).resources.local_path == str(local_path)


def test_failed_or_stale_binding_never_changes_config(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    write_config(Config(), config_path)
    before = config_path.read_text(encoding="utf-8")

    with pytest.raises(resource_binding.StaleResourceBindingError):
        resource_binding.bind_resource_repo(
            "https://github.com/example/resources",
            expected_current_repo_url="https://github.com/example/other.git",
            config_path=config_path,
        )

    monkeypatch.setattr(
        resource_binding.git_ops,
        "probe_remote_binding",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(git_ops.GitError("denied")),
    )
    with pytest.raises(git_ops.GitError, match="denied"):
        resource_binding.bind_resource_repo(
            "https://github.com/example/resources",
            expected_current_repo_url="",
            config_path=config_path,
        )

    assert config_path.read_text(encoding="utf-8") == before
