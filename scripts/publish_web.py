#!/usr/bin/env python3
"""Backward-compatible entry point for dogpdkb.workflows.publish_web."""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
_module = importlib.import_module("dogpdkb.workflows.publish_web")

if __name__ == "__main__":
    _module.main()
else:
    sys.modules[__name__] = _module
