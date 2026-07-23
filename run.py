#!/usr/bin/env python3
"""Entry point: `python run.py <command>` (see `python run.py --help`)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from idintel.cli import main

if __name__ == "__main__":
    sys.exit(main())
