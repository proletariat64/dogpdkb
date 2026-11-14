"""Scoped local documentation for the public argparse command surfaces."""

import argparse
import sys
import textwrap
from pathlib import Path

SUMMARIES = {
    "": "Synchronize selected Yuque knowledge through independent public tools.",
    "discover": "Validate a complete source listing and select recent changes; no body export.",
    "apply": "Export, compare, plan and OCR selected changes, then publish usable corpus Markdown.",
    "update-graph": "Compile published complete sources with the original Graphify CLI.",
    "sync": "Run serial discovery, export, comparison, planning, OCR, corpus and graph publication.",
    "status": "Observe a run without write locks or mutation; optionally stream snapshots.",
    "purge": "Explicitly delete selected book wash runs, preserving durable data and tool caches.",
    "doctor": "Check the package or configured tool entry points offline; no remote acceptance.",
    "init-config": "Create disabled portable example configuration without overwriting files.",
    "init-state": "Register an existing corpus baseline without rewriting Markdown or graph.",
    "publish-web": "Build local Web catalog/map snapshots independently of synchronization.",
    "update": "Reinstall a specified AntCode Release in an active managed Linux installation.",
    "uninstall": "Remove the active managed program and owned links; retain configuration and data.",
}

ARGUMENTS = {
    "config": "TOML instance file: explicit option > DOGPDKB_CONFIG > source default.toml or installed cwd/dogpdkb.toml",
    "book": "enabled source key in books.json (not its display name); optional only when exactly one source matches",
    "listing": "offline skylark-listing-1 JSON file; omitted: call configured Skylark listing; CLI paths use cwd",
    "replan": "with --resume RUN --retry-graph, recompute selection from retained original comparison snapshots; preserve published corpus/OCR and earlier evidence",
    "resume": "saved run ID, run directory or run.json path; reuse frozen inputs; cleaned sync runs support only explicit --retry-graph recovery",
    "retry_graph": "explicitly retry this saved run's failed/interrupted graph batch after fixing its cause; validates frozen inputs and target locks; sync requires --resume; default: suppress retries",
    "stop_after": "pause after all downloads/comparisons and the fixed plan, before OCR/corpus/graph writes; default: continue",
    "run": "run ID, directory or run.json path for the selected book; status defaults to latest active, otherwise latest run",
    "json": "emit machine-readable JSON; status --watch emits one JSON object per line (JSONL)",
    "watch": "repeat status every 1 second until interrupted; default: one snapshot",
    "all": "select all run-* wash directories for this book; mutually exclusive with --run",
    "force": "allow discarding paused/failed/interrupted runs; never bypass active process or write locks",
    "package": "check only the installed package, without reading instance configuration or probing tool entry points",
    "yes": "confirm uninstall without an interactive terminal; default: ask for confirmation",
}

EXAMPLES = {
    "discover": "dogpdkb discover --config instance/dogpdkb.toml --book funds",
    "apply": "dogpdkb apply --config instance/dogpdkb.toml --book funds --input differences.json --stop-after plan\n  dogpdkb apply --config instance/dogpdkb.toml --book funds --resume RUN",
    "update-graph": "dogpdkb update-graph --config instance/dogpdkb.toml --book funds --input manifest.json",
    "sync": "dogpdkb sync --config instance/dogpdkb.toml --book funds --stop-after plan\n  dogpdkb sync --config instance/dogpdkb.toml --book funds --resume RUN",
    "status": "dogpdkb status --config instance/dogpdkb.toml --book funds --watch --json",
    "purge": "dogpdkb purge --config instance/dogpdkb.toml --book funds --run RUN\n  dogpdkb purge --config instance/dogpdkb.toml --book funds --all --force",
    "doctor": "dogpdkb doctor --package --json\n  dogpdkb doctor --config instance/dogpdkb.toml --book funds --json",
    "init-config": "dogpdkb init-config --output instance",
    "init-state": "dogpdkb init-state --config instance/dogpdkb.toml --book funds",
    "publish-web": "dogpdkb publish-web --config instance/dogpdkb.toml --book funds",
    "update": "dogpdkb update --version 0.2.0  # must be an already published Release",
    "uninstall": "dogpdkb uninstall --yes",
}

DETAILS = {
    "discover": "Inputs: complete Skylark listing JSON, not Markdown. Writes a new wash run, listing, differences.json and durable observations, never corpus/graph. Missing baseline: use init-state first. stdout: JSON pointer with run/differences/counts; stderr: diagnostics. Exit codes: 0 selected/no changes; 1 failure; 64 usage; 130/143 interruption. Repeating creates a new listing observation; absent IDs never delete local data.",
    "apply": "Inputs: dogpdkb-differences-1 JSON from discover, or --resume (exclusive). Uses configured Skylark/mdcompare/mdocr and their existing authentication; may download and call OCR. Writes run reports, persistent inputs, state and corpus; emits dogpdkb-compile-1 manifest, not a graph update. stdout: exit_code/paused/manifest/run JSON; stderr: live tool diagnostics. Exit codes: 0 complete/plan pause; 2 usable set with failures; 1 batch failure; 64 usage; 130/143 interruption. Inspect reports before recovery: same-version failed/unknown steps remain suppressed. Resume validates saved inputs; it does not repair missing frozen inputs.",
    "sync": "Inputs: configured enabled book, or saved run via --resume. Uses Skylark SSO, mdocr token for new OCR and configured Graphify provider. Writes wash, durable state/inputs, OCR cache, corpus and graph; Web publication is separate. stdout: exit_code/paused/manifest/run JSON; stderr: live progress/diagnostics. Exit codes: 0 complete/no work/plan pause; 2 usable set completed with failures; 1 batch failure; 64 usage; 130/143 interruption. Completed/partial sync cleans successful wash copies and retains failure evidence. After fixing a graph failure, --resume RUN --retry-graph revalidates its retained final compile manifest and repeats only Graphify, including after cleanup. Add --replan to recompute body selection from retained original snapshots without repeating export/OCR or rewriting corpus; missing original snapshots stop recovery. Other cleaned resumes are refused. Ordinary repeats never retry failed/unknown same-version steps; historical failures remain recorded.",
    "update-graph": "Inputs: dogpdkb-compile-1 JSON from apply with complete corpus paths and frozen witnesses. Requires configured original Graphify CLI/provider and an existing graph's native manifest.json. Writes isolated graph workspace, execution report, durable graph facts and successful graph/manifest publication. stdout: run_id/exit_code/status/report JSON; stderr: tool progress. Exit codes: 0 complete/no work; 2 recorded extraction failures or blocked work (possibly no graph published); 1 graph/batch failure; 64 usage; 130/143 interruption. Failed compile retains the public graph and does not roll back corpus. Inspect report; do not blindly repeat a suppressed failed/unknown version.",
    "status": "Inputs: selected instance/book; optional run reference. Default: newest active run, otherwise newest recorded run. stdout: readable snapshot, JSON with --json, or JSONL with --watch --json; stderr: errors. No files, corpus, graph, caches or state are written; no tools/authentication are used. Exit codes: 0 snapshot/no-runs; 1 unreadable configuration/run; 64 usage; 130 interrupted watch. Repeating only observes; a stale recorded running run is shown as interrupted when its owner is no longer active.",
    "purge": "Inputs: exactly one of --run and --all; no remote authentication. Deletes only selected book wash directories, including failed Markdown/images/reports and compact run records. Preserves corpus, graph, durable state, persistent comparison inputs and tool caches. Active runs/locks and unsafe paths remain protected even with --force. stdout: book/purged-paths JSON; stderr: errors. Exit codes: 0 deletion/no targets; 1 refusal/I/O failure; 64 usage. Repeating --run after removal fails because the run no longer exists; --all can return an empty list. Purge never resets suppression, retries work or backfills history.",
    "doctor": "Inputs: optional instance/book, or --package without instance settings. Default preflight calls configured tools' --help, but does not authenticate, synchronize or prove remote availability. stdout: readable checks or a JSON check array; stderr: diagnostics. No corpus/graph/state publication; repeated checks may repeat tool probes. Exit codes: 0 all checks pass; 1 any failed check; 64 usage; 130/143 interrupted probe. Fix the failed check before business execution.",
    "init-config": "Inputs: output directory (default: cwd). Writes dogpdkb.toml and books.json examples with the source disabled. Existing files or symlinks cause refusal without overwrite. No credentials, tools or network required. stdout: creation message; stderr: error. Exit codes: 0 created; 1 I/O/collision; 64 usage. Edit the gateway/tool settings, source URL and enabled flag; repeat with a new empty directory rather than overwriting a configured instance.",
    "init-state": "Inputs: configured book and existing title-YYYY-MM-DD-[ID].md corpus. Registers day-precision baseline facts in state/<book>/state.json under write locks; does not rewrite Markdown/graph, import old runs, extract knowledge or invent graph success. Existing records remain; duplicate IDs fail before registration. stdout: initialization JSON counts; stderr: error. Exit codes: 0 registered; 1 invalid baseline/configuration/I/O; 64 usage. Safe repeat preserves registered facts; inspect duplicate IDs before retrying.",
    "publish-web": "Inputs: corpus directory and Graphify graph.json, defaulting to configured book. Requires TOML configuration even with explicit paths; no remote authentication. Writes local catalog/map snapshots and current Web outputs; does not upload/deploy, synchronize or compile. stdout: status/documents/nodes/edges/communities/catalog/map JSON; stderr: error. Exit codes: 0 published locally; 2 parser/configuration/path/JSON-read errors; 1 rendering/schema/publication failure. Repeating builds a new snapshot and replaces current pointers; retain snapshots according to your operational policy.",
    "update": "Inputs: published MAJOR.MINOR.PATCH target, required. Active installer-managed Linux environment only; custom pip/uv/source installs are refused. Downloads the installer from fixed AntCode HTTPS Releases (not GitHub); network and release access required. Uses lifecycle locks, replaces the environment and owned links, retains configuration/external data; no downgrade or rollback archive. stdout/stderr: installer messages/diagnostics. Exit codes: 0 installer success; 1 validation/I/O/refusal (installer failures propagate); 64 CLI usage. Stop jobs first; retrying the same version reinstalls the complete environment.",
    "uninstall": "Inputs: active installer-managed Linux environment; --yes required without a terminal. No remote authentication/network required. Removes only the managed environment and matching owned dogpdkb/mdocr links; retains configuration, all external data and the lifecycle lock inode. stdout: removal/retention details; stderr: errors. Exit codes: 0 removed; 1 refusal/cancellation/I/O; 64 usage. Stop jobs first. Repeating after removal cannot use the removed command; do not delete unrelated environments manually.",
}

CONFIGURATION = """Configuration:
  --config > DOGPDKB_CONFIG > checkout default.toml / installed cwd/dogpdkb.toml.
  TOML paths use its directory; book corpus paths use books.json. CLI file paths
  use cwd. --book is an enabled key; omit only with exactly one matching source.
  Tool authentication stays in existing tool environments; help needs none.
"""


class HelpParser(argparse.ArgumentParser):
    """Add a full local manual to the existing parser, without business setup."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)
        self.scope = self.prog.removeprefix("dogpdkb").strip()
        self.description = SUMMARIES.get(self.scope, self.description)
        self.add_argument(
            "--full",
            action="store_true",
            help="with -h/--help, include the complete offline contract",
        )
        self.full_help = False

    def parse_known_args(self, args=None, namespace=None):
        arguments = list(sys.argv[1:] if args is None else args)
        self.full_help = "--full" in arguments
        if self.full_help and not self.scope:
            command = next((arg for arg in arguments if arg in EXAMPLES), None)
            if command and arguments.index("--full") < arguments.index(command):
                self.error(
                    f"place --full after the command: dogpdkb {command} --help --full"
                )
        if self.full_help and not any(flag in arguments for flag in ("-h", "--help")):
            self.error("--full requires -h or --help")
        for action in self._actions:
            if isinstance(action, argparse._SubParsersAction):
                for choice in action._choices_actions:
                    choice.help = SUMMARIES.get(choice.dest, choice.help)
            elif action.dest in ARGUMENTS:
                action.help = ARGUMENTS[action.dest]
            elif action.dest == "input":
                action.help = {
                    "apply": "dogpdkb-differences-1 JSON from discover; exclusive with --resume",
                    "update-graph": "required dogpdkb-compile-1 JSON emitted by apply; complete corpus sources, not fragments",
                    "publish-web": "corpus Markdown directory; default: selected book corpus",
                }.get(self.scope, action.help)
            elif action.dest == "output":
                action.help = (
                    "example directory (default: cwd)"
                    if self.scope == "init-config"
                    else "Web output directory; default: paths.web/<book key>"
                )
            elif self.scope == "publish-web":
                action.help = {
                    "graph": "graph.json file; default: configured graph_dir or corpus/graphify-out/graph.json",
                    "book_name": "display name override; default: selected book name or explicit book URL",
                    "book_url": "source URL override; default: selected book URL",
                }.get(action.dest, action.help)
        return super().parse_known_args(arguments, namespace)

    def format_help(self):
        if self.scope:
            self.epilog = (
                "Example:\n  "
                + EXAMPLES[self.scope]
                + "\n\n"
                + textwrap.fill(DETAILS[self.scope], width=88)
            )
            if self.scope not in {"init-config", "update", "uninstall"}:
                self.epilog += "\n\n" + CONFIGURATION
        else:
            self.epilog = (
                """Start here:
  dogpdkb init-config --output instance
  Edit instance/dogpdkb.toml and books.json; enable your chosen source.
  dogpdkb doctor --config instance/dogpdkb.toml --book KEY
  dogpdkb init-state --config instance/dogpdkb.toml --book KEY
  dogpdkb sync --config instance/dogpdkb.toml --book KEY --stop-after plan

Choose discover for selection, apply for corpus, update-graph for graph, or sync
for the serial chain. status observes; purge deletes wash; publish-web is separate.
stdout carries results, stderr diagnostics. Business exits are command-specific;
read the selected command's Exit codes before deciding how to recover.
"""
                + CONFIGURATION
            )
        self.epilog += f"\nLocal contract: {self.prog} --help --full (or -h --full)."
        if self.full_help:
            root = Path(__file__).with_name("resources") / "help"
            self.epilog += "\n\n" + (root / "configuration.txt").read_text(
                encoding="utf-8"
            )
            manual = root / f"{self.scope or 'root'}.txt"
            self.epilog += "\n\n" + manual.read_text(encoding="utf-8")
        return super().format_help()

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\nTry '{self.prog} --help'.\n")
