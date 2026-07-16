from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lpm.core.config import Config, GithubConfig, InstallConfig, ResourcesConfig
from lpm.core.models import Registry, RegistryItem
from lpm.core.ownership import mark_lpm_managed_mcp, write_managed_marker
from lpm.core.platforms import PlatformProfile, PlatformsConfig
from lpm.core.registry import load_registry, save_registry
from lpm.services import installer, resource_manager
from lpm.services.operation_history import operation_history, restore_operation


def _config(
    root: Path,
    install: Path,
    *,
    token: str = "",
    platforms: list[PlatformProfile] | None = None,
) -> Config:
    return Config(
        github=GithubConfig(token=token),
        install=InstallConfig(target=str(install)),
        resources=ResourcesConfig(local_path=str(root)),
        platforms=PlatformsConfig(profiles=platforms or []),
    )


def test_registry_v4_migrates_items_to_active_lifecycle(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        "\n".join(
            [
                "version: 4",
                "items:",
                "  - name: demo",
                "    kind: skill",
                "    source: external",
                "    repo: https://github.com/example/demo",
                "    ref: main",
            ]
        ),
        encoding="utf-8",
    )

    registry = load_registry(registry_path)

    assert registry.version == 6
    assert registry.get("demo").lifecycle == "active"


def test_delete_local_resource_marks_removed_and_deletes_repo_files(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    source = root / "skills" / "local-demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("# Local Demo\n", encoding="utf-8")
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="local-demo",
                    kind="skill",
                    source="local",
                    path="skills/local-demo",
                )
            ]
        ),
        registry_path,
    )

    result = resource_manager.delete_resource(
        "local-demo",
        config=_config(root, install),
        registry_path=registry_path,
    )

    stored = load_registry(registry_path).get("local-demo")
    assert result.effect == "local_files_deleted"
    assert result.deleted_local_files is True
    assert not source.exists()
    assert stored is not None
    assert stored.lifecycle == "removed"
    assert stored.removed_effect == "local_files_deleted"


def test_delete_external_resource_only_marks_index_removed(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    untouched = tmp_path / "external"
    untouched.mkdir()
    (untouched / "SKILL.md").write_text("# External\n", encoding="utf-8")
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="external-demo",
                    kind="skill",
                    source="external",
                    repo="https://github.com/example/external-demo",
                )
            ]
        ),
        registry_path,
    )

    result = resource_manager.delete_resource(
        "external-demo",
        config=_config(root, install),
        registry_path=registry_path,
    )

    stored = load_registry(registry_path).get("external-demo")
    assert result.effect == "index_only"
    assert untouched.exists()
    assert stored is not None
    assert stored.lifecycle == "removed"
    assert stored.removed_effect == "index_only"


def test_delete_owned_resource_deletes_remote_and_marks_removed(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="owned-demo",
                    kind="skill",
                    source="owned",
                    repo="https://github.com/example/owned-demo",
                )
            ]
        ),
        registry_path,
    )
    calls: list[tuple[str, str]] = []

    class FakeGithubClient:
        def __init__(self, token: str):
            assert token == "token"

        def delete_repo(self, owner: str, name: str) -> bool:
            calls.append((owner, name))
            return True

    monkeypatch.setattr(resource_manager, "GithubClient", FakeGithubClient)

    result = resource_manager.delete_resource(
        "owned-demo",
        config=_config(root, install, token="token"),
        registry_path=registry_path,
        confirm_name="owned-demo",
    )

    stored = load_registry(registry_path).get("owned-demo")
    assert calls == [("example", "owned-demo")]
    assert result.effect == "remote_repo_deleted"
    assert result.remote_repo_deleted is True
    assert stored is not None
    assert stored.lifecycle == "removed"
    assert stored.removed_effect == "remote_repo_deleted"


def test_uninstall_keeps_registry_lifecycle_active(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    source = root / "skills" / "local-demo"
    installed = install / "local-demo"
    source.mkdir(parents=True)
    installed.mkdir(parents=True)
    (source / "SKILL.md").write_text("# Local Demo\n", encoding="utf-8")
    (installed / "SKILL.md").write_text("# Installed Demo\n", encoding="utf-8")
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="local-demo",
                    kind="skill",
                    source="local",
                    path="skills/local-demo",
                )
            ]
        ),
        registry_path,
    )

    result = resource_manager.uninstall_resource(
        "local-demo",
        config=_config(root, install),
        registry_path=registry_path,
    )

    stored = load_registry(registry_path).get("local-demo")
    assert result["uninstalled"] is True
    assert not installed.exists()
    assert stored is not None
    assert stored.lifecycle == "active"


def test_uninstall_selected_platform_keeps_cache_and_other_targets(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    source = root / "skills" / "local-demo"
    installed = install / "local-demo"
    cursor_target = tmp_path / "cursor" / "skills" / "local-demo"
    codex_target = tmp_path / "codex" / "skills" / "local-demo"
    for path in (source, installed, cursor_target, codex_target):
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(path.name, encoding="utf-8")
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="local-demo",
                    kind="skill",
                    source="local",
                    path="skills/local-demo",
                )
            ]
        ),
        registry_path,
    )
    entry = load_registry(registry_path).get("local-demo")
    assert entry is not None
    write_managed_marker(cursor_target, entry, platform="cursor")
    write_managed_marker(codex_target, entry, platform="codex")

    result = resource_manager.uninstall_resource(
        "local-demo",
        config=_config(
            root,
            install,
            platforms=[
                PlatformProfile(name="cursor", enabled=True, skills_dir=str(tmp_path / "cursor" / "skills")),
                PlatformProfile(name="codex", enabled=True, skills_dir=str(tmp_path / "codex" / "skills")),
            ],
        ),
        registry_path=registry_path,
        platform_filter="cursor",
    )

    assert result["uninstalled"] is True
    assert installed.exists()
    assert not cursor_target.exists()
    assert codex_target.exists()


def test_preview_prefers_install_then_source_then_clone_cache(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    source = root / "skills" / "local-demo"
    installed = install / "local-demo"
    source.mkdir(parents=True)
    installed.mkdir(parents=True)
    (source / "SKILL.md").write_text("source preview", encoding="utf-8")
    (installed / "SKILL.md").write_text("installed preview", encoding="utf-8")
    registry_path = root / "registry.yaml"
    entry = RegistryItem(
        name="local-demo",
        kind="skill",
        source="local",
        path="skills/local-demo",
    )
    save_registry(Registry(items=[entry]), registry_path)
    cfg = _config(root, install)

    assert resource_manager.preview_resource(
        "local-demo",
        config=cfg,
        registry_path=registry_path,
    ).text == "installed preview"

    (installed / "SKILL.md").unlink()
    installed.rmdir()
    assert resource_manager.preview_resource(
        "local-demo",
        config=cfg,
        registry_path=registry_path,
    ).text == "source preview"

    external = RegistryItem(
        name="external-demo",
        kind="skill",
        source="external",
        repo="https://github.com/example/external-demo",
        subdir="skill",
    )
    save_registry(Registry(items=[external]), registry_path)
    clone_content = install / ".lpm" / "clones" / "external-demo" / "skill"
    clone_content.mkdir(parents=True)
    (clone_content / "SKILL.md").write_text("clone preview", encoding="utf-8")

    assert resource_manager.preview_resource(
        "external-demo",
        config=cfg,
        registry_path=registry_path,
    ).text == "clone preview"


def test_preview_fetches_remote_to_cache_when_missing(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="external-demo",
                    kind="skill",
                    source="external",
                    repo="https://github.com/example/external-demo",
                )
            ]
        ),
        registry_path,
    )

    def fake_clone(url: str, dest: Path, **_kwargs) -> None:
        assert url == "https://github.com/example/external-demo"
        dest.mkdir(parents=True)
        (dest / ".git").mkdir()
        (dest / "SKILL.md").write_text("remote preview", encoding="utf-8")

    monkeypatch.setattr(resource_manager.git_ops, "clone", fake_clone)

    result = resource_manager.preview_resource(
        "external-demo",
        config=_config(root, install),
        registry_path=registry_path,
    )

    assert result.text == "remote preview"
    assert (install / "external-demo" / "SKILL.md").is_file()


def test_preview_with_platform_prefers_installed_target(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    installed = install / "local-demo"
    cursor_target = tmp_path / "cursor" / "skills" / "local-demo"
    installed.mkdir(parents=True)
    cursor_target.mkdir(parents=True)
    (installed / "SKILL.md").write_text("cache preview", encoding="utf-8")
    (cursor_target / "SKILL.md").write_text("cursor preview", encoding="utf-8")
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="local-demo",
                    kind="skill",
                    source="local",
                    path="skills/local-demo",
                )
            ]
        ),
        registry_path,
    )

    result = resource_manager.preview_resource(
        "local-demo",
        config=_config(
            root,
            install,
            platforms=[
                PlatformProfile(name="cursor", enabled=True, skills_dir=str(tmp_path / "cursor" / "skills")),
            ],
        ),
        registry_path=registry_path,
        platform_filter="cursor",
    )

    assert result.text == "cursor preview"


def test_open_path_platform_requires_existing_target(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="external-demo",
                    kind="skill",
                    source="external",
                    repo="https://github.com/example/external-demo",
                )
            ]
        ),
        registry_path,
    )

    with pytest.raises(FileNotFoundError):
        resource_manager.resource_open_path(
            "external-demo",
            config=_config(
                root,
                install,
                platforms=[
                    PlatformProfile(name="cursor", enabled=True, skills_dir=str(tmp_path / "cursor" / "skills")),
                ],
            ),
            registry_path=registry_path,
            platform_filter="cursor",
        )


def test_mcp_target_inventory_and_platform_uninstall(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    mcp_json = tmp_path / "cursor" / "mcp.json"
    mcp_json.parent.mkdir(parents=True)
    mcp_json.write_text(
        json.dumps({"mcpServers": {"demo-mcp": {"command": "demo"}}}),
        encoding="utf-8",
    )
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="demo-mcp",
                    kind="mcp",
                    source="owned",
                    path="mcp/demo-mcp",
                    mcp_config={"command": "demo"},
                )
            ]
        ),
        registry_path,
    )
    cfg = _config(
        root,
        install,
        platforms=[PlatformProfile(name="cursor", enabled=True, mcp_json=str(mcp_json))],
    )
    mark_lpm_managed_mcp(
        mcp_json,
        "demo-mcp",
        resource_name="demo-mcp",
        platform="cursor",
    )

    inventory = resource_manager.build_resource_inventory(config=cfg, registry_path=registry_path)
    target = inventory["items"][0].local_state.targets[0]
    assert target.platform == "cursor"
    assert target.installed is True

    result = resource_manager.uninstall_resource(
        "demo-mcp",
        config=cfg,
        registry_path=registry_path,
        platform_filter="cursor",
    )

    assert result["uninstalled"] is True
    data = json.loads(mcp_json.read_text(encoding="utf-8"))
    assert "demo-mcp" not in data["mcpServers"]


def test_uninstall_preserves_unmanaged_mcp_server(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    mcp_json = tmp_path / "cursor" / "mcp.json"
    mcp_json.parent.mkdir(parents=True)
    mcp_json.write_text(
        json.dumps({"mcpServers": {"demo-mcp": {"command": "user"}}}),
        encoding="utf-8",
    )
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="demo-mcp",
                    kind="mcp",
                    source="owned",
                    path="mcp/demo-mcp",
                    mcp_config={"command": "managed"},
                )
            ]
        ),
        registry_path,
    )
    cfg = _config(
        root,
        install,
        platforms=[PlatformProfile(name="cursor", enabled=True, mcp_json=str(mcp_json))],
    )

    result = resource_manager.uninstall_resource(
        "demo-mcp",
        config=cfg,
        registry_path=registry_path,
        platform_filter="cursor",
    )

    assert result["uninstalled"] is False
    data = json.loads(mcp_json.read_text(encoding="utf-8"))
    assert data["mcpServers"]["demo-mcp"] == {"command": "user"}


def test_inventory_respects_platform_allowlist_and_keeps_stale_target_removable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    cursor_target = tmp_path / "cursor" / "skills" / "cursor-only"
    codex_target = tmp_path / "codex" / "skills" / "cursor-only"
    cursor_target.mkdir(parents=True)
    codex_target.mkdir(parents=True)
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="cursor-only",
                    kind="skill",
                    source="local",
                    path="skills/cursor-only",
                    platforms=["cursor"],
                )
            ]
        ),
        registry_path,
    )
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(
                name="cursor",
                enabled=True,
                skills_dir=str(tmp_path / "cursor" / "skills"),
            ),
            PlatformProfile(
                name="codex",
                enabled=True,
                skills_dir=str(tmp_path / "codex" / "skills"),
            ),
        ],
    )
    entry = load_registry(registry_path).get("cursor-only")
    assert entry is not None
    write_managed_marker(cursor_target, entry, platform="cursor")
    write_managed_marker(codex_target, entry, platform="codex")

    inventory = resource_manager.build_resource_inventory(
        config=cfg,
        registry_path=registry_path,
    )
    targets = {
        target.platform: target
        for target in inventory["items"][0].local_state.targets
    }

    assert targets["cursor"].supported is True
    assert targets["cursor"].installed is True
    assert targets["codex"].supported is False
    assert targets["codex"].installed is True
    with pytest.raises(FileNotFoundError):
        resource_manager.resource_open_path(
            "cursor-only",
            config=cfg,
            registry_path=registry_path,
            platform_filter="codex",
        )

    result = resource_manager.uninstall_resource(
        "cursor-only",
        config=cfg,
        registry_path=registry_path,
        platform_filter="codex",
    )
    assert result["uninstalled"] is True
    assert not codex_target.exists()


def test_uninstall_refuses_unmanaged_platform_target(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    target = tmp_path / "cursor" / "skills" / "local-demo"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("# User owned\n", encoding="utf-8")
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="local-demo",
                    kind="skill",
                    source="local",
                    path="skills/local-demo",
                )
            ]
        ),
        registry_path,
    )
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(
                name="cursor",
                enabled=True,
                skills_dir=str(target.parent),
            )
        ],
    )

    result = resource_manager.uninstall_resource(
        "local-demo",
        config=cfg,
        registry_path=registry_path,
        platform_filter="cursor",
    )

    assert result["uninstalled"] is False
    assert (target / "SKILL.md").is_file()


def test_install_disallowed_platform_stops_before_writing_cache(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    source = root / "skills" / "cursor-only"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("# Cursor only\n", encoding="utf-8")
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="cursor-only",
                    kind="skill",
                    source="local",
                    path="skills/cursor-only",
                    platforms=["cursor"],
                )
            ]
        ),
        registry_path,
    )
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(
                name="codex",
                enabled=True,
                skills_dir=str(tmp_path / "codex" / "skills"),
            )
        ],
    )

    with pytest.raises(RuntimeError, match="not allowed on platform"):
        resource_manager.install_resource(
            "cursor-only",
            config=cfg,
            registry_path=registry_path,
            platform_filter="codex",
        )

    assert not (install / "cursor-only").exists()


def test_install_external_resource_replaces_stale_non_git_directory(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    stale = install / "caveman"
    stale.mkdir(parents=True)
    (stale / "partial.txt").write_text("stale", encoding="utf-8")
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="caveman",
                    kind="plugin",
                    source="external",
                    repo="https://github.com/example/caveman",
                )
            ]
        ),
        registry_path,
    )

    def fake_clone(url: str, dest: Path, **_kwargs) -> None:
        assert url == "https://github.com/example/caveman"
        assert not dest.exists()
        dest.mkdir(parents=True)
        (dest / ".git").mkdir()
        (dest / "README.md").write_text("caveman", encoding="utf-8")

    monkeypatch.setattr(installer.git_ops, "clone", fake_clone)

    result = resource_manager.install_resource(
        "caveman",
        config=_config(root, install),
        registry_path=registry_path,
    )

    assert result.action == installer.SyncAction.INSTALLED
    assert not (stale / "partial.txt").exists()
    assert (stale / "README.md").is_file()


def test_resource_install_failure_rolls_back_cache_and_platform_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    source = root / "skills" / "demo"
    cache = install / "demo"
    target = tmp_path / "cursor" / "skills" / "demo"
    source.mkdir(parents=True)
    cache.mkdir(parents=True)
    target.mkdir(parents=True)
    (source / "SKILL.md").write_text("new", encoding="utf-8")
    (cache / "SKILL.md").write_text("old-cache", encoding="utf-8")
    (target / "SKILL.md").write_text("old-target", encoding="utf-8")
    entry = RegistryItem(
        name="demo",
        kind="skill",
        source="local",
        path="skills/demo",
    )
    write_managed_marker(target, entry, platform="cursor")
    registry_path = root / "registry.yaml"
    save_registry(Registry(items=[entry]), registry_path)
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(
                name="cursor",
                enabled=True,
                skills_dir=str(tmp_path / "cursor" / "skills"),
            )
        ],
    )

    def fail_distribution(*_args, **_kwargs):
        shutil.rmtree(target)
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("partial", encoding="utf-8")
        raise RuntimeError("simulated platform failure")

    monkeypatch.setattr(installer, "_distribute_to_platforms", fail_distribution)

    with pytest.raises(RuntimeError, match="simulated platform failure"):
        resource_manager.install_resource(
            "demo",
            config=cfg,
            registry_path=registry_path,
        )

    assert (cache / "SKILL.md").read_text(encoding="utf-8") == "old-cache"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "old-target"
    latest = operation_history(limit=1)[0]
    assert latest.kind == "resource-install"
    assert latest.status == "rolled_back"


def test_repeated_resource_install_records_empty_change_set(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    source = root / "skills" / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("same", encoding="utf-8")
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="demo",
                    kind="skill",
                    source="local",
                    path="skills/demo",
                )
            ]
        ),
        registry_path,
    )
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(
                name="cursor",
                enabled=True,
                skills_dir=str(tmp_path / "cursor" / "skills"),
            )
        ],
    )

    resource_manager.install_resource("demo", config=cfg, registry_path=registry_path)
    resource_manager.install_resource("demo", config=cfg, registry_path=registry_path)

    latest = operation_history(limit=1)[0]
    assert latest.kind == "resource-install"
    assert latest.status == "succeeded"
    assert latest.changed_target_count == 0
    assert latest.restorable is False


def test_repeated_mcp_install_does_not_rewrite_config_or_ownership(
    tmp_path: Path,
) -> None:
    root = tmp_path / "resources"
    (root / "mcp" / "demo-mcp").mkdir(parents=True)
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="demo-mcp",
                    kind="mcp",
                    source="local",
                    path="mcp/demo-mcp",
                    mcp_config={"command": "demo", "args": ["serve"]},
                )
            ]
        ),
        registry_path,
    )
    cfg = _config(
        root,
        tmp_path / "install",
        platforms=[
            PlatformProfile(
                name="cursor",
                enabled=True,
                mcp_json=str(tmp_path / "cursor" / "mcp.json"),
            )
        ],
    )

    resource_manager.install_resource("demo-mcp", config=cfg, registry_path=registry_path)
    resource_manager.install_resource("demo-mcp", config=cfg, registry_path=registry_path)

    latest = operation_history(limit=1)[0]
    assert latest.kind == "resource-install"
    assert latest.changed_target_count == 0


def test_resource_uninstall_can_be_restored_from_operation_history(
    tmp_path: Path,
) -> None:
    root = tmp_path / "resources"
    install = tmp_path / "install"
    source = root / "skills" / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("demo", encoding="utf-8")
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="demo",
                    kind="skill",
                    source="local",
                    path="skills/demo",
                )
            ]
        ),
        registry_path,
    )
    target = tmp_path / "cursor" / "skills" / "demo"
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(
                name="cursor",
                enabled=True,
                skills_dir=str(tmp_path / "cursor" / "skills"),
            )
        ],
    )
    resource_manager.install_resource("demo", config=cfg, registry_path=registry_path)

    resource_manager.uninstall_resource("demo", config=cfg, registry_path=registry_path)
    uninstall = operation_history(limit=1)[0]
    assert uninstall.kind == "resource-uninstall"
    assert uninstall.restorable is True
    assert not target.exists()
    assert not (install / "demo").exists()

    restore_operation(uninstall.operation_id)

    assert (target / "SKILL.md").read_text(encoding="utf-8") == "demo"
    assert (install / "demo" / "SKILL.md").read_text(encoding="utf-8") == "demo"
