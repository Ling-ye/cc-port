"""GitHub OAuth device flow and narrow credential updates for the desktop app."""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ..core.config import (
    CONFIG_ENV_VAR,
    default_config_path,
    default_state_dir,
    load_raw_config,
    write_config,
)

OAUTH_CLIENT_ID_ENV_VAR = "LPM_GITHUB_OAUTH_CLIENT_ID"
# GitHub OAuth client IDs are public identifiers. Replace this empty value with
# the project's registered OAuth App client ID before producing a public build.
BUILTIN_GITHUB_OAUTH_CLIENT_ID = ""

DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_URL = "https://api.github.com"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"

PURPOSE_SCOPES: dict[str, tuple[str, ...]] = {
    "standard": ("repo",),
    "organization_owner": ("repo", "read:org"),
    "remote_delete": ("repo", "delete_repo"),
}

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


class OAuthConfigurationError(RuntimeError):
    """Raised when the desktop build has no usable OAuth application ID."""


class OAuthSessionError(RuntimeError):
    """Raised when a device authorization session is invalid."""


class GithubOwnerScopeRequired(RuntimeError):
    """Raised when organization membership needs a read:org authorization."""


class GithubDeleteScopeRequired(RuntimeError):
    """Raised before an owned repository delete without delete_repo."""


class GithubApiError(RuntimeError):
    """A sanitized GitHub API failure."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class TokenIdentity:
    login: str
    scopes: tuple[str, ...]


def oauth_client_id() -> str:
    return (
        os.environ.get(OAUTH_CLIENT_ID_ENV_VAR, "").strip()
        or BUILTIN_GITHUB_OAUTH_CLIENT_ID.strip()
    )


def auth_status() -> dict[str, Any]:
    raw_cfg = load_raw_config()
    env_token = os.environ.get(CONFIG_ENV_VAR, "").strip()
    config_token = raw_cfg.github.token.strip()
    effective_token = env_token or config_token
    source = "env" if env_token else ("config" if config_token else "none")
    base = {
        "state": "missing" if not effective_token else "connected",
        "source": source,
        "login": "",
        "scopes": [],
        "token_preview": mask_token(effective_token),
        "config_token_preview": mask_token(config_token),
        "can_reveal": bool(config_token),
        "can_clear": bool(config_token),
        "env_override": bool(env_token),
        "oauth_configured": bool(oauth_client_id()),
        "error": "",
    }
    if not effective_token:
        return base
    try:
        identity = validate_token(effective_token)
    except (GithubApiError, URLError, ValueError) as exc:
        base["state"] = "invalid"
        base["error"] = str(exc)
        return base
    base["login"] = identity.login
    base["scopes"] = list(identity.scopes)
    return base


def start_authorization(purpose: str) -> dict[str, Any]:
    purpose = str(purpose or "").strip()
    base_scopes = PURPOSE_SCOPES.get(purpose)
    if base_scopes is None:
        raise ValueError("Unsupported GitHub authorization purpose.")
    if os.environ.get(CONFIG_ENV_VAR, "").strip():
        raise OAuthConfigurationError(
            f"{CONFIG_ENV_VAR} overrides the token stored by the desktop app. "
            "Update or remove the environment token before authorizing in the GUI."
        )
    client_id = oauth_client_id()
    if not client_id:
        raise OAuthConfigurationError(
            f"GitHub OAuth is not configured. Set {OAUTH_CLIENT_ID_ENV_VAR} for development "
            "or embed the registered OAuth App client ID in the release build."
        )

    scopes = _authorization_scopes(base_scopes)
    data, _headers = _request_json(
        DEVICE_CODE_URL,
        data={"client_id": client_id, "scope": " ".join(scopes)},
    )
    device_code = _required_response_str(data, "device_code")
    user_code = _required_response_str(data, "user_code")
    verification_uri = _required_response_str(data, "verification_uri")
    expires_in = _positive_response_int(data, "expires_in")
    interval = max(1, int(data.get("interval") or 5))
    session_id = secrets.token_urlsafe(24)
    now = time.time()
    _write_session(
        session_id,
        {
            "device_code": device_code,
            "purpose": purpose,
            "scopes": list(scopes),
            "expires_at": now + expires_in,
            "interval": interval,
            "next_poll_at": now + interval,
        },
    )
    return {
        "session_id": session_id,
        "user_code": user_code,
        "verification_uri": verification_uri,
        "expires_in": expires_in,
        "interval": interval,
        "purpose": purpose,
        "scopes": list(scopes),
    }


def poll_authorization(session_id: str) -> dict[str, Any]:
    session = _read_session(session_id)
    now = time.time()
    if now >= float(session["expires_at"]):
        _delete_session(session_id)
        return {"state": "expired"}

    next_poll_at = float(session.get("next_poll_at") or 0)
    if now < next_poll_at:
        return {
            "state": "pending",
            "retry_after": max(1, int(next_poll_at - now + 0.999)),
        }

    client_id = oauth_client_id()
    if not client_id:
        raise OAuthConfigurationError("GitHub OAuth client ID is no longer configured.")
    response, _headers = _request_json(
        ACCESS_TOKEN_URL,
        data={
            "client_id": client_id,
            "device_code": str(session["device_code"]),
            "grant_type": DEVICE_GRANT_TYPE,
        },
    )
    error = str(response.get("error") or "")
    interval = int(session.get("interval") or 5)
    if error == "authorization_pending":
        session["next_poll_at"] = now + interval
        _write_session(session_id, session)
        return {"state": "pending", "retry_after": interval}
    if error == "slow_down":
        interval += 5
        session["interval"] = interval
        session["next_poll_at"] = now + interval
        _write_session(session_id, session)
        return {"state": "slow_down", "retry_after": interval}
    if error in {"expired_token", "access_denied"}:
        _delete_session(session_id)
        return {"state": "expired" if error == "expired_token" else "denied"}
    if error:
        raise OAuthSessionError(
            str(response.get("error_description") or "GitHub authorization failed.")
        )

    token = _required_response_str(response, "access_token")
    identity = validate_token(token)
    required_scopes = {str(scope) for scope in session.get("scopes", [])}
    actual_scopes = set(identity.scopes)
    missing = sorted(required_scopes - actual_scopes)
    if missing:
        _delete_session(session_id)
        raise OAuthSessionError(
            f"GitHub authorization did not grant required scopes: {', '.join(missing)}."
        )

    cfg = load_raw_config()
    cfg.github.token = token
    write_config(cfg, cfg.source_path or default_config_path())
    _delete_session(session_id)
    return {
        "state": "authorized",
        "login": identity.login,
        "scopes": list(identity.scopes),
        "token_preview": mask_token(token),
    }


def cancel_authorization(session_id: str) -> dict[str, bool]:
    existed = _session_path(session_id).is_file()
    _delete_session(session_id)
    return {"cancelled": existed}


def reveal_config_token() -> dict[str, str]:
    token = load_raw_config().github.token.strip()
    if not token:
        raise ValueError("No token is stored in config.toml.")
    return {"token": token}


def clear_config_token() -> dict[str, Any]:
    cfg = load_raw_config()
    cfg.github.token = ""
    write_config(cfg, cfg.source_path or default_config_path())
    return auth_status()


def set_github_owner(owner: str) -> dict[str, str]:
    value = str(owner or "").strip()
    if not _OWNER_RE.fullmatch(value):
        raise ValueError("Enter a valid GitHub user or organization name.")
    effective_token = os.environ.get(CONFIG_ENV_VAR, "").strip() or load_raw_config().github.token.strip()
    if not effective_token:
        raise ValueError("Connect GitHub before saving the repository owner.")

    identity = validate_token(effective_token)
    account, _headers = _request_json(
        f"{GITHUB_API_URL}/users/{quote(value, safe='')}",
        token=effective_token,
    )
    account_type = str(account.get("type") or "")
    canonical = str(account.get("login") or value)
    if account_type == "User":
        if canonical.lower() != identity.login.lower():
            raise ValueError("A personal repository owner must match the authorized GitHub account.")
    elif account_type == "Organization":
        try:
            membership, _headers = _request_json(
                f"{GITHUB_API_URL}/user/memberships/orgs/{quote(canonical, safe='')}",
                token=effective_token,
            )
        except GithubApiError as exc:
            if exc.status == 403 and "read:org" not in identity.scopes:
                raise GithubOwnerScopeRequired(
                    "Organization membership verification requires a GitHub authorization "
                    "with read:org."
                ) from exc
            if exc.status in {403, 404}:
                raise ValueError(
                    f"The authorized account is not an active member of {canonical}."
                ) from exc
            raise
        if str(membership.get("state") or "") != "active":
            raise ValueError(f"The GitHub organization membership for {canonical} is not active.")
    else:
        raise ValueError("The repository owner must be a GitHub user or organization.")

    cfg = load_raw_config()
    cfg.github.owner = canonical
    write_config(cfg, cfg.source_path or default_config_path())
    return {
        "owner": canonical,
        "owner_type": account_type.lower(),
        "authorized_login": identity.login,
    }


def require_authorization(purpose: str) -> TokenIdentity:
    """Require the effective token to contain the server-defined purpose scopes."""
    required = PURPOSE_SCOPES.get(purpose)
    if required is None:
        raise ValueError("Unsupported GitHub authorization purpose.")
    token = os.environ.get(CONFIG_ENV_VAR, "").strip() or load_raw_config().github.token.strip()
    if not token:
        if purpose == "remote_delete":
            raise GithubDeleteScopeRequired(
                "Connect GitHub and authorize delete_repo before deleting an owned repository."
            )
        raise ValueError("Connect GitHub before continuing.")
    identity = validate_token(token)
    missing = sorted(set(required) - set(identity.scopes))
    if missing:
        if purpose == "remote_delete":
            raise GithubDeleteScopeRequired(
                "Deleting an owned GitHub repository requires an on-demand delete_repo authorization."
            )
        raise GithubOwnerScopeRequired(
            f"GitHub authorization is missing required scopes: {', '.join(missing)}."
        )
    return identity


def validate_token(token: str) -> TokenIdentity:
    data, headers = _request_json(f"{GITHUB_API_URL}/user", token=token)
    login = _required_response_str(data, "login")
    raw_scopes = headers.get("x-oauth-scopes", "")
    scopes = tuple(sorted({scope.strip() for scope in raw_scopes.split(",") if scope.strip()}))
    return TokenIdentity(login=login, scopes=scopes)


def mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}{'*' * max(4, len(token) - 8)}{token[-4:]}"


def _authorization_scopes(base_scopes: tuple[str, ...]) -> tuple[str, ...]:
    """Preserve relevant scopes when an existing config token is upgraded."""
    retained: set[str] = set(base_scopes)
    token = load_raw_config().github.token.strip()
    if token:
        try:
            retained.update(
                scope
                for scope in validate_token(token).scopes
                if scope in {"repo", "read:org", "delete_repo"}
            )
        except (GithubApiError, URLError, ValueError):
            pass
    return tuple(scope for scope in ("repo", "read:org", "delete_repo") if scope in retained)


def _request_json(
    url: str,
    *,
    data: dict[str, str] | None = None,
    token: str = "",
) -> tuple[dict[str, Any], dict[str, str]]:
    body = urlencode(data).encode("utf-8") if data is not None else None
    headers = {
        "Accept": "application/json",
        "User-Agent": "LingyePluginMarketplace-Desktop",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed GitHub endpoints
            payload = json.loads(response.read().decode("utf-8"))
            response_headers = {key.lower(): value for key, value in response.headers.items()}
    except HTTPError as exc:
        message = f"GitHub request failed with HTTP {exc.code}."
        try:
            error_payload = json.loads(exc.read().decode("utf-8"))
            message = str(error_payload.get("message") or message)
        except (ValueError, OSError):
            pass
        raise GithubApiError(exc.code, message) from exc
    if not isinstance(payload, dict):
        raise ValueError("GitHub returned an invalid JSON response.")
    return payload, response_headers


def _session_dir() -> Path:
    path = default_state_dir() / "oauth"
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:  # pragma: no cover - Windows ACLs inherit from the user profile
        pass
    return path


def _session_path(session_id: str) -> Path:
    value = str(session_id or "").strip()
    if not _SESSION_ID_RE.fullmatch(value):
        raise OAuthSessionError("Invalid GitHub authorization session.")
    return _session_dir() / f"{value}.json"


def _read_session(session_id: str) -> dict[str, Any]:
    path = _session_path(session_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OAuthSessionError("GitHub authorization session was not found or has expired.") from exc
    except (OSError, ValueError) as exc:
        raise OAuthSessionError("GitHub authorization session is unreadable.") from exc
    if not isinstance(value, dict):
        raise OAuthSessionError("GitHub authorization session is invalid.")
    return value


def _write_session(session_id: str, value: dict[str, Any]) -> None:
    path = _session_path(session_id)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=True)
        try:
            os.chmod(temporary_path, 0o600)
        except OSError:  # pragma: no cover
            pass
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _delete_session(session_id: str) -> None:
    _session_path(session_id).unlink(missing_ok=True)


def _required_response_str(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ValueError(f"GitHub response is missing {key}.")
    return value


def _positive_response_int(data: dict[str, Any], key: str) -> int:
    try:
        value = int(data.get(key))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"GitHub response has an invalid {key}.") from exc
    if value <= 0:
        raise ValueError(f"GitHub response has an invalid {key}.")
    return value
