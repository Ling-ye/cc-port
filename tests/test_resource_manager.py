from __future__ import annotations

from pathlib import Path

from lpm.core.config import Config, GithubConfig, InstallConfig, ResourcesConfig
from lpm.core.models import Registry, RegistryItem
from lpm.core.registry import load_registry, save_registry
from lpm.services import installer, resource_manager


def _config(root: Path, install: Path, *, token: str = "") -> Config:
    return Config(
        github=GithubConfig(token=token),
        install=InstallConfig(target=str(install)),
        resources=ResourcesConfig(local_path=str(root)),
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

    assert registry.version == 5
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
