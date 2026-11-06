"""mdocr — prepare and OCR images referenced by Markdown."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dogpdkb")
except PackageNotFoundError:
    __version__ = "uninstalled"
