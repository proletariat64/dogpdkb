# mdcomparer doctor PING/PONG

Status: resolved

## Behavior

`mdcomparer doctor [--config FILE] [--pretty]` answers whether the configured provider, OAuth credential and model can complete a minimal live request. Keep the existing CLI and TOML lookup. Bare doctor uses compiled defaults. With no credential or local test endpoint, it reports why the probe was skipped.

After local validation passes and a usable OAuth credential or supported loopback endpoint is available, send one request through the same provider route, model and reasoning effort as semantic scoring. Prompt with `PING` and ask for a short reply. Any completed, nonempty assistant text reply passes, including `PONG`. Use a small output cap and a 20-second total deadline. The probe reads no Markdown and uses no scoring work plan. It admits no semantic scoring request and writes no score journal.

Keep scoring budgets separate. A missing scoring budget permits the diagnostic ping. An explicit zero budget skips it and reports `request_budget_zero`. Doctor reads saved credentials without refreshing or rewriting them. An expired token is reported locally. The probe never retries.

## Report

Keep the single final JSON object and stderr progress. `--pretty` changes JSON formatting only. Include the selected provider/model and distinguish local checks from live results:

| Signal | Interpretation |
| --- | --- |
| Provider | HTTP response received, transport failure, deadline exceeded, or skipped. Include the HTTP status when available. |
| OAuth | Local credential missing, expired or usable. Separately report remote acceptance, rejection, or unverified. Valid local credentials alone do not prove remote acceptance. |
| Model | Passed when a completed response contains nonempty assistant text. Report explicit provider model errors separately from permission, throttling, generic request errors and invalid or incomplete responses. Ambiguous failures leave model availability unverified. |

Reuse the existing summary and named checks. Include stable reason codes and actionable hints. Emit no credentials, raw replies, private endpoint URLs or proxy secrets.

Exit 0 for successful configured checks and a completed text reply, or a clean local-only report with an intentional skip. Exit 1 for invalid configuration. Exit 2 for failing local prerequisites or a failed attempted probe. An expired or rejected credential makes the diagnosis fail.

## Acceptance

Use a loopback provider to verify exactly one PING request carrying the configured model and effort. Verify that both `PONG` and a different completed text reply pass. Cover empty or incomplete responses, HTTP 401, a model rejection, HTTP 429 and timeout. Bare doctor and explicit zero budget must send zero requests. Validate that compact and pretty outputs parse to the same diagnostic structure.
