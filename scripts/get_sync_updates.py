#!/usr/bin/env python3
"""Backward-compatible entry point for dogpdkb.workflows.get_sync_updates."""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
_module = importlib.import_module("dogpdkb.workflows.get_sync_updates")

if __name__ == "__main__":
    _module.main()
else:
    sys.modules[__name__] = _module
