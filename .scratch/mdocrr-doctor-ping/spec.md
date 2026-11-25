# mdocrr doctor PING/PONG

Status: draft

## Command and scope

Use `mdocrr doctor [--config FILE] [--pretty]` with the existing configuration
lookup. Bare `doctor` uses compiled defaults. Stdout contains one compact JSON
object by default. `--pretty` indents the same JSON object. Both forms use the
same checks, network behavior, and exit status. Diagnostics go to stderr.

- **LLM** uses the same provider route, model, effort, proxy, CA and OAuth
  credential as OCR. After local checks pass and a usable credential is
  available, send one text-only request with input `PING` and ask for a short
  reply. Any completed assistant text reply with non-whitespace content passes.
  `PONG` is an example. Apply a 20-second total deadline through
  response completion, a bounded response size and a small output cap where the
  route supports one. Disable retries, redirects and model fallback.
- **Paddle** keeps the existing local preflight. Report the live probe as skipped
  with `paddle_ping_unsupported`. The configured endpoint submits image OCR jobs,
  so doctor sends no HTTP request and reports remote token acceptance and model
  behavior as unverified.

An omitted LLM `oauth_file` skips the probe with `credential_not_configured`.
A missing or invalid configured credential file, or an expired token, fails
the local checks. An explicit `request_budget = 0` skips the LLM probe with
`request_budget_zero`. A missing or positive budget permits one diagnostic
request. Include local credential findings even when the probe is skipped.
The probe uses the saved access token without refreshing or rewriting it.
It sends no Markdown, images or OCR prompt and creates no workspace, cache
entry or journal record. It consumes no batch budget, although the provider
may meter the request.

## Report and exit status

Extend the existing JSON summary and named local checks. Include the effective
provider route, model and effort for LLM, `network_checked` indicating whether
the request was attempted, and separate probe results with stable reason codes:

| Signal | Result |
| --- | --- |
| HTTP endpoint | Response received with HTTP status, transport failure, deadline exceeded, or skipped. A non-2xx response still confirms that HTTP answered. |
| Credentials | Local state is reported separately from remote acceptance. Report remote acceptance after a successful authenticated response, rejection on clear authentication errors, and unverified when transport, throttling or ambiguous errors prevent an inference. Paddle reports local token usability. |
| Model | Pass when a completed reply contains nonempty assistant text. Report empty or incomplete responses, explicit model rejection, permissions, throttling and generic HTTP failures separately. Leave model availability unverified if the response establishes no model outcome. |

Include the relevant configuration key and a short next step for failures and
skips. Emit no token, account identifier, raw response or private endpoint or
proxy URL.

- Exit `0` for a completed text reply or an intentional skip with otherwise
  passing local checks.
- Exit `1` for an unreadable or invalid configuration.
- Exit `2` for a failed local prerequisite or an attempted probe that fails.

## Acceptance

Use a loopback fixture for each LLM route. Verify the provider route, model,
effort and fixed PING, one request on every attempted probe, and no follow-up
request on redirects or timeouts. Verify that both `PONG` and a different
completed text reply pass. Cover empty or incomplete responses, HTTP 401,
explicit model rejection, HTTP 429, and timeout. Verify zero requests for bare
doctor, zero budget, expired or invalid credentials, and Paddle. Check exit
status, separate local and remote results, redaction, and lack of credential
or workspace writes. Verify that compact and pretty output parse to the same
JSON object and produce the same exit status for success, failure, and skip.
