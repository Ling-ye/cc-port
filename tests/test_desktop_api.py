from __future__ import annotations

from pathlib import Path

from lpm.core.config import Config
from lpm.core.models import RegistryItem
from lpm.interfaces import desktop_api
from lpm.services.env_manager import EnvDiffItem, EnvDiffPlan
from lpm.services.local_resources import ImportLocalResult
from lpm.services.resource_manager import ResourceDeleteResult


def test_desktop_upload_pushes_resource_repo_by_default(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "demo"
    stored = tmp_path / "resources" / "skills" / "demo"
    source.mkdir()
    (source / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
    calls: list[str] = []

    def fake_import_local_resource(path: Path, **_kwargs) -> ImportLocalResult:
        assert path == source
        return ImportLocalResult(
            entry=RegistryItem(name="demo", kind="skill", source="local", path="skills/demo"),
            source_path=source,
            stored_path=stored,
        )

    def fake_push_resource_repo(*, config: Config):
        calls.append("push")
        return {"local_path": str(tmp_path / "resources")}

    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(desktop_api, "detect_local_resource_type", lambda *_args, **_kwargs: "skill")
    monkeypatch.setattr(desktop_api, "import_local_resource", fake_import_local_resource)
    monkeypatch.setattr(desktop_api, "push_resource_repo", fake_push_resource_repo)

    result = desktop_api.run_action("upload", {"path": str(source)})

    assert result["ok"] is True
    assert calls == ["push"]
    assert result["data"]["push"]["local_path"].endswith("resources")


def test_desktop_upload_allows_explicit_no_push(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "demo"
    stored = tmp_path / "resources" / "skills" / "demo"
    source.mkdir()
    (source / "SKILL.md").write_text("# Demo\n", encoding="utf-8")

    def fake_import_local_resource(path: Path, **_kwargs) -> ImportLocalResult:
        assert path == source
        return ImportLocalResult(
            entry=RegistryItem(name="demo", kind="skill", source="local", path="skills/demo"),
            source_path=source,
            stored_path=stored,
        )

    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(desktop_api, "detect_local_resource_type", lambda *_args, **_kwargs: "skill")
    monkeypatch.setattr(desktop_api, "import_local_resource", fake_import_local_resource)
    monkeypatch.setattr(
        desktop_api,
        "push_resource_repo",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("push should be skipped")),
    )

    result = desktop_api.run_action("upload", {"path": str(source), "no_push": True})

    assert result["ok"] is True
    assert result["data"]["push"] is None


def test_desktop_resource_delete_pushes_resource_repo_by_default(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[str, str]] = []
    entry = RegistryItem(
        name="demo",
        kind="skill",
        source="local",
        path="skills/demo",
        lifecycle="removed",
        removed_effect="local_files_deleted",
    )

    def fake_delete_resource(name: str, **kwargs) -> ResourceDeleteResult:
        assert name == "demo"
        assert isinstance(kwargs["config"], Config)
        calls.append(("delete", name))
        return ResourceDeleteResult(
            name=name,
            effect="local_files_deleted",
            entry=entry,
            deleted_path=tmp_path / "resources" / "skills" / "demo",
            deleted_local_files=True,
        )

    def fake_push_resource_repo(*, message: str, config: Config):
        assert isinstance(config, Config)
        calls.append(("push", message))
        return {"local_path": str(tmp_path / "resources")}

    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(desktop_api, "delete_resource", fake_delete_resource)
    monkeypatch.setattr(desktop_api, "push_resource_repo", fake_push_resource_repo)

    result = desktop_api.run_action("resource_delete", {"name": "demo"})

    assert result["ok"] is True
    assert calls == [("delete", "demo"), ("push", "lpm: delete resource demo")]
    assert result["data"]["name"] == "demo"
    assert result["data"]["deleted_local_files"] is True
    assert result["data"]["push"]["local_path"].endswith("resources")


def test_desktop_env_diff_import_serializes_paths_and_choices(tmp_path: Path, monkeypatch) -> None:
    def fake_build_env_import_diff(snapshot: str, *, config: Config) -> EnvDiffPlan:
        assert snapshot == "snapshot.zip"
        assert isinstance(config, Config)
        return EnvDiffPlan(
            operation="import",
            source="snapshot",
            local_root=tmp_path / "local",
            incoming_root=tmp_path / "incoming",
            items=[
                EnvDiffItem(
                    id="resource:demo",
                    group="resource",
                    name="demo",
                    kind="skill",
                    status="modified",
                    local_path=tmp_path / "local" / "resources" / "skills" / "demo",
                    incoming_path=tmp_path / "incoming" / "resources" / "skills" / "demo",
                    default_choice="incoming",
                    preview="--- local\n+++ incoming",
                )
            ],
            default_choices={"resource:demo": "incoming"},
        )

    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(desktop_api, "build_env_import_diff", fake_build_env_import_diff)

    result = desktop_api.run_action("env_diff_import", {"snapshot": "snapshot.zip"})

    assert result["ok"] is True
    data = result["data"]
    assert data["local_root"] == str(tmp_path / "local")
    assert data["default_choices"] == {"resource:demo": "incoming"}
    assert data["items"][0]["local_path"].endswith("resources/skills/demo")
