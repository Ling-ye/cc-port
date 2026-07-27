"""PyInstaller entry script for the desktop API sidecar binary.

This is intentionally tiny: it just defers to ``cc_port.interfaces.desktop_api.main`` so the
generated executable matches the behaviour of the ``cc-port-desktop-api`` console
script installed by ``pip install -e .``.
"""

from __future__ import annotations

import sys

from cc_port.interfaces.desktop_api import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
