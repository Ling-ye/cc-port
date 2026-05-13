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

