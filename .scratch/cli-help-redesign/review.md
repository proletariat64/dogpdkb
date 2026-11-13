# CLI help implementation review

Status: resolved

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

## Final literal-boundary review

The next exact-range OCR reviewed one source file and found three bounded lexer
issues: rejected fenced openers hiding same-line triple-backtick code spans,
backticks inside reference labels splitting definition context, and code-span
pairing crossing blank paragraph boundaries. These are recorded in
/tmp/dogpdkb-help-ocr-relocation-final.txt and assigned to the same preservation
implementer for public CLI regressions and focused correction. Earlier final
959-test and 100-call artifact results apply to source 3c37b04 and are not
substituted for acceptance of the corrected source.

## Final known-finding closure and verification boundary

Commit 3774f54 fixes the final three lexer findings. The nearest 102 public CLI
regressions pass, including all 21 publication-preservation cases. Independent
peer inspection and seven focused public cases close the exact three findings.
The last provider report still contains three findings; their closure is test
and peer evidence, not a claim of a final provider-zero report. Runtime source
is integrated at 0cddefd.

The user explicitly directed that, after OCR remediation, local full
`make check` and `make smoke` must not be repeated; PR CI owns those gates.
The already-running full check was interrupted with exit 130. The latest direct
smoke launch was rejected by automatic approval review before starting because
it was equivalent to the newly prohibited local full smoke gate. No workaround
or retry was attempted. Earlier 959-test/full-smoke/100-call results remain
identified with their earlier source snapshots; full final-source acceptance
will be recorded from PR CI. All known implementation findings are fixed.

## Delivery result

All known findings are closed by focused regression and independent peer
verification. Final runtime payload: 0cddefd. The last three provider findings
were fixed in 3774f54 and verified by 102 nearest CLI cases plus seven peer
cases; no final provider-zero result is claimed.

PR #20 is open from the existing feat/sync-wash-cleanup branch. At code-equivalent
head 07d44bf both CI runs passed: push 43368250538 and PR 43368302338. The PR job
explicitly reports successful Run quality gate and Build and verify isolated
platform installation steps. This is final-source CI proof, distinct from the
prior locally recorded 959-test/full-smoke/100-call snapshots. No local full
gate was rerun after the user's instruction.

All task-owned linked implementation/verification worktrees are cleaned; original
checkouts, dirty user files, source snapshot and useful package/proof artifacts
are preserved. The independent Skylark push remains rejected pending explicit
repository-destination authorization; primary PR delivery is complete.
