pub(crate) const ROOT: &str = "Quick start
  mdcomparer draft before.md after.md --plan work.json
  mdcomparer run --plan work.json

Use mdcomparer COMMAND --help for an example and exit codes.
Use mdcomparer help all for the full offline contract.";

pub(crate) const DRAFT: &str = "Example
  mdcomparer draft before.md after.md --plan work.json

Writes a semantic work plan with one local paired identity. Choose a new .json
path. The snapshots stay read-only and the plan is editable until acceptance.

Exit codes
  0 created, 1 invalid input or output collision, 64 usage error.";

pub(crate) const ACCEPT: &str = "Example
  mdcomparer accept --plan work.json

Configuration comes from --config, default.toml next to the plan, or built-in
defaults. Acceptance records input hashes, counts, and effective comparison
settings. The plan and its workspace stay bound after acceptance.

Exit codes
  0 accepted, 1 invalid input/configuration or workspace failure, 64 usage error.";

pub(crate) const RUN: &str = "Example
  mdcomparer run --plan work.json > summary.json 2>> progress.log

Accepts a new plan and runs the operation specified in it. Existing plans use
frozen settings. A completed plan returns its verified result. For a new plan,
configuration comes from --config, adjacent default.toml or built-in defaults.

Exit codes
  0 complete, 1 invocation or artifact failure, 2 partial report, 64 usage error.";

pub(crate) const SCORE: &str = "Example
  mdcomparer score --plan score.json --config scoring.toml

A score plan selects scoped identities and binds one committed comparison.
Configure an explicit request budget. Zero is a local run with no requests.
Resume uses the frozen configuration and never repeats a recorded admission.

Exit codes
  0 complete, 1 invocation or artifact failure, 2 partial, 3 cancelled,
  64 usage error.";

pub(crate) const ROUTE: &str = "Example
  mdcomparer route --plan work.json --scores score.mdcompare/result.json

Uses the current route policy and writes a separately bound report offline.
The default threshold is 3, with ambiguity interception disabled.

Exit codes
  0 published, 1 invalid input/configuration or binding failure, 64 usage error.";

pub(crate) const STATUS: &str = "Example
  mdcomparer status --plan work.json

Reports an existing comparison or score workspace. Status is read-only.

Exit codes
  0 state read successfully, 1 missing or invalid workspace, 64 usage error.";

pub(crate) const CANCEL: &str = "Example
  mdcomparer cancel --plan score.json

Records cancellation for an accepted score plan. Existing requests can settle.
Use score --resume with the same plan to continue unused admissions.

Exit codes
  0 intent recorded or task already terminal, 1 missing or invalid workspace,
  64 usage error.";

pub(crate) const DOCTOR: &str = "Example
  mdcomparer doctor --config scoring.toml --pretty

Checks the local runtime and explicitly configured scoring prerequisites.
Without --config it checks built-in defaults and requires no credentials.
Reports the semantic scoring model, budget, transport and saved OAuth state.
Runs offline. Use --pretty for indented JSON.

Exit codes
  0 capabilities available, 1 invalid configuration, 2 capability unavailable,
  64 usage error.";

pub(crate) const HELP: &str = "\
Example
  mdcomparer help all

Displays the embedded work plan, configuration, output and recovery contract.

Exit codes
  0 displayed, 1 output failure, 64 usage error.";

pub(crate) const ALL: &str = include_str!("contract.txt");
