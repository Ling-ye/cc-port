from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from cc_port.services.approval import (
    ApprovalRequiredError,
    approve_approval_request,
    consume_approval,
    create_approval_request,
    invalidate_approval_request,
    list_approval_requests,
    load_approval_request,
)


@pytest.fixture
def isolated_state_home(tmp_path, monkeypatch: pytest.MonkeyPatch):
    state_home = tmp_path / "state"
    monkeypatch.setenv("CC_PORT_STATE_HOME", str(state_home))
    return state_home


def test_approval_is_bound_and_single_use(isolated_state_home) -> None:
    scope = {"profiles": ["codex-windows"], "actions": ["asset-download"]}
    request = create_approval_request(
        kind="asset-batch",
        operation_id="operation-1",
        plan_hash="plan-1",
        scope=scope,
        summary="Install one managed skill",
    )
    assert request.revision

    with pytest.raises(ApprovalRequiredError):
        consume_approval(
            request.approval_id,
            kind="asset-batch",
            operation_id="operation-1",
            plan_hash="plan-1",
            scope=scope,
        )

    approved = approve_approval_request(request.approval_id)
    assert approved.status == "approved"

    with pytest.raises(ApprovalRequiredError):
        consume_approval(
            request.approval_id,
            kind="asset-batch",
            operation_id="operation-1",
            plan_hash="different-plan",
            scope=scope,
        )

    consumed = consume_approval(
        request.approval_id,
        kind="asset-batch",
        operation_id="operation-1",
        plan_hash="plan-1",
        scope=scope,
    )
    assert consumed.status == "consumed"

    with pytest.raises(ApprovalRequiredError):
        consume_approval(
            request.approval_id,
            kind="asset-batch",
            operation_id="operation-1",
            plan_hash="plan-1",
            scope=scope,
        )


def test_duplicate_pending_request_is_reused(isolated_state_home) -> None:
    values = {
        "kind": "asset-action",
        "operation_id": "operation-2",
        "plan_hash": "plan-2",
        "scope": {"resource": "skill:demo", "action": "download"},
        "summary": "Install skill demo",
    }
    first = create_approval_request(**values)
    second = create_approval_request(**values)

    assert first.approval_id == second.approval_id
    assert [item.approval_id for item in list_approval_requests()] == [first.approval_id]


def test_expired_request_cannot_be_approved(isolated_state_home) -> None:
    request = create_approval_request(
        kind="asset-action",
        operation_id="operation-3",
        plan_hash="plan-3",
        scope={"resource": "skill:demo"},
        summary="Install skill demo",
    )
    stored = load_approval_request(request.approval_id)
    stored.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

    # Persist through the public state transition path by changing the file used
    # by this isolated test; no production caller can mutate returned records.
    from cc_port.services import approval

    stored.revision = approval._request_revision(stored)
    approval._save_request_unlocked(stored)

    with pytest.raises(ValueError, match="not pending: expired"):
        approve_approval_request(request.approval_id)


def test_review_identity_must_still_match_when_approving(isolated_state_home) -> None:
    request = create_approval_request(
        kind="asset-action",
        operation_id="operation-4",
        plan_hash="plan-4",
        scope={"resource": "skill:demo", "profile": "codex-windows"},
        summary="Install skill demo",
        metadata={"resource_key": "skill:demo", "profile_id": "codex-windows"},
    )

    with pytest.raises(ValueError, match="changed after it was reviewed"):
        approve_approval_request(
            request.approval_id,
            expected_operation_id=request.operation_id,
            expected_plan_hash="different-plan",
            expected_scope_hash=request.scope_hash,
            expected_revision=request.revision,
        )

    approved = approve_approval_request(
        request.approval_id,
        expected_operation_id=request.operation_id,
        expected_plan_hash=request.plan_hash,
        expected_scope_hash=request.scope_hash,
        expected_revision=request.revision,
    )
    assert approved.status == "approved"


def test_approval_revision_detects_persisted_scope_tampering(isolated_state_home) -> None:
    request = create_approval_request(
        kind="asset-batch",
        operation_id="operation-5",
        plan_hash="plan-5",
        scope={"resource_keys": ["skill:demo"]},
        summary="Upload one skill",
    )
    from cc_port.services import approval

    path = approval._approval_root() / f"{request.approval_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["plan_hash"] = "tampered"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="integrity check failed"):
        load_approval_request(request.approval_id)


@pytest.mark.parametrize("forged_status", ["approved", "consumed"])
def test_approval_revision_detects_persisted_status_tampering(
    isolated_state_home,
    forged_status: str,
) -> None:
    request = create_approval_request(
        kind="asset-action",
        operation_id="operation-6",
        plan_hash="plan-6",
        scope={"resource": "skill:demo"},
        summary="Install skill demo",
    )
    from cc_port.services import approval

    path = approval._approval_root() / f"{request.approval_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["status"] = forged_status
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="integrity check failed"):
        load_approval_request(request.approval_id)


def test_approval_revision_changes_at_each_lifecycle_transition(isolated_state_home) -> None:
    scope = {"resource": "skill:demo"}
    pending = create_approval_request(
        kind="asset-action",
        operation_id="operation-7",
        plan_hash="plan-7",
        scope=scope,
        summary="Install skill demo",
    )
    approved = approve_approval_request(pending.approval_id)
    consumed = consume_approval(
        pending.approval_id,
        kind="asset-action",
        operation_id="operation-7",
        plan_hash="plan-7",
        scope=scope,
    )

    assert len({pending.revision, approved.revision, consumed.revision}) == 3


@pytest.mark.parametrize("initial_status", ["pending", "approved"])
def test_invalidation_atomically_rejects_complete_binding(
    isolated_state_home,
    initial_status: str,
) -> None:
    scope = {"profile": "codex-windows", "actions": ["install-skill"]}
    request = create_approval_request(
        kind="ai-integration",
        operation_id="operation-8",
        plan_hash="plan-8",
        scope=scope,
        summary="Install AI integration",
    )
    if initial_status == "approved":
        request = approve_approval_request(request.approval_id)

    invalidated = invalidate_approval_request(
        request.approval_id,
        kind="ai-integration",
        operation_id="operation-8",
        plan_hash="plan-8",
        scope=scope,
    )

    assert invalidated.status == "rejected"
    assert invalidated.rejected_at
    with pytest.raises(ApprovalRequiredError, match="not approved: rejected"):
        consume_approval(
            request.approval_id,
            kind="ai-integration",
            operation_id="operation-8",
            plan_hash="plan-8",
            scope=scope,
        )


def test_invalidation_requires_the_complete_binding(isolated_state_home) -> None:
    scope = {"profile": "codex-windows", "actions": ["install-skill"]}
    request = create_approval_request(
        kind="ai-integration",
        operation_id="operation-9",
        plan_hash="plan-9",
        scope=scope,
        summary="Install AI integration",
    )

    with pytest.raises(ValueError, match="does not match"):
        invalidate_approval_request(
            request.approval_id,
            kind="ai-integration",
            operation_id="operation-9",
            plan_hash="different-plan",
            scope=scope,
        )

    assert load_approval_request(request.approval_id).status == "pending"


def test_invalidation_never_rewrites_or_revives_consumed_request(
    isolated_state_home,
) -> None:
    scope = {"profile": "codex-windows", "actions": ["install-skill"]}
    request = create_approval_request(
        kind="ai-integration",
        operation_id="operation-10",
        plan_hash="plan-10",
        scope=scope,
        summary="Install AI integration",
    )
    approve_approval_request(request.approval_id)
    consumed = consume_approval(
        request.approval_id,
        kind="ai-integration",
        operation_id="operation-10",
        plan_hash="plan-10",
        scope=scope,
    )

    terminal = invalidate_approval_request(
        request.approval_id,
        kind="ai-integration",
        operation_id="operation-10",
        plan_hash="plan-10",
        scope=scope,
    )

    assert terminal.status == "consumed"
    assert terminal.revision == consumed.revision
    with pytest.raises(ValueError, match="not pending: consumed"):
        approve_approval_request(request.approval_id)
