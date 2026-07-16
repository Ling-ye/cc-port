from __future__ import annotations

from pathlib import Path

from lpm.core.config import Config, GithubConfig, ResourcesConfig
from lpm.core.models import RegistryItem
from lpm.infrastructure.github_client import GithubAuthError
from lpm.interfaces import desktop_api
from lpm.services.env_manager import EnvDiffItem, EnvDiffPlan
from lpm.services.local_resources import ImportLocalResult
from lpm.services.resource_commit import (
    ResourceCommitChange,
    ResourceCommitIssue,
    ResourceCommitPlan,
)
from lpm.services.resource_manager import ResourceDeleteResult
from lpm.services.resource_sync import ResourceSyncPlan, SyncConflict


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
    assert Path(data["items"][0]["local_path"]) == tmp_path / "local" / "resources" / "skills" / "demo"


def test_desktop_resource_sync_plan_serializes_conflicts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(
        desktop_api,
        "build_resource_sync_plan",
        lambda **_kwargs: ResourceSyncPlan(
            operation_id="abc123",
            repo_path=tmp_path / "resources",
            branch="main",
            status="conflict",
            local_commit="local",
            remote_commit="remote",
            merge_base="base",
            ahead=1,
            behind=1,
            worktree_path=tmp_path / "state" / "worktree",
            conflicts=[
                SyncConflict(
                    id="resource:demo",
                    path="skills/demo/SKILL.md",
                    resource="demo",
                    reason="changed on both sides",
                )
            ],
        ),
    )

    result = desktop_api.run_action("resource_sync_plan")

    assert result["ok"] is True
    assert result["data"]["repo_path"] == str(tmp_path / "resources")
    assert result["data"]["conflicts"][0]["id"] == "resource:demo"


def test_desktop_resource_commit_plan_serializes_resource_blockers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(
        desktop_api,
        "build_resource_commit_plan",
        lambda **_kwargs: ResourceCommitPlan(
            repo_path=tmp_path / "resources",
            changed_paths=["notes.txt"],
            managed_paths=[],
            resources=[
                ResourceCommitChange(
                    name="notes.txt",
                    kind="metadata",
                    action="added",
                    paths=["notes.txt"],
                )
            ],
            blocked_paths=[
                ResourceCommitIssue(
                    path="notes.txt",
                    reason="outside managed scope",
                )
            ],
            secret_findings=[],
            suggested_message="lpm: update resource metadata",
        ),
    )

    result = desktop_api.run_action("resource_commit_plan")

    assert result["ok"] is True
    assert result["data"]["blocked"] is True
    assert result["data"]["blocked_paths"][0]["path"] == "notes.txt"


def test_desktop_operation_history_and_force_restore(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        desktop_api,
        "operation_history",
        lambda *, limit: [
            {
                "operation_id": "abc123",
                "kind": "resource-install",
                "status": "succeeded",
                "restorable": True,
                "limit": limit,
            }
        ],
    )

    def fake_restore(operation_id: str, *, force: bool, config: Config):
        assert isinstance(config, Config)
        calls.append((operation_id, force))
        return {"source_operation_id": operation_id, "status": "succeeded"}

    monkeypatch.setattr(desktop_api, "restore_operation", fake_restore)

    history = desktop_api.run_action("operation_history", {"limit": 20})
    restored = desktop_api.run_action(
        "operation_restore",
        {"operation_id": "abc123", "force": True},
    )

    assert history["ok"] is True
    assert history["data"]["operations"][0]["limit"] == 20
    assert restored["ok"] is True
    assert calls == [("abc123", True)]


def test_desktop_operation_page_detail_and_state_maintenance(monkeypatch) -> None:
    monkeypatch.setattr(
        desktop_api,
        "operation_history_page",
        lambda *, offset, limit: {
            "operations": [{"operation_id": "abc123"}],
            "offset": offset,
            "limit": limit,
            "total": 1,
            "has_more": False,
        },
    )
    monkeypatch.setattr(
        desktop_api,
        "operation_detail",
        lambda operation_id: {
            "operation_id": operation_id,
            "targets": [{"path": "/target"}],
        },
    )
    monkeypatch.setattr(
        desktop_api,
        "list_orphan_backups",
        lambda: [{"name": "lost", "size_bytes": 42}],
    )
    monkeypatch.setattr(
        desktop_api,
        "list_orphan_quarantines",
        lambda: [{"quarantine_id": "def456", "item_count": 1}],
    )
    monkeypatch.setattr(
        desktop_api,
        "list_maintenance_audits",
        lambda *, limit: [{"audit_id": "audit123", "limit": limit}],
    )
    monkeypatch.setattr(
        desktop_api,
        "load_maintenance_audit",
        lambda audit_id: {"audit_id": audit_id, "status": "succeeded"},
    )

    page = desktop_api.run_action(
        "operation_history_page",
        {"offset": 20, "limit": 20},
    )
    detail = desktop_api.run_action(
        "operation_detail",
        {"operation_id": "abc123"},
    )
    orphans = desktop_api.run_action("orphan_backups")
    quarantines = desktop_api.run_action("orphan_quarantines")
    audits = desktop_api.run_action("maintenance_audits", {"limit": 10})
    audit = desktop_api.run_action(
        "maintenance_audit",
        {"audit_id": "audit123"},
    )

    assert page["data"]["offset"] == 20
    assert detail["data"]["targets"][0]["path"] == "/target"
    assert orphans["data"]["orphans"][0]["name"] == "lost"
    assert quarantines["data"]["quarantines"][0]["quarantine_id"] == "def456"
    assert audits["data"]["audits"][0]["limit"] == 10
    assert audit["data"]["audit"]["audit_id"] == "audit123"


def test_desktop_state_retention_plan_and_prune(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_plan(**kwargs):
        calls.append(("plan", kwargs["retention_days"]))
        return {"candidate_count": 1, "candidates": [{"operation_id": "abc123"}]}

    def fake_prune(operation_ids: list[str], **kwargs):
        calls.append(("prune", (operation_ids, kwargs["max_backup_mb"])))
        return {"deleted_operation_ids": operation_ids, "reclaimed_bytes": 42}

    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(desktop_api, "build_state_retention_plan", fake_plan)
    monkeypatch.setattr(desktop_api, "prune_state", fake_prune)

    plan = desktop_api.run_action(
        "state_retention_plan",
        {
            "retention_days": 30,
            "keep_latest_operations": 5,
            "max_backup_mb": 100,
        },
    )
    pruned = desktop_api.run_action(
        "state_prune",
        {
            "operation_ids": ["abc123"],
            "retention_days": 30,
            "keep_latest_operations": 5,
            "max_backup_mb": 100,
        },
    )

    assert plan["ok"] is True
    assert plan["data"]["candidate_count"] == 1
    assert pruned["ok"] is True
    assert calls == [
        ("plan", 30),
        ("prune", (["abc123"], 100)),
    ]


def test_desktop_lists_and_cleans_stale_sync_worktrees(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        desktop_api,
        "list_stale_resource_sync_plans",
        lambda *, min_age_hours: [
            {
                "operation_id": "abc123",
                "worktree_path": tmp_path / "worktree",
                "age_hours": min_age_hours,
            }
        ],
    )
    monkeypatch.setattr(
        desktop_api,
        "cleanup_stale_resource_sync_plan",
        lambda operation_id, **_kwargs: {"operation_id": operation_id, "status": "abandoned"},
    )

    stale = desktop_api.run_action("resource_sync_stale", {"min_age_hours": 12})
    cleaned = desktop_api.run_action(
        "resource_sync_cleanup",
        {"operation_id": "abc123"},
    )

    assert stale["ok"] is True
    assert stale["data"]["plans"][0]["worktree_path"] == str(tmp_path / "worktree")
    assert stale["data"]["plans"][0]["age_hours"] == 12
    assert cleaned["data"]["status"] == "abandoned"


def test_config_branches_falls_back_to_git_credentials_when_token_is_rejected(
    monkeypatch,
) -> None:
    cfg = Config(
        github=GithubConfig(token="expired-token"),
        resources=ResourcesConfig(
            repo_name="LingyeAIResources",
            repo_url="https://github.com/Ling-ye/LingyeAIResources.git",
            branch="main",
        ),
    )

    class RejectedGithubClient:
        def __init__(self, token: str):
            assert token == "expired-token"

        def list_repo_branches(self, owner: str, name: str):
            assert (owner, name) == ("Ling-ye", "LingyeAIResources")
            raise GithubAuthError("GitHub token rejected.")

    monkeypatch.setattr(desktop_api, "load_raw_config", lambda: cfg)
    monkeypatch.setattr(desktop_api, "GithubClient", RejectedGithubClient)
    monkeypatch.setattr(
        desktop_api.git_ops,
        "remote_branches",
        lambda url: ("main", ["dev", "main"]),
    )

    result = desktop_api._config_branches(
        {
            "draft": {
                "github": {},
                "install": {},
                "resources": {},
            }
        }
    )

    assert result["branches"] == ["main", "dev"]
    assert result["default_branch"] == "main"
    assert "GitHub token rejected" in result["warning"]
    assert "local Git/SSH credentials" in result["warning"]


def test_config_branches_uses_git_credentials_without_api_token(monkeypatch) -> None:
    cfg = Config(
        resources=ResourcesConfig(
            repo_name="LingyeAIResources",
            repo_url="https://github.com/Ling-ye/LingyeAIResources.git",
            branch="release",
        )
    )
    monkeypatch.setattr(desktop_api, "load_raw_config", lambda: cfg)
    monkeypatch.setattr(
        desktop_api.git_ops,
        "remote_branches",
        lambda url: ("main", ["main", "release"]),
    )

    result = desktop_api._config_branches(
        {
            "draft": {
                "github": {},
                "install": {},
                "resources": {},
            }
        }
    )

    assert result["branches"] == ["main", "release"]
    assert result["selected_branch"] == "release"
    assert "No API token is configured" in result["warning"]
