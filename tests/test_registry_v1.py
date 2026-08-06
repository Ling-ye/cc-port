from __future__ import annotations

from pathlib import Path

import pytest

from cc_port.core.models import (
    AmbiguousResourceNameError,
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
