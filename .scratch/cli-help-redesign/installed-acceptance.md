# Fresh-install help and side-effect acceptance

Status: final artifact passed; original import-side-effect defect corrected and reverified
Date: 2026-11-13

## Artifacts and environment

- Python wheel: `/tmp/dogpdkb-help-dist/dogpdkb-0.2.0-py3-none-any.whl`.
- Fresh Python 3.12.3 environment:
  `/tmp/dogpdkb-help-proof/versions/0.2.0.abcdef`.
- Managed prefix: `/tmp/dogpdkb-help-proof`, with an absolute current symlink,
  a valid four-line .dogpdkb-managed ownership marker, and actual installed
  dogpdkb/mdcompare/mdocr entry points exposed through prefix/bin links.
- Python installation used offline uv cache only, installing the local wheel
  and six dependencies. All 73 installed tool runtime/help files were compared
  directly against the wheel and matched; no source import override was used.
- Independent Skylark entry point:
  `/tmp/skylark-help-install/node_modules/.bin/skylark`, installed from
  `/tmp/skylark-help-redesign/artifacts/pi-skylark-0.1.0.tgz`.
- Final passing trace evidence and JSON results:
  `/tmp/dogpdkb-help-proof-evidence-final`.
- Final reproduction harness: `/tmp/dogpdkb-help-proof-run-final.py`.
- Earlier passing import-fix proof and harness remain preserved:
  `/tmp/dogpdkb-help-proof-evidence-fixed` and
  `/tmp/dogpdkb-help-proof-run-fixed.py`.
- Preserved pre-fix evidence and harness:
  `/tmp/dogpdkb-help-proof-evidence` and `/tmp/dogpdkb-help-proof-run.py`.
- Final Python artifact source commit:
  `3c37b047d822fa74074882c3f1058c126b7d8341`. The rebuilt final wheel was
  explicitly reinstalled into the same task-owned environment; all 73 installed
  runtime/help files directly matched that exact final wheel after the trace run.
  Final artifact summary is retained with the traces as final-artifact-summary.json.

## Coverage

The matrix covered 24 current root/command scopes: dogpdkb 13, mdcompare 4,
mdocr 4, and Skylark 3. Every scope ran -h, --help, -h --full, and --help --full,
plus one root --version request per tool: 100 subprocess invocations in total.

All invocations used unrelated cwd, isolated HOME/XDG/TMP paths, absent OCR
credentials, a nonexistent configuration path, unavailable gateway/proxy values,
and a PATH containing only system interpreter directories. Python bytecode
writes were disabled explicitly with PYTHONDONTWRITEBYTECODE=1. No operational
CLI substitute was installed.

A real exclusive managed lifecycle lock was held throughout all 100 requests;
a competing nonblocking lock still failed at the end. The managed prefix,
HOME, cwd, and temporary cache trees were compared before and after. The
ownership marker and lock content were compared directly.

Every invocation ran under /usr/bin/strace with descendant/thread tracing,
file-operation, network, exec/process, write, and flock syscall capture. Expected
console pipe writes, Node runtime pipes/eventfds and its env-to-node interpreter
exec chain were classified separately from business subprocesses and disk writes.

## Results

| Tool | Invocations | Exit 0 with output | Strict side-effect acceptance |
| --- | ---: | ---: | ---: |
| dogpdkb | 53 | 53 | 53 |
| mdcompare | 17 | 17 | 17 |
| mdocr | 17 | 17 | 17 |
| Skylark | 13 | 13 | 13 |
| Total | 100 | 100 | 100 |

All 100 requests produced nonempty stdout, empty stderr, and exit 0. All tested
filesystem metadata, HOME/cwd/cache trees, ownership marker, and lock contents
remained unchanged. No request attempted flock or launched a business tool.

The final-artifact matrix passed strict side-effect acceptance for all 100 calls:
zero network syscalls, zero operational write syscalls, zero flock syscalls, and
zero unexpected business subprocesses. Expected interpreter exec chains and
Node runtime threads/IPC were observed separately; they were not counted as
business activity. Across 230 trace files, the only 113 exec calls were the
expected executable/interpreter chains. The exclusive lifecycle lock remained
held and all recorded filesystem snapshots remained unchanged. The complete final
passing rerun took 52.12 seconds under tracing; no runtime timeout occurred.

Before the correction, all 100 calls also exited 0, but all 17 mdocr help/version
requests opened an IPv6 socket and bound ::1 on an ephemeral port. The original
strict score was 83/100. No connect/send call or remote request was observed.
Eager requests/OCR/Paddle imports before help dispatch loaded urllib3, whose
HAS_IPV6 initialization performed that probe. An independent import of requests
reproduced the same socket/bind pair. This failure was reported and preserved
rather than reclassified as harmless. The replacement wheel defers operational
imports and the complete fresh-installed matrix now records zero such syscalls.

## Follow-up and limits

The mdocr correction was made by the implementation task, not this acceptance
task. This acceptance explicitly reinstalled the rebuilt fixed wheel and reran
all 100 calls in the isolated managed-shaped environment, preserving other user
installs. The final artifact was reinstalled and reverified after all implementation fixes.
The disposable environment and harness remain available if another artifact is
built, but this proof describes the completed final artifact above. No source or Git/PR change was made by
this acceptance task.

This proof covers help/version and observed side effects for these exact local
artifacts. It does not establish live authentication, remote availability,
export completeness, paid model behavior, production synchronization, release
publication, or deployed-host acceptance. No such operation was run. Interpreter
cache creation is excluded by the explicit Python environment setting, not by
an assertion that Python never writes bytecode in an arbitrary environment.

## Final runtime CI handoff

The strict 100-call trace above is scoped to its recorded source 3c37b04.
Subsequent runtime corrections are in the destination-relocation helper and
were verified by 102 focused CLI cases and seven independent peer cases.
Final runtime payload: 0cddefd44f070f686d5ef4f4ce5ba814faa94858.

Per the user's explicit instruction, post-OCR full check/smoke runs only in PR CI.
Both final-source runs at code-equivalent head 07d44bf passed:

- https://github.com/proletariat64/dogpdkb/actions/runs/43368250538
- https://github.com/proletariat64/dogpdkb/actions/runs/43368302338

The latter job records successful quality-gate and isolated platform installation
steps. The latest local full check was interrupted (130); the latest direct
smoke launch was rejected before starting. Earlier local installed proofs are
not relabeled as final-source reruns. No production source or paid model tool
operation was performed.
