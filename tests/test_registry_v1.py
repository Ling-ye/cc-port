from __future__ import annotations

from pathlib import Path

import pytest

from cc_port.core.models import (
    AmbiguousResourceNameError,
    CcPortResourceSettings,
    CcPortSettings,
    ExternalSource,
    Registry,
    RegistryItem,
    RegistryResource,
    ResourceKey,
)
from cc_port.core.registry import (
    UnsupportedRegistryVersionError,
    canonical_registry_text,
    load_registry,
    save_registry,
)


def test_legacy_registry_is_not_loaded_as_v1(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    path.write_text("version: 7\nitems: []\n", encoding="utf-8")

    with pytest.raises(UnsupportedRegistryVersionError):
        load_registry(path)


def test_registry_uses_kind_and_name_as_unique_key() -> None:
    registry = Registry(
        resources=[
            RegistryResource(kind="skill", name="demo", path="skills/demo"),
            RegistryResource(kind="prompt", name="demo", path="prompts/demo"),
        ]
    )

    assert registry.get("demo", "skill").path == "skills/demo"
    assert registry.get_key(ResourceKey(kind="prompt", name="demo")).path == "prompts/demo"
    with pytest.raises(AmbiguousResourceNameError):
        registry.get("demo")

    registry.upsert(
        RegistryItem(name="demo", kind="skill", source="local", path="skills/demo-v2")
    )
    assert len(registry.resources) == 2
    assert registry.get("demo", "skill").path == "skills/demo-v2"

    removed = registry.remove("demo", "prompt")
    assert removed is not None and removed.kind == "prompt"
    assert registry.get("demo").kind == "skill"


def test_registry_remove_cleans_cc_port_resource_settings() -> None:
    registry = Registry(
        items=[
            RegistryItem(
                name="demo",
                kind="skill",
                source="local",
                path="skills/demo",
                platforms=["claude-code"],
            )
        ]
    )

    assert "skill:demo" in registry.cc_port.resources

    removed = registry.remove("demo", "skill")

    assert removed is not None
    assert registry.resources == []
    assert "skill:demo" not in registry.cc_port.resources


def test_saving_after_last_overlay_resource_removal_deletes_overlay(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry.yaml"
    registry = Registry(
        items=[
            RegistryItem(
                name="demo",
                kind="skill",
                source="local",
                path="skills/demo",
                platforms=["claude-code"],
            )
        ]
    )
    save_registry(registry, path)
    overlay = tmp_path / "cc-port.yaml"
    assert overlay.is_file()

    registry.remove("demo", "skill")
    save_registry(registry, path)

    assert not overlay.exists()


@pytest.mark.parametrize(
    "resource",
    [
        {"kind": "skill", "name": "demo"},
        {"kind": "skill", "name": "demo", "path": "skills/demo", "source": {"type": "git", "locator": "https://example.invalid/demo"}},
        {"kind": "skill", "name": "demo", "path": "../demo"},
        {"kind": "skill", "name": "demo", "path": "skills\\demo"},
        {"kind": "skill", "name": "demo", "path": "C:/skills/demo"},
        {"kind": "skill", "name": "demo", "path": "skills/demo", "description": "not portable"},
    ],
)
def test_registry_resource_rejects_invalid_or_mixed_locations(resource: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RegistryResource.model_validate(resource)


def test_unknown_kind_and_source_round_trip_with_extra_data(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    registry = Registry(
        resources=[
            RegistryResource.model_validate(
                {
                    "kind": "dataset",
                    "name": "records",
                    "source": {
                        "type": "future-hub",
                        "locator": "example/records",
                        "revision": "stable",
                    },
                    "consumer": {"format": "parquet"},
                }
            )
        ]
    )

    save_registry(registry, path)
    loaded = load_registry(path)
    unknown = loaded.resources[0]

    assert unknown.model_extra == {"consumer": {"format": "parquet"}}
    assert unknown.source is not None and unknown.source.type == "future-hub"
    assert canonical_registry_text(loaded) == path.read_text(encoding="utf-8")


def test_unknown_extra_field_order_does_not_change_canonical_yaml() -> None:
    first = RegistryResource.model_validate(
        {
            "kind": "dataset",
            "name": "records",
            "path": "datasets/records",
            "consumer": {"z": 1, "a": {"y": 2, "b": 3}},
            "alpha": True,
        }
    )
    second = RegistryResource.model_validate(
        {
            "alpha": True,
            "consumer": {"a": {"b": 3, "y": 2}, "z": 1},
            "path": "datasets/records",
            "name": "records",
            "kind": "dataset",
        }
    )

    assert canonical_registry_text(Registry(resources=[first])) == canonical_registry_text(
        Registry(resources=[second])
    )


def test_cc_port_overlay_is_separate_from_portable_registry(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    registry = Registry(
        items=[
            RegistryItem(
                name="demo",
                kind="skill",
                source="local",
                path="skills/demo",
                install_dir="shared-demo",
                platform_install_dirs={"cursor": "cursor-demo"},
                platforms=["cursor"],
            )
        ]
    )

    save_registry(registry, path)
    portable = path.read_text(encoding="utf-8")
    overlay = (tmp_path / "cc-port.yaml").read_text(encoding="utf-8")
    loaded = load_registry(path).get("demo", "skill")

    assert "install_name" not in portable
    assert "platforms" not in portable
    assert "install_name: shared-demo" in overlay
    assert loaded is not None
    assert loaded.install_target_name("cursor") == "cursor-demo"
    assert loaded.install_target_name("codex") == "shared-demo"
    assert loaded.platforms == ["cursor"]


@pytest.mark.parametrize("alias_field", ["install_name", "install_names"])
def test_memory_install_names_are_rejected_from_remote_overlay(
    tmp_path: Path,
    alias_field: str,
) -> None:
    payload: dict[str, object] = {
        "version": 1,
        "resources": {
            "memory:shared-memory": {
                alias_field: (
                    "private-project-slot"
                    if alias_field == "install_name"
                    else {"claude-code-wsl": "private-project-slot"}
                )
            }
        },
    }

    with pytest.raises(ValueError, match="machine-local"):
        CcPortSettings.model_validate(payload)


def test_memory_install_names_cannot_be_saved_after_overlay_mutation(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    path.write_text(
        "version: 1\nresources:\n- kind: memory\n  name: shared-memory\n"
        "  path: memories/shared-memory\n",
        encoding="utf-8",
    )
    registry = load_registry(path)
    registry.cc_port.resources["memory:shared-memory"] = CcPortResourceSettings(
        install_name="private-project-slot"
    )

    with pytest.raises(ValueError, match="machine-local"):
        save_registry(registry, path)

    assert "private-project-slot" not in path.read_text(encoding="utf-8")


def test_invalid_remote_overlay_fails_closed_on_registry_load(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    path.write_text("version: 1\nresources: []\n", encoding="utf-8")
    private_slot = "C--Users-private-project"
    (tmp_path / "cc-port.yaml").write_text(
        "version: 1\nresources:\n  memory:shared:\n"
        f"    install_name: {private_slot}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid portable resource settings") as error:
        load_registry(path)

    assert private_slot not in str(error.value)


def test_memory_registry_item_install_name_is_rejected_before_upsert() -> None:
    registry = Registry()
    with pytest.raises(ValueError, match="machine-local"):
        registry.upsert(
            RegistryItem(
                name="shared-memory",
                kind="memory",
                source="local",
                path="memories/shared-memory",
                platform_install_dirs={
                    "claude-code-wsl": "private-project-slot"
                },
            )
        )

    assert not registry.resources
    assert not registry.cc_port.resources


@pytest.mark.parametrize(
    ("resource_key", "platforms", "message"),
    [
        ("instruction:user-guidance", [], "exactly one portable source tool"),
        (
            "instruction:user-guidance",
            ["claude-code", "codex"],
            "exactly one portable source tool",
        ),
        ("memory:project-memory", [], "only to Claude Code"),
        ("memory:project-memory", ["codex"], "only to Claude Code"),
    ],
)
def test_remote_overlay_cannot_bypass_personal_resource_tool_binding(
    resource_key: str,
    platforms: list[str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        CcPortSettings.model_validate(
            {
                "version": 1,
                "resources": {
                    resource_key: {
                        "platforms": platforms,
                    }
                },
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"platforms": ["/home/private/claude"]},
        {"platforms": [r"C:\\Users\\private\\.claude"]},
        {"platforms": ["claude-code\nprivate"]},
        {"install_names": {"claude-windows:private": "portable-name"}},
    ],
)
def test_remote_overlay_rejects_path_like_or_local_platform_identity(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="portable tool ids"):
        CcPortSettings.model_validate(
            {
                "version": 1,
                "resources": {"skill:demo": payload},
            }
        )


@pytest.mark.parametrize(
    "field_value",
    [
        {"platforms": ["claude-windows:private"]},
        {"platform_install_dirs": {"/home/private/claude": "demo"}},
    ],
)
def test_resolved_resource_rejects_nonportable_platform_metadata(
    field_value: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="portable tool ids"):
        RegistryItem(
            name="demo",
            kind="skill",
            source="local",
            path="skills/demo",
            **field_value,
        )


def test_metadata_is_derived_from_content_and_not_persisted(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Derived\nversion: 1.2.3\nauthor: Example\nlicense: MIT\n---\nbody\n",
        encoding="utf-8",
    )
    path = tmp_path / "registry.yaml"
    save_registry(
        Registry(resources=[RegistryResource(kind="skill", name="demo", path="skills/demo")]),
        path,
    )

    loaded = load_registry(path).get("demo", "skill")
    text = path.read_text(encoding="utf-8")

    assert loaded is not None
    assert (loaded.description, loaded.version, loaded.author, loaded.license) == (
        "Derived",
        "1.2.3",
        "Example",
        "MIT",
    )
    for field in ("description", "author", "license"):
        assert f"  {field}:" not in text


def test_external_source_carries_portable_locator() -> None:
    source = ExternalSource(
        type="git",
        locator="https://github.com/example/resources",
        revision="abc123",
        subpath="skills/demo",
    )
    registry = Registry(
        resources=[RegistryResource(kind="skill", name="demo", source=source)]
    )

    entry = registry.get("demo", "skill")

    assert entry is not None and entry.external_source == source
    assert entry.repo == "https://github.com/example/resources"
    assert entry.ref == "abc123"
    assert entry.subdir == "skills/demo"
