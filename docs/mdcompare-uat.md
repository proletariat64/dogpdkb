# mdcompare real-file UAT — 2026-11-07

The independent CLI was run against the fixed 98 historical-enriched / incoming
raw pairs supplied from co.bot. It generated exactly 98 results on each of two
runs, with byte-identical JSON and human reports. Both runs exited 2 and reported
98 UNKNOWN, preserving the missing provenance/resource evidence instead of
inventing verified skips. No production sync, OCR, Graphify, baseline advancement
or input mutation was performed.

## Inputs and adaptation

The source package is co.bot's retained mdcompare UAT package dated 2026-11-07.
A verified copy is retained under the integration worktree's ignored, private
`tmp/mdcompare-uat-20261107/source/`. It contains 307 files / 28,077,256 bytes,
including 98 old/new/diff triples and the 4,757-row capture-time path index.
The guide was read before use. The earlier transfer comparison matched every file;
the local retained copy was also compared directly byte-for-byte.

The final manifests were adapted from the fixed inventory, not the live path
index: scoped document IDs pair each fixed old.md/new.md. Baseline declares
`historical_enriched`; incoming declares `raw`. There is no old raw provenance or
complete old/new image evidence, so none was fabricated. Resource declarations
are empty because no captured resource bytes were supplied; discovery still finds
unverified dependencies. Names came from the captured inventory, logical paths
were left blank, and source references use stable document IDs. These metadata
values are adapter inputs, not evidence that production metadata stayed equal.

Files are private/read-only (0400; source directories 0500). Adaptation manifests
and reports are separate under the integration worktree's ignored, private
`tmp/mdcompare-uat-20261107/results/`. They are excluded from Git and review-provider
context. The remote package and production directories were not changed.

## Repeatable real-file run

With the private adapted manifests in place, the same command was run twice with
separate output names and default budgets:

```bash
mdcompare compare --baseline baseline.json --incoming incoming.json \
  --output run-1.json --report run-1.md --include-evidence
mdcompare compare --baseline baseline.json --incoming incoming.json \
  --output run-2.json --report run-2.md --include-evidence
```

Observed on the integrated comparator containing the prepared-JPEG validation fix:

| Check | Observed result |
| --- | --- |
| Results / unique scoped identities | 98 / 98, sorted and complete on both runs |
| Action counts | UNKNOWN 98; every other action 0; sum 98 |
| Exit codes | 2, 2; stdout/stderr empty |
| Historical scope | HISTORICAL_ENRICHED_TO_RAW and provenance_verified=false for all 98 |
| Missing raw reason | HISTORICAL_RAW_UNAVAILABLE for all 98 |
| Resource and OCR dimensions | 92 UNKNOWN, 6 NOT_APPLICABLE each; no verified OCR reuse |
| Bounded text evidence | 87 documents; every document's displayed body evidence at most 8,000 characters |
| Diff budget limit | 11 DIFF_BUDGET_EXCEEDED at default work budget 2,000,000; never treated as equal |
| Determinism | Both JSON files and both human reports byte-identical |
| Input preservation | All 307 files compared directly before/after the runs, unchanged |
| Report permissions | All four final reports 0600 |

The private verification.json records these counts and checks. No report body is
copied into this tracked record. Limits were retained instead of increasing a
budget to manufacture a better overall result.

## Five guide cases

All five recommended IDs produced UNKNOWN with explicit asymmetric scope and
bounded observed differences:

| Document ID | Real evidence check |
| --- | --- |
| 559193484 | Default display clipping was explicit. A separate bounded 128,000-character display run showed the old 100k and new 300k values; no clipping, same UNKNOWN action and unchanged diff work budget. |
| 558886610 | The old APD and new AP2 values remained visible in default bounded differences; UNKNOWN was preserved. |
| 587484756 | Observed text differences retained; conservative dependency discovery leaves resources/OCR unverified, and missing historical raw still prevents a skip. |
| 576210508 | Observed mixed historical/raw differences retained with unverified resources/OCR. |
| 518275144 | Unmarked/unclear historical transcription did not become a verified deletion or equality conclusion. |

The amount spotlight used a one-document adaptation with the same source files:

```bash
mdcompare compare --baseline amount-baseline.json --incoming amount-incoming.json \
  --output amount-evidence.json --include-evidence --max-evidence-chars 128000
```

This is observed file-difference evidence, not a new business-semantic gold label.
The prior 27/45/26 manual classifications were not used to assign CLI actions.

## Proof layers and remaining limits

Real-file UAT establishes safe handling of this selected legacy batch, complete
results, bounded explanations, stable repeated decisions and unchanged inputs.
It cannot prove normal successful-old-raw to new-raw skips, paired image equality,
OCR reuse, full-corpus filtering rates, business correctness or production release.
No raw baseline was promoted merely because an export exists.

Development uses separate offline fixtures for the six actions, tiny financial
changes, Markdown semantics, resource replacement, OCR compatibility, provenance,
input drift, malformed inputs and publication failures. Those fixtures are not
passed off as real UAT. Installation smoke uses its own disposable fixture only
to prove that a built wheel contains the CLI and can run independently outside the
source checkout; it is separate from the real-file acceptance above.

Final repository checks and the independent standards/specification review are
recorded in ticket 07's Answer. Ticket 07 is resolved after the integration owner completed the review gate and final checks. No remote CI or publication is implied by local
checks.

## Final post-review verification

The final runtime integration is `0ca16a88a46df11a2c5137223fcb54a9e4190cc1`. After review fixes, both real-file runs, the amount spotlight, the full 722-test suite, type/lint/document checks and rebuilt isolated installation smoke passed again. Resource/OCR applicability changed from the pre-review 89 UNKNOWN / 9 NOT_APPLICABLE to 92 UNKNOWN / 6 NOT_APPLICABLE; all 98 document actions remained UNKNOWN. The earlier reports and artifacts are retained privately under `results/pre-review/`. Final verification and rebuilt artifacts use the existing private results paths. The [review record](mdcompare-review.md) separates original findings from post-fix validation.
