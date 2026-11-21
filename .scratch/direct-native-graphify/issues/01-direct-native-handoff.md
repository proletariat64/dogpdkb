# Replace graph wrapper and remove one-off OCR recovery

Status: resolved

Implement the single serial handoff in [spec](../spec.md). Verify through the existing CLI and the agreed private UAT. Do not add alternate dispatch paths or change production.

## Acceptance

Private UAT: 10/10 live exports matched the source fixtures; the native graph run indexed 10/10 finalized bodies in fixed directories, with no OCR or Graphify dispatch on the unchanged rerun. Focused regressions cover partial output, retry ownership, unselected exposure, and competing instances sharing a target. No production changes.
