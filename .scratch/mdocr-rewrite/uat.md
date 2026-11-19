# mdocr rewrite UAT checklist

Status: planned (no run evidence yet)

Test an installed wheel in an isolated workspace; nothing in the workstation production corpus or current CLI install is an output destination.

| Gate | Observable acceptance |
| --- | --- |
| Startup and packaging | Help is offline; Paddle works without Node/Pi; LLM is explicit about missing or unsupported Pi, Node, model capability or OAuth; package contains prompt and bridge files. |
| Plan | File, JSON array and recursive directory produce deterministic, collision-free output maps. No OCR or output publication before run. Source, image, options or prompt drift cannot masquerade as the original plan. |
| Paddle installed UAT | Use only a local fake jobs server. Check original image link resolution, exact authored bytes outside the OCR block, no additional retry/call after interruption, and the 3 → 2 → 1 fallback with in-flight work accounted for. |
| LLM installed UAT | Fake runtime exercises strict schema, malformed/missing/misordered/truncated replies, two kinds of budget and 429 path. Real OAuth probes use only synthetic fixtures, one request per explicitly selected route and reasoning effort medium. No fallback. Skip/declare any missing credential rather than substituting a route. |
| Resume safety | In a fresh process, an unknown admitted request cannot be sent again. A replay of a completed plan verifies the retained output/evidence and does not call a provider. No held or unverifiable document overwrites an existing good output. |
| Cross-tool | Invoke `dogpdkb`, `mdocr`, and `mdcompare` through their public CLI/artifacts; inspect new mode-evidence and verify that OCR-derived text does not become authored text. |
| Repository | Required tests, Ruff, docs, wheel and sdist, installed help/read-only smoke, `git diff --check`. Inspect Greptile only for the exact current PR head; reverify after fixes. |

Live routes planned: `openai/gpt-6.1-terra` and `openai-codex/gpt-6.1-terra`; a successful Codex-OAuth inference verifies the route, but proves Device Code enrollment only if that enrollment was observed. No authentic corpora or credentials in screenshots, fixtures or PR evidence.
