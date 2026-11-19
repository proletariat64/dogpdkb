# Discover and freeze selected sources

Status: resolved
Blocked by: 01

Implement combined discovery with per-source observations, existing Yuque listing and selection policy, recursive directory scans and frozen byte witnesses. Full selected-scan failure exposes no executable plan. Check scan selection, skipped paths, directory snapshots and failures at the CLI.

## Answer

Discovery validates the selected scans before saving observations; directory
versions and frozen inputs bind selected Markdown bytes. CLI tests cover source
selection, exclusion rules, multi-Yuque identity and all-selected failure.
