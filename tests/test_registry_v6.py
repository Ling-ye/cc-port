from __future__ import annotations

from pathlib import Path

import pytest

from lpm.core.models import (
    AmbiguousResourceNameError,
    Registry,
    RegistryItem,
    ResourceKey,
)
from lpm.core.ownership import is_lpm_managed, write_managed_marker
from lpm.core.registry import load_registry, save_registry


def test_v5_migrates_without_losing_items_or_install_dir(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    path.write_text(
        "\n".join(
            [
                "version: 5",
                "items:",
                "  - name: demo",
                "    kind: skill",
                "    source: local",
                "    path: skills/demo",
                "    install_dir: shared-demo",
            ]
        ),
        encoding="utf-8",
    )

    registry = load_registry(path)

    assert registry.version == 6
    item = registry.get("demo", "skill")
    assert item is not None
    assert item.install_dir == "shared-demo"
    assert item.platform_install_dirs == {}


def test_registry_uses_kind_and_name_as_unique_key() -> None:
    registry = Registry(
        items=[
            RegistryItem(name="demo", kind="skill", source="local", path="skills/demo"),
            RegistryItem(name="demo", kind="prompt", source="local", path="prompts/demo"),
        ]
    )

    assert registry.get("demo", "skill").path == "skills/demo"
    assert registry.get_key(ResourceKey(kind="prompt", name="demo")).path == "prompts/demo"
    with pytest.raises(AmbiguousResourceNameError):
        registry.get("demo")

    registry.upsert(
        RegistryItem(name="demo", kind="skill", source="local", path="skills/demo-v2")
    )
    assert len(registry.items) == 2
    assert registry.get("demo", "skill").path == "skills/demo-v2"

    removed = registry.remove("demo", "prompt")
    assert removed is not None
    assert removed.kind == "prompt"
    assert registry.get("demo").kind == "skill"


def test_platform_install_dir_precedence_and_validation() -> None:
    entry = RegistryItem(
        name="demo",
        kind="skill",
        source="local",
        path="skills/demo",
        install_dir="global-demo",
        platform_install_dirs={"cursor": "cursor-demo"},
    )

    assert entry.install_target_name("cursor") == "cursor-demo"
    assert entry.install_target_name("codex") == "global-demo"

    with pytest.raises(ValueError):
        RegistryItem(
            name="demo",
            kind="skill",
            source="local",
            path="skills/demo",
            platform_install_dirs={"cursor": "../escape"},
        )


def test_registry_yaml_sorts_by_kind_and_name(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(name="zeta", kind="skill", source="local", path="skills/zeta"),
                RegistryItem(name="alpha", kind="prompt", source="local", path="prompts/alpha"),
            ]
        ),
        path,
    )

    loaded = load_registry(path)
    assert [item.resource_key for item in loaded.items] == ["prompt:alpha", "skill:zeta"]


def test_managed_marker_prefers_composite_resource_key(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    entry = RegistryItem(name="demo", kind="skill", source="local", path="skills/demo")
    write_managed_marker(target, entry, platform="cursor")

    assert is_lpm_managed(target, resource_key="skill:demo")
    assert not is_lpm_managed(target, resource_key="prompt:demo")
