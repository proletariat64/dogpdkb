"""Versioned input model shared by offline comparison stages."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"
ACTIONS = (
    "SKIP_VERIFIED",
    "UPDATE_METADATA_ONLY",
    "UPDATE_TEXT_REUSE_OCR",
    "PROCESS_CHANGED_RESOURCES",
    "PROCESS_REQUIRED",
    "UNKNOWN",
)


@dataclass(frozen=True)
class Document:
    knowledge_base: str
    document_id: str
    content_kind: str
    path: Path
    metadata: dict[str, Any]
    resources: list[dict[str, Any]]
    base_dir: Path
    ocr: dict[str, Any] | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    body_scope: dict[str, Any] = field(default_factory=dict)

    @property
    def identity(self) -> tuple[str, str]:
        return self.knowledge_base, self.document_id

    def resolve(self, reference: str) -> Path:
        path = Path(reference)
        return path if path.is_absolute() else self.base_dir / path


@dataclass(frozen=True)
class CompareOptions:
    normalize_line_endings: bool = False
    include_evidence: bool = False
    max_file_bytes: int = 16 * 1024 * 1024
    max_diff_work: int = 2_000_000
    max_evidence_chars: int = 8000
    max_char_region: int = 4096
