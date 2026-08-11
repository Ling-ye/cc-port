"""Local, single-use approvals for AI initiated write operations.

The MCP or CLI caller is not an approval authority.  A model-supplied boolean
such as ``confirmed=true`` or the compatibility flag ``--yes`` therefore never
authorizes a write. Planning code creates one pending request, the trusted
desktop surface approves it, and apply consumes the grant after matching the
complete binding.

The revision below detects accidental or unsynchronized state corruption; it
is not a keyed signature and does not create an OS-level security boundary
against code with unrestricted access to the current user's state directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from ..core.config import default_state_dir
from .state_lock import acquire_target_locks

APPROVAL_SCHEMA_VERSION = 2
DEFAULT_APPROVAL_TTL_SECONDS = 30 * 60
ApprovalStatus = Literal["pending", "approved", "consumed", "rejected", "expired"]


@dataclass
class ApprovalRequest:
    approval_id: str
    kind: str
    operation_id: str
    plan_hash: str
    scope_hash: str
    summary: str
    status: ApprovalStatus
    created_at: str
    expires_at: str
    approved_at: str = ""
    consumed_at: str = ""
    rejected_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    revision: str = ""
    schema_version: int = APPROVAL_SCHEMA_VERSION

    @property
    def active(self) -> bool:
        return self.status in {"pending", "approved"} and not _is_expired(self)


class ApprovalRequiredError(RuntimeError):
    """Raised when an apply operation has no matching approved grant."""

    def __init__(self, message: str, *, approval_id: str = "") -> None:
        self.approval_id = approval_id
        super().__init__(message)


def create_approval_request(
    *,
    kind: str,
    operation_id: str,
    plan_hash: str,
    scope: dict[str, Any],
    summary: str,
    metadata: dict[str, Any] | None = None,
    ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
) -> ApprovalRequest:
    """Create or reuse a pending request for one exact plan and scope."""
    normalized_kind = _required_token(kind, "kind")
    normalized_operation = _required_token(operation_id, "operation_id")
    normalized_plan_hash = _required_token(plan_hash, "plan_hash")
    normalized_summary = str(summary).strip() or "CC Port write operation"
    normalized_metadata = _safe_metadata(metadata or {})
    if ttl_seconds < 60 or ttl_seconds > 24 * 60 * 60:
        raise ValueError("Approval TTL must be between 60 seconds and 24 hours.")
    scope_hash = approval_scope_hash(scope)
    root = _approval_root()
    root.mkdir(parents=True, exist_ok=True)
    with acquire_target_locks([root], timeout_seconds=10.0):
        for request in _list_requests_unlocked():
            request = _expire_if_needed(request)
            if (
                request.status == "pending"
                and request.kind == normalized_kind
                and request.operation_id == normalized_operation
                and request.plan_hash == normalized_plan_hash
                and request.scope_hash == scope_hash
                and request.summary == normalized_summary
                and request.metadata == normalized_metadata
            ):
                return request
        now = datetime.now(timezone.utc)
        request = ApprovalRequest(
            approval_id=uuid4().hex,
            kind=normalized_kind,
            operation_id=normalized_operation,
            plan_hash=normalized_plan_hash,
            scope_hash=scope_hash,
            summary=normalized_summary,
            status="pending",
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(),
            metadata=normalized_metadata,
        )
        request.revision = _request_revision(request)
        _save_request_unlocked(request)
        return request


def list_approval_requests(
    *,
    statuses: set[ApprovalStatus] | None = None,
) -> list[ApprovalRequest]:
    """Return newest requests first while persisting expiry transitions."""
    root = _approval_root()
    if not root.is_dir():
        return []
    with acquire_target_locks([root], timeout_seconds=10.0):
        requests = [_expire_if_needed(item) for item in _list_requests_unlocked()]
    if statuses is not None:
        requests = [item for item in requests if item.status in statuses]
    return sorted(requests, key=lambda item: item.created_at, reverse=True)


def load_approval_request(approval_id: str) -> ApprovalRequest:
    safe_id = _approval_id(approval_id)
    path = _approval_root() / f"{safe_id}.json"
    with acquire_target_locks([path], timeout_seconds=10.0):
        request = _load_request_unlocked(path)
        return _expire_if_needed(request)


def approve_approval_request(
    approval_id: str,
    *,
    expected_operation_id: str = "",
    expected_plan_hash: str = "",
    expected_scope_hash: str = "",
    expected_revision: str = "",
) -> ApprovalRequest:
    """Approve one pending request; this function is not exposed as an MCP tool."""
    safe_id = _approval_id(approval_id)
    path = _approval_root() / f"{safe_id}.json"
    with acquire_target_locks([path], timeout_seconds=10.0):
        request = _expire_if_needed(_load_request_unlocked(path))
        _validate_expected_request(
            request,
            operation_id=expected_operation_id,
            plan_hash=expected_plan_hash,
            scope_hash=expected_scope_hash,
            revision=expected_revision,
        )
        if request.status == "approved":
            return request
        if request.status != "pending":
            raise ValueError(f"Approval request is not pending: {request.status}.")
        request.status = "approved"
        request.approved_at = _now()
        _save_request_unlocked(request)
        return request


def reject_approval_request(
    approval_id: str,
    *,
    expected_operation_id: str = "",
    expected_plan_hash: str = "",
    expected_scope_hash: str = "",
    expected_revision: str = "",
) -> ApprovalRequest:
    safe_id = _approval_id(approval_id)
    path = _approval_root() / f"{safe_id}.json"
    with acquire_target_locks([path], timeout_seconds=10.0):
        request = _expire_if_needed(_load_request_unlocked(path))
        _validate_expected_request(
            request,
            operation_id=expected_operation_id,
            plan_hash=expected_plan_hash,
            scope_hash=expected_scope_hash,
            revision=expected_revision,
        )
        if request.status == "rejected":
            return request
        if request.status not in {"pending", "approved"}:
            raise ValueError(f"Approval request cannot be rejected: {request.status}.")
        request.status = "rejected"
        request.rejected_at = _now()
        _save_request_unlocked(request)
        return request


def invalidate_approval_request(
    approval_id: str,
    *,
    kind: str,
    operation_id: str,
    plan_hash: str,
    scope: dict[str, Any],
) -> ApprovalRequest:
    """Atomically invalidate one exact approval binding after a stale precheck.

    ``rejected`` is the wire-compatible terminal representation of an
    invalidated request. Already-terminal requests never transition back to an
    active state, and a consumed request is never rewritten as rejected.
    """
    safe_id = _approval_id(approval_id)
    path = _approval_root() / f"{safe_id}.json"
    expected = (
        _required_token(kind, "kind"),
        _required_token(operation_id, "operation_id"),
        _required_token(plan_hash, "plan_hash"),
        approval_scope_hash(scope),
    )
    with acquire_target_locks([path], timeout_seconds=10.0):
        request = _expire_if_needed(_load_request_unlocked(path))
        actual = (
            request.kind,
            request.operation_id,
            request.plan_hash,
            request.scope_hash,
        )
        if actual != expected:
            raise ValueError(
                "Approval request does not match the invalidated operation, plan, or scope."
            )
        if request.status in {"rejected", "consumed", "expired"}:
            return request
        if request.status not in {"pending", "approved"}:  # pragma: no cover - schema guard
            raise ValueError(f"Approval request cannot be invalidated: {request.status}.")
        request.status = "rejected"
        request.rejected_at = _now()
        _save_request_unlocked(request)
        return request


def consume_approval(
    approval_id: str,
    *,
    kind: str,
    operation_id: str,
    plan_hash: str,
    scope: dict[str, Any],
) -> ApprovalRequest:
    """Atomically consume an approved request bound to the exact apply input."""
    safe_id = _approval_id(approval_id)
    path = _approval_root() / f"{safe_id}.json"
    with acquire_target_locks([path], timeout_seconds=10.0):
        request = _expire_if_needed(_load_request_unlocked(path))
        if request.status != "approved":
            raise ApprovalRequiredError(
                f"Approval request is not approved: {request.status}.",
                approval_id=request.approval_id,
            )
        expected = (
            _required_token(kind, "kind"),
            _required_token(operation_id, "operation_id"),
            _required_token(plan_hash, "plan_hash"),
            approval_scope_hash(scope),
        )
        actual = (
            request.kind,
            request.operation_id,
            request.plan_hash,
            request.scope_hash,
        )
        if actual != expected:
            raise ApprovalRequiredError(
                "Approval request does not match this operation, plan, or scope.",
                approval_id=request.approval_id,
            )
        request.status = "consumed"
        request.consumed_at = _now()
        _save_request_unlocked(request)
        return request


def approval_scope_hash(scope: dict[str, Any]) -> str:
    normalized = _normalize_json(scope)
    payload = json.dumps(
        normalized,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _approval_root() -> Path:
    return default_state_dir() / "approvals"


def _list_requests_unlocked() -> list[ApprovalRequest]:
    root = _approval_root()
    if not root.is_dir():
        return []
    requests: list[ApprovalRequest] = []
    for path in root.glob("*.json"):
        try:
            requests.append(_load_request_unlocked(path))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return requests


def _load_request_unlocked(path: Path) -> ApprovalRequest:
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(f"Unknown approval request: {path.stem}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Approval request must be a JSON object.")
    if raw.get("schema_version") != APPROVAL_SCHEMA_VERSION:
        raise ValueError("Unsupported approval request schema.")
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Approval request metadata must be an object.")
    raw["metadata"] = metadata
    request = ApprovalRequest(**raw)
    if not request.revision or request.revision != _request_revision(request):
        raise ValueError("Approval request integrity check failed.")
    return request


def _save_request_unlocked(request: ApprovalRequest) -> Path:
    root = _approval_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_approval_id(request.approval_id)}.json"
    request.revision = _request_revision(request)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=root)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(asdict(request), handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path


def _expire_if_needed(request: ApprovalRequest) -> ApprovalRequest:
    if request.status not in {"pending", "approved"} or not _is_expired(request):
        return request
    request.status = "expired"
    _save_request_unlocked(request)
    return request


def _is_expired(request: ApprovalRequest) -> bool:
    try:
        expires = datetime.fromisoformat(request.expires_at)
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= expires


def _approval_id(value: str) -> str:
    safe = str(value).strip().lower()
    if len(safe) != 32 or any(char not in "0123456789abcdef" for char in safe):
        raise ValueError("Invalid approval id.")
    return safe


def _required_token(value: str, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized or len(normalized) > 256 or any(ord(char) < 32 for char in normalized):
        raise ValueError(f"Invalid {field_name}.")
    return normalized


def _safe_metadata(value: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_json(value)
    if not isinstance(normalized, dict):
        raise ValueError("Approval metadata must be an object.")
    encoded = json.dumps(normalized, ensure_ascii=True, sort_keys=True)
    if len(encoded) > 16_384:
        raise ValueError("Approval metadata is too large.")
    return normalized


def _request_revision(request: ApprovalRequest) -> str:
    return approval_scope_hash(
        {
            "schema_version": request.schema_version,
            "approval_id": request.approval_id,
            "kind": request.kind,
            "operation_id": request.operation_id,
            "plan_hash": request.plan_hash,
            "scope_hash": request.scope_hash,
            "summary": request.summary,
            "status": request.status,
            "created_at": request.created_at,
            "expires_at": request.expires_at,
            "approved_at": request.approved_at,
            "consumed_at": request.consumed_at,
            "rejected_at": request.rejected_at,
            "metadata": request.metadata,
        }
    )


def _validate_expected_request(
    request: ApprovalRequest,
    *,
    operation_id: str,
    plan_hash: str,
    scope_hash: str,
    revision: str,
) -> None:
    supplied = [operation_id, plan_hash, scope_hash, revision]
    if not any(supplied):
        return
    if not all(str(value).strip() for value in supplied):
        raise ValueError("Approval review identity is incomplete.")
    expected = (
        _required_token(operation_id, "operation_id"),
        _required_token(plan_hash, "plan_hash"),
        _required_token(scope_hash, "scope_hash"),
        _required_token(revision, "revision"),
    )
    actual = (
        request.operation_id,
        request.plan_hash,
        request.scope_hash,
        request.revision,
    )
    if expected != actual:
        raise ValueError("Approval request changed after it was reviewed.")


def _normalize_json(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _normalize_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_normalize_json(item) for item in value]
    raise ValueError(f"Unsupported approval value type: {type(value).__name__}.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
