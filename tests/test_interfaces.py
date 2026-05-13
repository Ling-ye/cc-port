import json
from pathlib import Path

from lpm.core.config import load_raw_config
from lpm.interfaces.desktop_api import run_action


def test_desktop_api_platforms_smoke() -> None:
    result = run_action("platforms", {})

    assert result["ok"] is True
    assert "platforms" in result["data"]


def test_public_interface_modules_importable() -> None:
    import lpm.interfaces.cli
    import lpm.interfaces.mcp_server

    assert lpm.interfaces.cli.app is not None
    assert lpm.interfaces.mcp_server.main is not None


def test_config_get_redacts_token(monkeypatch, tmp_path: Path) -> None:
    token = "ghp_1234567890abcd"
    _write_config(monkeypatch, tmp_path, token=token)

    result = run_action("config_get", {})

    assert result["ok"] is True
    data = result["data"]
    assert data["token_source"] == "config"
    assert data["token_preview"].startswith("ghp_")
    assert data["token_preview"].endswith("abcd")
    assert token not in json.dumps(data)


def test_config_save_replaces_and_clears_token(monkeypatch, tmp_path: Path) -> None:
    _write_config(monkeypatch, tmp_path, token="oldtoken1234")
    draft = run_action("config_get", {})["data"]["config"]

    replaced = run_action(
        "config_save",
        {
            "draft": draft,
            "token_action": "replace",
            "new_token": "newtoken5678",
            "prepare_resource_repo": False,
        },
    )
    assert replaced["ok"] is True
    assert load_raw_config().github.token == "newtoken5678"

    cleared = run_action(
        "config_save",
        {"draft": draft, "token_action": "clear", "prepare_resource_repo": False},
    )
    assert cleared["ok"] is True
    assert load_raw_config().github.token == ""


def test_config_save_does_not_write_env_token(monkeypatch, tmp_path: Path) -> None:
    _write_config(monkeypatch, tmp_path, token="filetoken1234")
    monkeypatch.setenv("LPM_GITHUB_TOKEN", "envtoken9999")
    draft = run_action("config_get", {})["data"]["config"]
    draft["github"]["owner"] = "new-owner"

    result = run_action(
        "config_save",
        {"draft": draft, "token_action": "preserve", "prepare_resource_repo": False},
    )

    assert result["ok"] is True
    raw = load_raw_config()
    assert raw.github.token == "filetoken1234"
    assert raw.github.owner == "new-owner"


def test_config_check_reports_missing_remote_and_local(monkeypatch, tmp_path: Path) -> None:
    _write_config(monkeypatch, tmp_path, token="token123456")
    monkeypatch.setattr("lpm.interfaces.desktop_api.GithubClient", _MissingRepoClient)
    draft = run_action("config_get", {})["data"]["config"]
    draft["github"]["owner"] = "octo"
    draft["resources"]["repo_name"] = "missing-resources"
    draft["resources"]["local_path"] = str(tmp_path / "missing-local")

    result = run_action("config_check", {"draft": draft})

    assert result["ok"] is True
    missing = {item["id"] for item in result["data"]["missing"]}
    assert {"remote_repo", "local_path"} <= missing
    assert result["data"]["can_prepare"] is True


def test_config_check_reports_local_non_git(monkeypatch, tmp_path: Path) -> None:
    _write_config(monkeypatch, tmp_path, token="token123456")
    monkeypatch.setattr("lpm.interfaces.desktop_api.GithubClient", _ExistingRepoClient)
    local = tmp_path / "not-git"
    local.mkdir()
    draft = run_action("config_get", {})["data"]["config"]
    draft["resources"]["local_path"] = str(local)

    result = run_action("config_check", {"draft": draft})

    assert result["ok"] is True
    missing = {item["id"] for item in result["data"]["missing"]}
    assert "local_git" in missing
    assert "remote_repo" not in missing


def test_config_save_prepare_updates_resource_config(monkeypatch, tmp_path: Path) -> None:
    _write_config(monkeypatch, tmp_path, token="token123456")
    prepared_path = tmp_path / "prepared"

    def fake_prepare(cfg, token: str):
        assert token == "token123456"
        cfg.resources.repo_name = "prepared-resources"
        cfg.resources.repo_url = "https://github.com/octo/prepared-resources"
        cfg.resources.local_path = str(prepared_path)
        return {"created": True, "repo_url": cfg.resources.repo_url, "local_path": cfg.resources.local_path}

    monkeypatch.setattr("lpm.interfaces.desktop_api._prepare_resource_target", fake_prepare)
    draft = run_action("config_get", {})["data"]["config"]
    draft["github"]["owner"] = "octo"
    draft["resources"]["repo_name"] = "prepared-resources"

    result = run_action(
        "config_save",
        {"draft": draft, "token_action": "preserve", "prepare_resource_repo": True},
    )

    assert result["ok"] is True
    raw = load_raw_config()
    assert raw.resources.repo_url == "https://github.com/octo/prepared-resources"
    assert raw.resources.local_path == str(prepared_path)
    assert result["data"]["resource_repo"]["created"] is True


def _write_config(monkeypatch, tmp_path: Path, *, token: str) -> Path:
    monkeypatch.delenv("LPM_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("LPM_RESOURCE_HOME", raising=False)
    path = tmp_path / "config.toml"
    monkeypatch.setenv("LPM_CONFIG", str(path))
    path.write_text(
        "\n".join(
            [
                "[github]",
                f'token = "{token}"',
                'owner = ""',
                'repo_prefix = "cursor-skill-"',
                "default_private = false",
                "",
                "[install]",
                'target = "~/.cursor/skills"',
                "",
                "[resources]",
                'repo_name = "LingyeAIResources"',
                'repo_url = ""',
                'local_path = ""',
                'branch = "main"',
                "",
                "[platforms.cursor]",
                "enabled = true",
                'skills_dir = "~/.cursor/skills"',
                'mcp_json = "~/.cursor/mcp.json"',
                'rules_dir = ""',
            ]
        ),
        encoding="utf-8",
    )
    return path


class _MissingRepoClient:
    def __init__(self, token: str):
        self.token = token

    def authenticated_login(self) -> str:
        return "octo"

    def get_repo(self, owner: str, name: str):
        return None


class _ExistingRepoClient(_MissingRepoClient):
    def get_repo(self, owner: str, name: str):
        return object()
