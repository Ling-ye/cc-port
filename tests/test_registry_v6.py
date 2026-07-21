from __future__ import annotations

from pathlib import Path

import pytest

from lpm.core.models import (
    AmbiguousResourceNameError,
    PluginInstallation,
    PluginOrigin,
    PluginSpec,
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

    assert registry.version == 7
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


def test_v7_plugin_reference_can_omit_github_repo_and_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    entry = RegistryItem(
        name="codex-openai-bundled-chrome",
        kind="plugin",
        source="external",
        plugin=PluginSpec(
            track="reference",
            platform="codex",
            plugin_id="chrome",
            origin=PluginOrigin(
                type="marketplace",
                marketplace="openai-bundled",
                source="openai-bundled",
            ),
            observed_version="26.707.72221",
            installations=[PluginInstallation(scope="user", enabled=True)],
        ),
    )

    save_registry(Registry(items=[entry]), path)
    loaded = load_registry(path)

    assert loaded.version == 7
    plugin = loaded.get("codex-openai-bundled-chrome", "plugin")
    assert plugin is not None and plugin.plugin is not None
    assert plugin.plugin.track == "reference"
    assert plugin.plugin.observed_version == "26.707.72221"
    assert "path:" not in path.read_text(encoding="utf-8")


def test_v6_plugin_remains_legacy_without_guessed_plugin_metadata(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    path.write_text(
        "version: 6\n"
        "items:\n"
        "  - name: legacy\n"
        "    kind: plugin\n"
        "    source: local\n"
        "    path: plugins/legacy\n",
        encoding="utf-8",
    )

    loaded = load_registry(path)

    assert loaded.version == 7
    assert loaded.get("legacy", "plugin").plugin is None
