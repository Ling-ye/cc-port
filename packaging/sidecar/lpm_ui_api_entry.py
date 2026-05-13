"""PyInstaller entry script for the desktop sidecar binary.

This is intentionally tiny: it just defers to ``lpm.ui_api.main`` so the
generated executable matches the behaviour of the ``lpm-ui-api`` console
script installed by ``pip install -e .``.
"""

from __future__ import annotations

import sys

from lpm.ui_api import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
