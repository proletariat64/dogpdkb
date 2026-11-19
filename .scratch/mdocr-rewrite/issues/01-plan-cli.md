# Public mdocr plan and CLI
Status: ready-for-agent

Implement the immutable new public planner and CLI shape in the map: file, JSON list, directory, deterministic input/output, source/image/prompt identity, exact Markdown occurrences, no inference. Park the run CLI dispatch on the agreed lazy import until ticket 03. Cover plan-only and help via subprocess TDD. Own CLI, plan, parser/help and packaged default prompt data; own migrating old help tests, not runner or consumer tests. Do not touch Pi bridge. Keep public bindings specified in the map.
