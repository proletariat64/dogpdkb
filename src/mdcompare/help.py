"""Installed, offline help for the independent mdcompare CLI."""

from importlib.resources import files

MANIFEST_EXAMPLE = """{
  "schema_version": "1.0",
  "documents": [{
    "knowledge_base": "example-book", "document_id": "42",
    "content_kind": "raw", "path": "document.md",
    "metadata": {"name": "Limits", "logical_path": "Payments/Limits",
                 "source_ref": "example-book:42"},
    "resources": []
  }]
}"""

ROOT = """Start here:
  1. Capture immutable local Markdown snapshots.
  2. Describe them in JSON manifests (not Markdown command arguments).
  3. Use catalog to discover resources; semantic for authored-body routing;
     knowledge for diagnostics and compare for strict evidence decisions.

Example:
  mdcompare catalog --input descriptors.json --output manifest.json
  mdcompare semantic --baseline old.json --incoming new.json --output report.json --diff-output diff.json

Prerequisites and effects:
  With the exception of explicit semantic-score, no login, network or model is required. Inputs are
  read-only; only explicitly requested manifests/reports are published.
  Command-line paths are relative to cwd. Manifest paths are relative to
  their containing JSON manifest. Reports may replace existing output files.

Output and next steps:
  Successful commands are silent on stdout; diagnostics go to stderr.
  Consume the JSON output file, not stdout or the exit number alone.

Exit codes:
  0  Completed: inspect the report; differences or additions are allowed.
  2  Comparison report has unresolved/review results or item errors; inspect
     reason_codes and source locations before deciding on further work.
  1  Invalid arguments/input or global publication failure; fix the diagnostic.

More help:
  mdcompare COMMAND --help        First-invocation contract and JSON example.
  mdcompare COMMAND --help --full Complete installed schema and edge cases.
  mdcompare --help --full         Tool-wide input and publication contract.
  -h is equivalent to --help. --full requires one of these help flags.
"""

COMPARE = f"""Inputs:
  --baseline and --incoming are JSON manifest files, not Markdown files.
  Pair documents by (knowledge_base, document_id), never by filenames.
  A minimal manifest (create document.md beside each manifest) is:
{MANIFEST_EXAMPLE}

Example:
  mdcompare compare --baseline old.json --incoming new.json --output report.json

Prerequisites and paths:
  Offline; no authentication or model. Supply immutable local snapshots.
  CLI paths resolve from cwd; document/resource/OCR paths from each manifest.
  Create report parent directories first. --report is optional (no Markdown
  report by default); --include-evidence is opt-in (no body snippets by default).

Outputs and write scope:
  --output is the authoritative schema_version 1.0 JSON report. --report adds
  a human Markdown report. Inputs are never changed; existing output reports
  are privately staged and atomically replaced (mode 0600). Output paths must
  not alias one another or any declared input. Stdout is silent; stderr has
  concise diagnostics. No downloading, OCR, graph extraction or baseline update.

Exit codes and next steps:
  0  Resolved report; differences are valid. Inspect action and reason_codes.
  2  Report contains UNKNOWN or item errors; retain known changes, inspect
     text/resources/metadata/ocr dimensions and repair the stated evidence gap.
  1  Invalid arguments/manifest or global read/publication failure; fix stderr
     diagnostics. Do not mistake an old output file for a newly complete report.
  Rerunning reads snapshots again and replaces requested reports; it does not
  fetch missing evidence, run models, process resources or authorize publication.

Full schema: mdcompare compare --help --full
"""

SEMANTIC_SCORE = """Example:
  mdcompare semantic-score --report semantic.json --diff-index diff.json --output scores.json --request-budget 20

Requires installed Pi and Node, and Pi OAuth. Default: openai/gpt-6.1-terra, medium.
Sends changed authored evidence only, with at most two nearby units per side.
Oversized evidence is not clipped or sent. Output is consumed by semantic-route.
Default concurrency: 3. HTTP 429 drains the current tier and lowers the limit.
Keep the journal and lock; use the same bindings and output to resume without replay.
Exit 0: complete; 2: some eligible documents unscored; 1: invalid input or failure.
Use mdcompare semantic-score --help --full for the full contract.
"""

SEMANTIC_ROUTE = """Offline score routing after mdcompare semantic and external scoring.
Requires the exact semantic report, diff index, and mdcompare-semantic-scores-1 JSON.
Score rows bind (knowledge_base, document_id) to evidence path and SHA-256 bytes.
Only CHANGED documents are score-routed; missing scores remain COMPILE. Score >
--compile-threshold compiles (default 3); scores at or below skip unless
--skip-ambiguity-intercept yes and ambiguous, which compiles. No model/network.
Outputs semantic-route-1.0 separately; invalid bindings publish nothing.

Full schema: mdcompare semantic-route --help --full
"""

SEMANTIC = """Offline authored-body routing; no OCR, network or model calls.
Pair by (knowledge_base, document_id). New/changed/uncertain incoming documents COMPILE;
only paired contextual normalized equality SKIPs; unavailable body DEFERs.
Baseline-only identities are advisory and never produce deletion compilation.
Outputs: semantic-1.0 report and complete mdcompare-semantic-diff-1 evidence.
Optional --recovery accepts mdcompare-body-recovery-1 insertion-only repair evidence.

Full schema: mdcompare semantic --help --full
"""

KNOWLEDGE = f"""Inputs:
  --baseline and --incoming are JSON manifest files, not Markdown files.
  Documents are paired by (knowledge_base, document_id). Minimal manifest:
{MANIFEST_EXAMPLE}

Example:
  mdcompare knowledge --baseline old.json --incoming new.json --output body.json

Scope and prerequisites:
  Offline, read-only body knowledge comparison; no login, token or model.
  Images, alt text/image URLs and explicit marked OCR blocks are excluded.
  Resource/OCR declarations are ignored for decisions; ordinary body links
  remain meaningful. No image/OCR work is generated. --report is optional;
  --include-evidence explicitly permits bounded body snippets in reports.

Paths, outputs and effects:
  CLI paths resolve from cwd; input paths from their JSON manifest directory.
  Create report parent directories first. --output writes knowledge-1.0 JSON;
  optional --report writes Markdown. Inputs never change; existing reports are
  atomically replaced, mode 0600. Outputs cannot alias declared inputs.
  Stdout is silent; diagnostics go to stderr. No extraction or baseline advance.

Exit codes and next steps:
  0  Determinate body results; additions are allowed. Inspect status and units.
  2  Complete report with REVIEW, UNKNOWN or item errors. Inspect reason_codes,
     units, old/new source locations and graphify_plan before deciding on work.
  1  Invalid arguments/manifests or global read/publication failure; fix stderr
     diagnostics and do not consume a stale report as a new complete result.
  REVIEW is not an extraction queue. graphify_plan is advice only; preserve
  uncertainty and existing knowledge. Repeating the command re-reads inputs
  and replaces reports; it does not resolve semantic uncertainty or run a model.

Full schema: mdcompare knowledge --help --full
"""

CATALOG = f"""Input:
  --input is a JSON descriptor manifest, not a Markdown document. Each path
  identifies local Markdown. Minimal input (resources may be omitted):
{MANIFEST_EXAMPLE}

Example:
  mdcompare catalog --input descriptors.json --output manifest.json

Prerequisites and paths:
  Offline; no token, login or model. CLI paths resolve from cwd. Document and
  declared evidence paths resolve from the input manifest directory; Markdown
  local image/link targets resolve from the Markdown file's parent.

Output and write scope:
  --output writes schema_version 1.0 JSON with absolute document/evidence paths,
  resources and resource_warnings. Each discovered resource has resource_id,
  url, kind and path when local bytes exist. Remote resources are never fetched.
  Missing/unsupported syntax stays visible in resource_warnings, not a claim
  of completeness. Stdout is silent; diagnostics go to stderr.
  Input Markdown/manifests/evidence are read-only. Output parents are created;
  existing output is atomically replaced (mode 0600). Output must not alias an
  input manifest, document or declared/discovered local resource/evidence file.

Exit codes and next steps:
  0  Catalog written; inspect resource_warnings before comparing dependencies.
  1  Invalid arguments/manifest, unreadable input or publication failure; fix
     stderr diagnostics. An old output file is not a newly successful catalog.
  Repeating catalog refreshes this inventory and replaces the selected output.
  It does not download resources, OCR, compare knowledge or advance a baseline.

Full schema: mdcompare catalog --help --full
"""


DOCTOR = """Examples:
  mdcompare doctor
  mdcompare doctor --mode llm --model openai/gpt-6.1-terra --json

Default mode checks the local comparison runtime (no Node or login). LLM mode
checks the installed Pi SDK, model/effort, proxy setup, and stored OAuth. An
expired credential is a warning, not a check of refresh capability.
Local only: no requests, token refresh, credential output or report files.
Exit 0: no failing check; 2: environment check failed; 1: invalid invocation.
"""

NORMAL = {
    "mdcompare": ROOT,
    "doctor": DOCTOR,
    "compare": COMPARE,
    "knowledge": KNOWLEDGE,
    "semantic": SEMANTIC,
    "semantic-route": SEMANTIC_ROUTE,
    "semantic-score": SEMANTIC_SCORE,
    "catalog": CATALOG,
}


def full_contract(command: str) -> str:
    """Read only bundled documentation, never a checkout or remote resource."""
    if command == "doctor":
        return "\n--json emits mdcompare-doctor-1 with checks and network_checked: false.\n"
    names = ["manifest.txt", "input-safety.txt"]
    if command == "compare":
        names += ["compare.txt", "resources.txt", "ocr.txt", "history.txt"]
    elif command == "knowledge":
        names += ["knowledge.txt"]
    elif command == "semantic":
        names += ["semantic.txt"]
    elif command == "semantic-score":
        names += ["semantic-score.txt"]
    elif command == "semantic-route":
        names += ["semantic-route.txt"]
    elif command == "catalog":
        names += ["catalog.txt", "resources.txt"]
    return "\nComplete local contract\n=======================\n\n" + "\n\n".join(
        files("mdcompare").joinpath("help", name).read_text(encoding="utf-8")
        for name in names
    )
