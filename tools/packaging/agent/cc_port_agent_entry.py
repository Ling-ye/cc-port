"""PyInstaller entry point for the public CC Port CLI and MCP executable."""

from cc_port.interfaces.cli import app

if __name__ == "__main__":
    app()
