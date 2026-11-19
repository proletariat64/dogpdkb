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

## First installed real-route probe

- Built wheel and sdist offline and installed the wheel in a disposable Python 3.12 virtualenv. Planned only a generated PNG with two labelled boxes and an arrow, and synthetic Markdown. No corpus content.
- The two allowed plans each admitted one medium-effort Terra job, one plan per Pi OAuth route. Each CLI returned incomplete, with one durable `unknown_outcome` and no usage or published Markdown. The shell harness itself exited zero; do not report either route as accepted.
- Diagnosis without inference: native Node fetch timed out on unauthenticated HEAD checks to both service origins; the configured HTTPS proxy reached them (OpenAI 401 / Codex 405). The bridge now honors the configured HTTPS proxy for inference and token refresh, without a direct fallback. Local mocked transport tests verify the route and preserve the rate-limit status.
- Leave both unknown plans unchanged. Further live probes require fresh bounded authorization, use new isolated plans and run the rebuilt wheel; never retry the two old journals. Device Code enrollment itself has not been observed in this UAT.
