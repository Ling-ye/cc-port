from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_port.core.models import Registry, RegistryItem
from cc_port.core.registry import load_registry, save_registry
from cc_port.services import local_resources
from cc_port.services.local_path_probe import LocalPathProbe, ResourceTreeIssue
from cc_port.services.local_resources import export_claude_plugin, import_local_resource


def test_export_claude_plugin_uses_valid_slug_and_active_local_skills(tmp_path: Path) -> None:
    root = tmp_path / "LingyeAIResources"
    active = root / "skills" / "active-skill"
    removed = root / "skills" / "removed-skill"
    active.mkdir(parents=True)
    removed.mkdir(parents=True)
    (active / "SKILL.md").write_text(
        "---\nname: active-skill\ndescription: Active\n---\n",
        encoding="utf-8",
    )
    (removed / "SKILL.md").write_text(
        "---\nname: removed-skill\ndescription: Removed\n---\n",
        encoding="utf-8",
    )
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="active-skill",
                    kind="skill",
                    source="local",
                    path="skills/active-skill",
                ),
                RegistryItem(
                    name="cursor-only",
                    kind="skill",
                    source="local",
                    path="skills/cursor-only",
                    platforms=["cursor"],
                ),
            ]
        ),
        registry_path,
    )

    output = export_claude_plugin(registry_path=registry_path)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload == {
        "name": "lingye-ai-resources",
        "skills": ["./skills/active-skill"],
    }


@pytest.mark.parametrize("kind", ["instruction", "memory"])
def test_personal_resource_import_rejects_secrets_before_overwriting(
    tmp_path: Path,
    kind: str,
) -> None:
    secret = "ghp_1234567890abcdefghijkl"
    source = tmp_path / "source"
    source.mkdir()
    filename = "CLAUDE.md" if kind == "instruction" else "MEMORY.md"
    (source / filename).write_text(f"token: {secret}\n", encoding="utf-8")
    registry_path = tmp_path / "repo" / "registry.yaml"
    save_registry(Registry(), registry_path)
    destination = registry_path.parent / (
        "instructions" if kind == "instruction" else "memories"
    ) / "personal"
    destination.mkdir(parents=True)
    sentinel = destination / "existing.md"
    sentinel.write_text("keep me\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Secret-like content") as error:
        import_local_resource(
            source,
            kind=kind,  # type: ignore[arg-type]
            name="personal",
            platforms=["claude-code"],
            registry_path=registry_path,
            overwrite=True,
        )

    assert secret not in str(error.value)
    assert sentinel.read_text(encoding="utf-8") == "keep me\n"
    assert not load_registry(registry_path).items


def test_personal_resource_import_rejects_credentialized_urls(tmp_path: Path) -> None:
    source = tmp_path / "CLAUDE.md"
    source.write_text(
        "Use https://local-user:private-password@example.test/resource\n",
        encoding="utf-8",
    )
    registry_path = tmp_path / "repo" / "registry.yaml"
    save_registry(Registry(), registry_path)

    with pytest.raises(ValueError, match="credentialized URL") as error:
        import_local_resource(
            source,
            kind="instruction",
            name="personal",
            platforms=["claude-code"],
            registry_path=registry_path,
        )

    assert "private-password" not in str(error.value)
    assert not (registry_path.parent / "instructions" / "personal").exists()


def test_personal_resource_import_allows_credential_url_placeholder(tmp_path: Path) -> None:
    source = tmp_path / "CLAUDE.md"
    source.write_text(
        "Use https://local-user:${PASSWORD}@example.test/resource\n",
        encoding="utf-8",
    )
    registry_path = tmp_path / "repo" / "registry.yaml"
    save_registry(Registry(), registry_path)

    result = import_local_resource(
        source,
        kind="instruction",
        name="personal",
        platforms=["claude-code"],
        registry_path=registry_path,
    )

    assert result.stored_path.joinpath("CLAUDE.md").read_text(encoding="utf-8") == (
        "Use https://local-user:${PASSWORD}@example.test/resource\n"
    )


def test_direct_import_rejects_root_link_without_copying_target(tmp_path: Path) -> None:
    target = tmp_path / "real-instruction"
    target.mkdir()
    (target / "CLAUDE.md").write_text("# Linked source\n", encoding="utf-8")
    source = tmp_path / "linked-instruction"
    try:
        source.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symbolic links are unavailable: {exc}")
    registry_path = tmp_path / "repo" / "registry.yaml"
    save_registry(Registry(), registry_path)

    with pytest.raises(ValueError, match="regular source path"):
        import_local_resource(
            source,
            kind="instruction",
            name="personal",
            platforms=["claude-code"],
            registry_path=registry_path,
        )

    assert not (registry_path.parent / "instructions" / "personal").exists()


def test_direct_import_rejects_linked_source_ancestor_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "linked-parent"
    parent.mkdir()
    source = parent / "CLAUDE.md"
    source.write_text("# Private instruction\n", encoding="utf-8")
    registry_path = tmp_path / "repo" / "registry.yaml"
    save_registry(Registry(), registry_path)
    real_probe = local_resources.probe_local_path

    def fake_probe(path: Path | str) -> LocalPathProbe:
        candidate = Path(path).absolute()
        if candidate == parent.absolute():
            return LocalPathProbe(
                logical_path=candidate,
                content_path=tmp_path / "private-target",
                path_kind="junction",
                health="ready",
                raw_target="private target must not be exposed",
            )
        return real_probe(candidate)

    monkeypatch.setattr(local_resources, "probe_local_path", fake_probe)

    with pytest.raises(ValueError, match="non-linked source ancestors") as error:
        import_local_resource(
            source,
            kind="instruction",
            name="personal",
            platforms=["claude-code"],
            registry_path=registry_path,
        )

    assert "private target" not in str(error.value)
    assert not (registry_path.parent / "instructions" / "personal").exists()


def test_direct_import_rejects_linked_resource_root_before_overwrite(
    tmp_path: Path,
) -> None:
    source = tmp_path / "CLAUDE.md"
    source.write_text("# Safe instruction\n", encoding="utf-8")
    repo = tmp_path / "repo"
    registry_path = repo / "registry.yaml"
    save_registry(Registry(), registry_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "personal"
    sentinel.write_text("keep me\n", encoding="utf-8")
    try:
        (repo / "instructions").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symbolic links are unavailable: {exc}")

    with pytest.raises(ValueError, match="link or unsupported reparse point"):
        import_local_resource(
            source,
            kind="instruction",
            name="personal",
            platforms=["claude-code"],
            registry_path=registry_path,
            overwrite=True,
        )

    assert sentinel.read_text(encoding="utf-8") == "keep me\n"


@pytest.mark.parametrize(
    ("kind", "filename"),
    [("instruction", "CLAUDE.md"), ("memory", "MEMORY.md")],
)
@pytest.mark.parametrize("issue_code", ["unsupported-reparse", "loop"])
def test_personal_resource_import_rejects_unsafe_tree_before_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    filename: str,
    issue_code: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / filename).write_text("# Safe content\n", encoding="utf-8")
    registry_path = tmp_path / "repo" / "registry.yaml"
    save_registry(Registry(), registry_path)
    destination = registry_path.parent / (
        "instructions" if kind == "instruction" else "memories"
    ) / "personal"
    destination.mkdir(parents=True)
    sentinel = destination / "existing.md"
    sentinel.write_text("keep me\n", encoding="utf-8")
    nested = source / "nested-junction"

    monkeypatch.setattr(
        local_resources,
        "resource_tree_issues",
        lambda _root: [
            ResourceTreeIssue(
                path=nested,
                relative_path="nested-junction",
                code=issue_code,
                detail="private junction target must not be exposed",
            )
        ],
    )

    with pytest.raises(ValueError, match="unsafe linked") as error:
        import_local_resource(
            source,
            kind=kind,  # type: ignore[arg-type]
            name="personal",
            platforms=["claude-code"],
            registry_path=registry_path,
            overwrite=True,
        )

    assert issue_code in str(error.value)
    assert "private junction target" not in str(error.value)
    assert sentinel.read_text(encoding="utf-8") == "keep me\n"
    assert not load_registry(registry_path).items


def test_memory_import_preserves_topics_in_generic_exclusion_directories(
    tmp_path: Path,
) -> None:
    source = tmp_path / "memory"
    (source / "build").mkdir(parents=True)
    (source / "cache").mkdir()
    (source / "MEMORY.md").write_text("# Index\n", encoding="utf-8")
    (source / "build" / "notes.md").write_text("Build notes\n", encoding="utf-8")
    (source / "cache" / "topic.md").write_text("Cache topic\n", encoding="utf-8")
    registry_path = tmp_path / "repo" / "registry.yaml"
    save_registry(Registry(), registry_path)

    result = import_local_resource(
        source,
        kind="memory",
        name="personal-memory",
        platforms=["claude-code"],
        registry_path=registry_path,
    )

    assert (result.stored_path / "build" / "notes.md").read_text(encoding="utf-8") == (
        "Build notes\n"
    )
    assert (result.stored_path / "cache" / "topic.md").read_text(encoding="utf-8") == (
        "Cache topic\n"
    )
