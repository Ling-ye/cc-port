from __future__ import annotations

import json
from pathlib import Path

import pytest

from lpm.core.config import Config, GithubConfig, load_raw_config, write_config
from lpm.services import github_oauth


@pytest.fixture
def oauth_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    config_path = tmp_path / "config.toml"
    state_path = tmp_path / "state"
    monkeypatch.setenv("LPM_CONFIG", str(config_path))
    monkeypatch.setenv("LPM_STATE_HOME", str(state_path))
    monkeypatch.setenv("LPM_GITHUB_OAUTH_CLIENT_ID", "registered-client-id")
    monkeypatch.delenv("LPM_GITHUB_TOKEN", raising=False)
    write_config(Config(), config_path)
    return config_path, state_path


def test_auth_status_masks_config_token_and_never_returns_plaintext(
    oauth_environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, _state_path = oauth_environment
    write_config(Config(github=GithubConfig(token="gho_1234567890abcdef")), config_path)
    monkeypatch.setattr(
        github_oauth,
        "validate_token",
        lambda _token: github_oauth.TokenIdentity("lingye", ("repo",)),
    )

    result = github_oauth.auth_status()

    assert result["state"] == "connected"
    assert result["login"] == "lingye"
    assert result["source"] == "config"
    assert result["token_preview"].startswith("gho_")
    assert result["token_preview"].endswith("cdef")
    assert "gho_1234567890abcdef" not in json.dumps(result)


def test_device_flow_preserves_existing_scopes_and_commits_only_after_validation(
    oauth_environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, state_path = oauth_environment
    write_config(Config(github=GithubConfig(token="old-token")), config_path)
    now = {"value": 100.0}
    monkeypatch.setattr(github_oauth.time, "time", lambda: now["value"])
    requested_scopes: list[str] = []

    def fake_validate(token: str) -> github_oauth.TokenIdentity:
        if token == "old-token":
            return github_oauth.TokenIdentity("lingye", ("repo", "read:org"))
        assert token == "new-token"
        return github_oauth.TokenIdentity("lingye", ("delete_repo", "read:org", "repo"))

    def fake_request(url: str, *, data=None, token: str = ""):
        if url == github_oauth.DEVICE_CODE_URL:
            requested_scopes.append(data["scope"])
            return {
                "device_code": "machine-secret-device-code",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            }, {}
        assert url == github_oauth.ACCESS_TOKEN_URL
        assert data["device_code"] == "machine-secret-device-code"
        return {"access_token": "new-token"}, {}

    monkeypatch.setattr(github_oauth, "validate_token", fake_validate)
    monkeypatch.setattr(github_oauth, "_request_json", fake_request)

    session = github_oauth.start_authorization("remote_delete")

    assert requested_scopes == ["repo read:org delete_repo"]
    assert "device_code" not in session
    assert load_raw_config(config_path).github.token == "old-token"
    session_file = state_path / "oauth" / f"{session['session_id']}.json"
    assert session_file.is_file()
    assert github_oauth.poll_authorization(session["session_id"])["state"] == "pending"

    now["value"] = 106.0
    result = github_oauth.poll_authorization(session["session_id"])

    assert result["state"] == "authorized"
    assert "token" not in result
    assert load_raw_config(config_path).github.token == "new-token"
    assert not session_file.exists()


def test_failed_authorization_does_not_replace_existing_token(
    oauth_environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, _state_path = oauth_environment
    write_config(Config(github=GithubConfig(token="old-token")), config_path)
    now = {"value": 100.0}
    monkeypatch.setattr(github_oauth.time, "time", lambda: now["value"])
    monkeypatch.setattr(
        github_oauth,
        "validate_token",
        lambda _token: github_oauth.TokenIdentity("lingye", ("repo",)),
    )

    def fake_request(url: str, *, data=None, token: str = ""):
        if url == github_oauth.DEVICE_CODE_URL:
            return {
                "device_code": "device",
                "user_code": "CODE",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            }, {}
        return {"error": "access_denied"}, {}

    monkeypatch.setattr(github_oauth, "_request_json", fake_request)
    session = github_oauth.start_authorization("standard")
    now["value"] = 106.0

    assert github_oauth.poll_authorization(session["session_id"])["state"] == "denied"
    assert load_raw_config(config_path).github.token == "old-token"


def test_environment_token_blocks_gui_reauthorization(
    oauth_environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LPM_GITHUB_TOKEN", "environment-token")

    with pytest.raises(github_oauth.OAuthConfigurationError, match="overrides"):
        github_oauth.start_authorization("standard")


def test_builtin_client_id_is_used_when_development_override_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LPM_GITHUB_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.setattr(github_oauth, "BUILTIN_GITHUB_OAUTH_CLIENT_ID", "release-client-id")

    assert github_oauth.oauth_client_id() == "release-client-id"


def test_device_poll_handles_slow_down_and_expiration(
    oauth_environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    now = {"value": 100.0}
    responses = iter([
        ({
            "device_code": "device",
            "user_code": "CODE",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 30,
            "interval": 5,
        }, {}),
        ({"error": "slow_down"}, {}),
        ({"error": "expired_token"}, {}),
    ])
    monkeypatch.setattr(github_oauth.time, "time", lambda: now["value"])
    monkeypatch.setattr(github_oauth, "_request_json", lambda *_args, **_kwargs: next(responses))

    session = github_oauth.start_authorization("standard")
    now["value"] = 106.0
    slowed = github_oauth.poll_authorization(session["session_id"])
    assert slowed == {"state": "slow_down", "retry_after": 10}

    now["value"] = 117.0
    assert github_oauth.poll_authorization(session["session_id"])["state"] == "expired"


def test_device_session_can_be_cancelled(
    oauth_environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        github_oauth,
        "_request_json",
        lambda *_args, **_kwargs: ({
            "device_code": "device",
            "user_code": "CODE",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 30,
            "interval": 5,
        }, {}),
    )
    session = github_oauth.start_authorization("standard")

    assert github_oauth.cancel_authorization(session["session_id"]) == {"cancelled": True}
    with pytest.raises(github_oauth.OAuthSessionError):
        github_oauth.poll_authorization(session["session_id"])


def test_owner_validation_updates_only_owner(
    oauth_environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, _state_path = oauth_environment
    cfg = Config(github=GithubConfig(token="token", owner="old", repo_prefix="keep-"))
    cfg.resources.branch = "release"
    write_config(cfg, config_path)
    monkeypatch.setattr(
        github_oauth,
        "validate_token",
        lambda _token: github_oauth.TokenIdentity("Lingye", ("repo",)),
    )
    monkeypatch.setattr(
        github_oauth,
        "_request_json",
        lambda _url, **_kwargs: ({"type": "User", "login": "Lingye"}, {}),
    )

    result = github_oauth.set_github_owner("Lingye")
    updated = load_raw_config(config_path)

    assert result["owner"] == "Lingye"
    assert updated.github.owner == "Lingye"
    assert updated.github.token == "token"
    assert updated.github.repo_prefix == "keep-"
    assert updated.resources.branch == "release"


def test_organization_owner_requests_read_org_only_when_membership_is_hidden(
    oauth_environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, _state_path = oauth_environment
    write_config(Config(github=GithubConfig(token="token")), config_path)
    monkeypatch.setattr(
        github_oauth,
        "validate_token",
        lambda _token: github_oauth.TokenIdentity("Lingye", ("repo",)),
    )

    def fake_request(url: str, **_kwargs):
        if "/users/" in url:
            return {"type": "Organization", "login": "ExampleOrg"}, {}
        raise github_oauth.GithubApiError(403, "Resource not accessible")

    monkeypatch.setattr(github_oauth, "_request_json", fake_request)

    with pytest.raises(github_oauth.GithubOwnerScopeRequired):
        github_oauth.set_github_owner("ExampleOrg")
    assert load_raw_config(config_path).github.owner == ""


def test_active_organization_member_can_be_saved(
    oauth_environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, _state_path = oauth_environment
    write_config(Config(github=GithubConfig(token="token")), config_path)
    monkeypatch.setattr(
        github_oauth,
        "validate_token",
        lambda _token: github_oauth.TokenIdentity("Lingye", ("read:org", "repo")),
    )

    def fake_request(url: str, **_kwargs):
        if "/users/" in url:
            return {"type": "Organization", "login": "ExampleOrg"}, {}
        return {"state": "active"}, {}

    monkeypatch.setattr(github_oauth, "_request_json", fake_request)

    result = github_oauth.set_github_owner("ExampleOrg")

    assert result["owner_type"] == "organization"
    assert load_raw_config(config_path).github.owner == "ExampleOrg"


def test_owned_remote_delete_requires_delete_repo_scope(
    oauth_environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, _state_path = oauth_environment
    write_config(Config(github=GithubConfig(token="token")), config_path)
    monkeypatch.setattr(
        github_oauth,
        "validate_token",
        lambda _token: github_oauth.TokenIdentity("lingye", ("repo",)),
    )

    with pytest.raises(github_oauth.GithubDeleteScopeRequired):
        github_oauth.require_authorization("remote_delete")


def test_token_reveal_and_clear_only_touch_config_token(
    oauth_environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, _state_path = oauth_environment
    cfg = Config(github=GithubConfig(token="config-token", owner="Lingye"))
    write_config(cfg, config_path)
    monkeypatch.setenv("LPM_GITHUB_TOKEN", "environment-token")
    monkeypatch.setattr(
        github_oauth,
        "validate_token",
        lambda token: github_oauth.TokenIdentity(token, ("repo",)),
    )

    assert github_oauth.reveal_config_token() == {"token": "config-token"}
    status = github_oauth.clear_config_token()

    assert load_raw_config(config_path).github.token == ""
    assert load_raw_config(config_path).github.owner == "Lingye"
    assert status["source"] == "env"
    assert status["can_clear"] is False
