#!/usr/bin/env python3
"""Backward-compatible entry point for dogpdkb.workflows.graph_sync."""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
_module = importlib.import_module("dogpdkb.workflows.graph_sync")

if __name__ == "__main__":
    pass
else:
    sys.modules[__name__] = _module
