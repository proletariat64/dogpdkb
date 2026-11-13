# CLI help implementation review

Status: final artifact acceptance and review in progress

## Standards

Review boundary: actual default `origin/master` through integration HEAD, focusing
on the new help delta since `e8a88c4`; independent Skylark help delta since `0e7c218`.
No hard documented-standard violations. One optional observation: dogpdkb command
metadata spans three parallel dictionaries. Existing complete scope coverage
makes omissions visible; no cross-tool help framework is introduced to address
this nonblocking maintainability judgement.

## Spec

Initial review found missing compile-manifest fields/run lock/subscription fields,
an incorrect mdocr resource-count denominator, and incomplete managed-install
proof. Resolved by help corrections in 83f0dd5 and observed managed-install proof:
100 public calls passed with no lock acquisition. No scope creep found.

## Installed trace

The first 100-call managed-install trace proved successful exits, no operational
file mutations, no tool subprocesses and no managed lifecycle-lock acquisition.
It also found eager mdocr network-library imports opening/binding a local IPv6
socket in 17 calls. This was a real contract defect. Lazy mdocr operational imports fixed it;
the replacement wheel passed 100/100 under the same held lock with zero network,
write, flock or unexpected business-exec syscalls. All 73 installed runtime/help
files directly matched that replacement wheel. Final rebuilt-artifact proof
will follow the pending wash-data-preservation changes.

## OCR

Initial installed OCR completed 21 selected files with 11 findings. Help
modifier placement, abbreviation bypasses, target/re-OCR guidance, caller cache
selection and installed audit coverage were corrected. Focused help re-review
completed six files and requested stronger detection of existing-file mutation;
the guard now blocks write-intent opens, filesystem mutations and locks, with an
external truncation regression proving prior bytes remain intact. Four wash
findings from the same PR branch are being reproduced and repaired separately: mutable
publication witnesses, destructive OCR asset retention, wrong failed OCR request
index, and missing HTML/reference destination relocation. Original/focused OCR
outputs are retained under /tmp/dogpdkb-help-ocr*.txt; do not confuse intermediate
session markers with completed coverage.

## Current verification

Clean detached integration checkout: doc guard passed 131 documents; Ruff check
and formatting passed; 933 tests passed. Plain fresh-wheel smoke passed 87 Python
help/version calls plus plan/resume/cleanup/no-op/purge using real mdcompare and
image-less mdocr; Skylark/Graphify business processes were explicit fixtures.
These results precede review fixes and final artifact acceptance.

## Preservation follow-up

The four initial wash findings were fixed in ade8b95, with 92 nearest CLI tests
including 11 new regressions. Independent review then reproduced literal-code
corruption; focused OCR additionally found quoted-alt and srcset-descriptor
corruption in the same destination replacement. Commit 1cea13a protects explicit
literal regions, consumes complete attributes before selecting destinations,
and rewrites only the first URL token per srcset candidate. All 99 nearest CLI
regressions pass, including seven new follow-up cases. Source integrated at
9f9cc35; final targeted OCR and final built-wheel acceptance are pending.

A full clean-checkout gate at 1a738af passed 952 tests, doc guard 131 documents,
and Ruff/formatting. Final source adds the seven literal/attribute cases; final
count will be recorded from execution. The complete resource-kind enum has been
corrected to image/attachment/media in local full help and the tool contract.
