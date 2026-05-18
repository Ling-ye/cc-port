from __future__ import annotations

from pathlib import Path

from lpm.core.config import Config
from lpm.core.models import RegistryItem
from lpm.interfaces import desktop_api
from lpm.services.local_resources import ImportLocalResult


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
