from __future__ import annotations

import os
from pathlib import Path

import pytest

from cc_port.services import local_path_probe, resource_discovery
from cc_port.services.local_path_probe import (
    WINDOWS_REPARSE_TAG_LX_SYMLINK,
    LocalPathProbe,
    is_known_canonical_link_target,
    probe_local_path,
)


def _skill(path: Path, name: str) -> None:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name}\n---\n",
        encoding="utf-8",
    )


def test_probe_classifies_inaccessible_wsl_reparse_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "writing-great-skills"

    def inaccessible(_path: Path) -> os.stat_result:
        raise OSError(1920, "The system cannot access the file")

    monkeypatch.setattr(Path, "lstat", inaccessible)
    monkeypatch.setattr(
        local_path_probe,
        "_windows_reparse_tag",
        lambda _path: WINDOWS_REPARSE_TAG_LX_SYMLINK,
    )

    probe = probe_local_path(path)

    assert probe.path_kind == "wsl-symlink"
    assert probe.health == "unsupported-wsl"
    assert probe.content_path is None
    assert probe.reparse_tag_hex == "0xA000001D"


def test_discovery_keeps_scanning_after_blocked_wsl_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = tmp_path / ".codex" / "skills"
    good = skills / "good-skill"
    blocked = skills / "writing-great-skills"
    _skill(good, "good-skill")
    blocked.mkdir()
    real_probe = resource_discovery.probe_local_path

    def probe(path: Path | str) -> LocalPathProbe:
        candidate = Path(path).absolute()
        if candidate == blocked.absolute():
            return LocalPathProbe(
                logical_path=candidate,
                content_path=None,
                path_kind="wsl-symlink",
                health="unsupported-wsl",
                reparse_tag=WINDOWS_REPARSE_TAG_LX_SYMLINK,
                problem="WSL link is unsupported.",
            )
        return real_probe(candidate)

    monkeypatch.setattr(resource_discovery, "probe_local_path", probe)

    resources = resource_discovery.discover_resources(
        scope="directory",
        root_path=skills,
    )

    assert [(item.name_hint, item.status) for item in resources] == [
        ("good-skill", "ready"),
        ("writing-great-skills", "blocked"),
    ]
    blocked_resource = resources[1]
    assert blocked_resource.path_kind == "wsl-symlink"
    assert blocked_resource.reparse_tag == "0xA000001D"


def test_inaccessible_nested_skill_marker_blocks_only_that_resource(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = tmp_path / ".codex" / "skills"
    good = skills / "good-skill"
    blocked = skills / "blocked-skill"
    _skill(good, "good-skill")
    _skill(blocked, "blocked-skill")
    blocked_marker = blocked / "SKILL.md"
    real_probe = resource_discovery.probe_local_path

    def probe(path: Path | str) -> LocalPathProbe:
        candidate = Path(path).absolute()
        if candidate == blocked_marker.absolute():
            return LocalPathProbe(
                logical_path=candidate,
                content_path=None,
                path_kind="wsl-symlink",
                health="unsupported-wsl",
                reparse_tag=WINDOWS_REPARSE_TAG_LX_SYMLINK,
                problem="WSL link is unsupported.",
            )
        return real_probe(candidate)

    monkeypatch.setattr(resource_discovery, "probe_local_path", probe)
    monkeypatch.setattr(local_path_probe, "probe_local_path", probe)

    resources = resource_discovery.discover_resources(
        scope="directory",
        root_path=skills,
    )

    assert [(item.name_hint, item.status) for item in resources] == [
        ("blocked-skill", "blocked"),
        ("good-skill", "ready"),
    ]
    assert "SKILL.md" in resources[0].blockers[0]


def test_native_root_symlink_uses_canonical_content_and_keeps_logical_path(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / ".agents" / "skills" / "demo"
    logical = tmp_path / ".claude" / "skills" / "demo"
    _skill(canonical, "demo")
    logical.parent.mkdir(parents=True)
    try:
        logical.symlink_to(canonical, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Native symlink creation is unavailable: {exc}")

    resources = resource_discovery.discover_resources(
        scope="directory",
        root_path=logical.parent,
    )

    assert len(resources) == 1
    resource = resources[0]
    assert resource.path == logical.absolute()
    assert resource.content_path == canonical.resolve()
    assert resource.path_kind == "symlink"
    assert resource.link_target_trusted is True
    assert is_known_canonical_link_target(probe_local_path(logical)) is True


def test_nested_symlink_blocks_resource_upload_candidate(tmp_path: Path) -> None:
    resource_path = tmp_path / "skills" / "demo"
    external = tmp_path / "external.txt"
    _skill(resource_path, "demo")
    external.write_text("external", encoding="utf-8")
    nested = resource_path / "nested.txt"
    try:
        nested.symlink_to(external)
    except OSError as exc:
        pytest.skip(f"Native symlink creation is unavailable: {exc}")

    resources = resource_discovery.discover_resources(
        scope="directory",
        root_path=resource_path.parent,
    )

    assert len(resources) == 1
    assert resources[0].status == "blocked"
    assert "nested.txt" in resources[0].blockers[0]
