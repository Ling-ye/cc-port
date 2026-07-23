from __future__ import annotations

from pathlib import Path

import pytest

from lpm.core.config import Config, ResourcesConfig
from lpm.core.models import PluginInstallation, PluginOrigin, PluginSpec
from lpm.services import asset_sync
from lpm.services.doctor import _resource_repo_check
from lpm.services.resource_repo import ResourceRepoInfo
from lpm.services.ui_messages import (
    fallback_text,
    ui_message,
    ui_message_from_data,
    ui_messages_from_data,
)


@pytest.mark.parametrize(
    ("status", "local_status", "remote_status", "expected_code", "expected_fallback"),
    [
        (
            "uncomparable",
            "unknown",
            "present",
            "asset.diff.local_not_scanned",
            "Local assets have not been scanned yet.",
        ),
        (
            "local-only",
            "single",
            "missing",
            "asset.diff.local_only",
            "Local content is not present in the remote repository.",
        ),
        (
            "remote-only",
            "missing",
            "present",
            "asset.diff.remote_only",
            "Remote content is not installed in any scanned local tool.",
        ),
        (
            "same",
            "single",
            "present",
            "asset.diff.same",
            "Local and remote content fingerprints match.",
        ),
        (
            "content-different",
            "variants",
            "present",
            "asset.diff.content_different",
            "Local and remote content differ, or multiple local variants exist.",
        ),
        (
            "metadata-only",
            "single",
            "present",
            "asset.diff.metadata_only",
            "Content matches but metadata differs.",
        ),
        (
            "target-conflict",
            "single",
            "present",
            "asset.diff.target_conflict",
            "Multiple resources resolve to the same local target.",
        ),
        (
            "uncomparable",
            "single",
            "unavailable",
            "asset.diff.remote_unavailable",
            "The current remote state is unavailable; no absence is inferred.",
        ),
        (
            "uncomparable",
            "single",
            "present",
            "asset.diff.uncomparable",
            "The resource cannot be compared safely.",
        ),
    ],
)
def test_aggregate_diff_messages_keep_code_and_legacy_fallback(
    status: asset_sync.AssetStatus,
    local_status: str,
    remote_status: str,
    expected_code: str,
    expected_fallback: str,
) -> None:
    refs = asset_sync._aggregate_diff_summary_refs(
        status,
        local_status,
        remote_status,
    )

    assert refs == [ui_message(expected_code, expected_fallback)]
    assert asset_sync._aggregate_diff_summary(
        status,
        local_status,
        remote_status,
    ) == [expected_fallback]


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        ("remote-only", "asset.platform_diff.remote_only"),
        ("local-only", "asset.platform_diff.local_only"),
        ("same", "asset.platform_diff.same"),
        ("content-different", "asset.platform_diff.content_different"),
        ("metadata-only", "asset.platform_diff.metadata_only"),
        ("read-only-reference", "asset.platform_diff.read_only_reference"),
        ("target-conflict", "asset.platform_diff.target_conflict"),
        ("uncomparable", "asset.platform_diff.uncomparable"),
    ],
)
def test_platform_diff_messages_have_semantic_codes_and_compatible_text(
    status: asset_sync.AssetStatus,
    expected_code: str,
) -> None:
    refs = asset_sync._diff_summary_refs(status, ["description", "version"])

    assert refs[0].code == expected_code
    assert asset_sync._diff_summary(status, ["description", "version"]) == fallback_text(refs)
    if status == "metadata-only":
        assert refs[0].params == {"fields": "description, version"}
        assert refs[0].fallback == "Derived metadata differs: description, version."


def test_message_reference_parser_is_forward_compatible() -> None:
    data = {
        "code": "asset.diff.local_only",
        "fallback": "Local content is not present in the remote repository.",
        "params": {"count": 2, "enabled": True, "empty": None, "nested": {"ignored": True}},
        "future_field": "ignored",
    }

    ref = ui_message_from_data(data)

    assert ref is not None
    assert ref.code == "asset.diff.local_only"
    assert ref.params == {"count": 2, "enabled": True, "empty": None}
    assert ui_messages_from_data([data, None, {"fallback": "missing code"}]) == [ref]


def test_plugin_delete_detail_is_created_with_its_legacy_text() -> None:
    spec = PluginSpec(
        track="reference",
        platform="codex",
        plugin_id="demo",
        origin=PluginOrigin(type="marketplace", marketplace="example"),
    )

    instance = asset_sync._plugin_delete_instance(
        spec,
        PluginInstallation(scope="managed", enabled=True),
        None,
    )

    assert instance.detail_ref is not None
    assert instance.detail_ref.code == "plugin.delete.detail.managed_policy"
    assert instance.detail == instance.detail_ref.fallback


def test_doctor_warning_has_code_params_and_compatible_detail(tmp_path: Path) -> None:
    cfg = Config(
        resources=ResourcesConfig(
            repo_url="https://example.test/resources.git",
            local_path=str(tmp_path / "missing"),
        )
    )
    resource = ResourceRepoInfo(
        repo_name="resources",
        local_path=tmp_path / "missing",
        registry_path=tmp_path / "missing" / "registry.yaml",
        repo_url=cfg.resources.repo_url,
        remote_url="",
        branch="main",
        current_branch="",
        exists=False,
        is_git_repo=False,
        dirty=False,
    )

    check = _resource_repo_check(cfg, resource, True)

    assert check["detail_ref"].code == "doctor.resource_repo.path_missing"
    assert check["detail_ref"].params == {"path": str(resource.local_path)}
    assert check["detail"] == check["detail_ref"].fallback
