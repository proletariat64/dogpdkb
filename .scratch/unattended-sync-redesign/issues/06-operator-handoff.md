# operator-handoff

Status: integrated

Blocked by: none

Status/watch, scoped durable cleanup-agent handoff and retry eligibility, truth and digests, exit semantics, progressive help and docs; CLI acceptance.

Acceptance: applicable public CLI scenarios in [spec](../spec.md).

- `status` and `--watch` show checkpoint progress, independent publication
  outcomes, existing evidence and current observed/raw/corpus/graph facts.
  Equal versions can still have different recorded corpus and graph SHA-256.
- Terminal sync records retain a structured cleanup handoff through automated
  wash cleanup. Unknown sends are not replayed; matching unsent OCR evidence is
  revalidated for later catch-up. Graph-only recovery cannot change the saved
  scan mode, manifest or frozen input tree. Manual purge still removes the run.
- Release-venv public CLI verification: 186 focused tests passed with explicit
  worktree `PYTHONPATH` and pytest basetemp under a separate `mktemp` parent.
  The decomposed Unicode filename regression went red then green; 56 graph,
  graph-input-mode and handoff tests passed on the final code. Ruff and doc
  guard passed. No full suite, production tool/provider calls or push.
- Older records may lack hashes, active tool counters or a graph retry binding.
  No migration or general force-retry command; the observer does not infer
  missing counters or attest transport or live witness integrity.
