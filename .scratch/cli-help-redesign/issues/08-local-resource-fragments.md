# fix: Preserve fragments in relocated local destinations

Type: task
Status: resolved

## Scope

Review Issue 1 is valid: catalog resource IDs retain URL fragments while local
paths identify only files. Relocation currently drops the fragment, breaking SVG
symbol references. Preserve fragments in retained OCR output, fallback publication
and absolute tool-facing OCR input. Keep canonical raw bytes and URL path escaping.

## Acceptance

Public CLI regression with real SVG files, multiple fragments, escaped filenames,
relative/absolute destinations and output/fallback paths. Published destinations
must still select the same symbols after explicit wash deletion.

## Answer

Confirmed through four failing public CLI cases before the fix. Destination
formatting now encodes the new filesystem path separately from the original
fragment. Retention also recognizes relative destinations returned from normalized
OCR inputs. All four cases pass for retained OCR output/fallback and
relative/absolute input, with two distinct fragments on an SVG filename containing
space and hash characters. Canonical raw bytes remain unchanged and the referenced
SVG survives explicit purge. Nearest affected suite: 113 passed; focused Ruff and
format checks passed. Full check/smoke remain PR CI gates per user instruction.
