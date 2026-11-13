# fix: Preserve published assets and frozen witnesses during wash retention

Type: task
Status: resolved
Blocked by: none

## Scope

Resolve four concrete findings from the default-branch OCR review, because this
integration branch's wash lifecycle will be delivered in the same new PR:

1. Retaining replacement OCR files must not delete assets referenced by the last
   published corpus before the replacement is fully retained and published.
2. Corpus witnesses referenced by older manifests must remain immutable per
   publication, including interruption between corpus replacement and state save.
3. After an earlier export fails, OCR failure cleanup must retain the actual
   request path rather than recomputing an index from the old selection slot.
4. Retained local destinations in catalogued HTML attributes and reference-style
   Markdown definitions must survive automatic cleanup and explicit wash purge.

## Acceptance

Reproduce each defect through public CLI subprocesses and real local Markdown,
using existing independent-tool fixtures. Fault injection may observe/deny an
external filesystem operation; do not mock private orchestrator calls. Old
published images/witnesses remain intact on failure, failure evidence remains
readable, and local HTML/reference resources resolve after wash deletion. Keep
raw source bytes as canonical evidence and alter only tool-facing/published
copies where relocation is needed. No new content fingerprints, migration,
retry mechanism, private tool imports or production operations.

Spec: [CLI help delivery](../spec.md), preserving the existing wash lifecycle.
Original lifecycle spec: [wash cleanup](../../sync-wash-cleanup/spec.md).
OCR evidence: /tmp/dogpdkb-help-ocr.txt.

## Comments

The help redesign does not authorize new synchronization policy. These are
focused preservation fixes for existing behavior in the same integration PR.

## Answer

Implemented in ade8b95 and 1cea13a, integrated into the existing branch.
All four initial defects were reproduced through public CLI calls before fixes.
Additional literal-region, quoted-alt and srcset descriptor regressions also
failed before correction. The nearest 99 CLI regressions pass; canonical raw
bytes and published literal examples remain unchanged. Retained publication
generations are immutable; no migration, fingerprint or automatic retry added.
Final full-gate and artifact proof are owned by tickets 05/06.
