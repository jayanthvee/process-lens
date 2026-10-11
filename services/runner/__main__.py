"""Entry point: ``python -m services.runner --worker-id <id>``."""

from __future__ import annotations

import sys

from .worker import main

if __name__ == "__main__":
    sys.exit(main())
