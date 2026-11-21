"""Committed export admissions drive apply plan/Corpus adoption without refetch."""

import json
from pathlib import Path

import pytest

from dogpdkb.records import Run, Store, atomic_json
from dogpdkb.settings import document_identity, load_config, select_book
from tests.test_orchestrator_apply_cli import cli
from tests.test_wash_cleanup_cli import instance

VERSION = "2026-11-12T10:00:00Z"
REMOTES = ("alpha", "beta", "gamma")


def skylark_calls(root):
    log = root / "commands.jsonl"
    if not log.exists():
        return []
    return [
        json.loads(line)
        for line in log.read_text().splitlines()
        if json.loads(line)[0] == "skylark"
    ]


def admission_case(root, count):
    """Admit `count` staged exports committed against a frozen failed run."""
    config, _ = instance(root)
    books = root / "books.json"
    data = json.loads(books.read_bytes())
    data[0]["sources"][0]["document_ids"] = list(REMOTES)
    atomic_json(books, data)
    settings = load_config(config)
    store = Store(settings, select_book(settings, None))
    run = Run.create(store, "sync")
    specs = []
    for i, remote in enumerate(REMOTES):
        doc_id = document_identity("team", remote)
        store.document(doc_id).update(
            source_key="team", source_document_id=remote, observed={"version": VERSION}
        )
        raw = str(run.directory / f"raw/{i}.md")
        request = run.directory / f"export-{i}-request.json"
        report = run.directory / f"export-{i}-report.json"
        atomic_json(
            request,
            {
                "schema": "skylark-export-request-1",
                "documents": [{"document_id": remote, "path": raw, "format": "md"}],
            },
        )
        atomic_json(
            report,
            {
                "schema": "skylark-export-result-1",
                "status": "partial",
                "documents": [{"document_id": remote, "path": raw, "status": "failed"}],
            },
        )
        store.complete(
            doc_id, "export", VERSION, "failed", run.id, path=raw, report=str(report)
        )
        run.failure(
            document_id=doc_id,
            version=VERSION,
            step="export",
            reason="TOOL_FAILED",
            input=raw,
            report=str(report),
        )
        md = root / f"{remote}.md"
        body = f"# {remote}\n\nStaged body.\n".encode()
        md.write_bytes(body)
        staged_request, staged_report = (
            root / f"{remote}.{name}.json" for name in ("request", "report")
        )
        atomic_json(
            staged_request,
            {
                "schema": "skylark-export-request-1",
                "documents": [{"document_id": remote, "path": str(md), "format": "md"}],
            },
        )
        atomic_json(
            staged_report,
            {
                "schema": "skylark-export-result-1",
                "status": "completed",
                "counts": {"total": 1, "completed": 1, "failed": 0},
                "documents": [
                    {
                        "document_id": remote,
                        "path": str(md),
                        "status": "completed",
                        "version": VERSION,
                        "bytes": len(body),
                        "metadata": {"id": remote, "updated_at": VERSION},
                    }
                ],
            },
        )
        specs.append(
            {
                "remote_id": remote,
                "source_key": "team",
                "request": str(staged_request),
                "report": str(staged_report),
                "markdown": str(md),
            }
        )
    differences = run.directory / "differences.json"
    atomic_json(
        differences,
        {
            "schema": "dogpdkb-differences-2",
            "book": {"key": store.book.key},
            "documents": [
                {
                    "document_id": document_identity("team", remote),
                    "source_key": "team",
                    "source_document_id": remote,
                    "version": VERSION,
                    "format": "md",
                }
                for remote in REMOTES
            ],
        },
    )
    run.data["artifacts"]["differences"] = str(differences)
    run.finish("partial", 2)
    staged = root / "staged.json"
    atomic_json(
        staged,
        {"schema": "dogpdkb-staged-export-1", "documents": specs[:count]},
    )
    root_args = ("recover-export", "--config", config)
    prepared = cli(
        *root_args,
        "--run",
        run.id,
        "--input",
        staged,
        "--output-dir",
        root / "prepared",
    )
    assert prepared.returncode == 0, prepared.stderr
    result = json.loads(prepared.stdout)
    published = cli(
        *root_args,
        "--publish",
        result["receipt"],
        "--receipt-sha256",
        result["receipt_sha256"],
    )
    assert published.returncode == 0, published.stderr
    receipt = json.loads(published.stdout)
    return config, receipt


def state_path(root):
    return root / "state" / "funds" / "state.json"


def admitted_rows(root, receipt):
    return json.loads(Path(receipt["admitted"]).read_bytes())["documents"]


@pytest.mark.parametrize("count", [1, 2, 3])
def test_admitted_scope_counts_freeze_plan_without_export(tmp_path, count):
    config, receipt = admission_case(tmp_path, count)
    before = state_path(tmp_path).read_bytes()
    applied = cli(
        "apply",
        "--config",
        config,
        "--input",
        receipt["admitted"],
        "--stop-after",
        "plan",
    )
    assert applied.returncode == 0, applied.stderr
    assert not skylark_calls(tmp_path)
    result = json.loads(applied.stdout)
    assert result["paused"] is True
    run = json.loads(Path(result["run"]).read_bytes())
    plan = json.loads(Path(run["artifacts"]["plan"]).read_bytes())
    assert plan["schema"] == "dogpdkb-plan-1"
    assert len(plan["documents"]) == count
    assert "ocr_policy" in plan  # independent OCR decision stays inside the plan
    expected = {row["document_id"] for row in admitted_rows(tmp_path, receipt)}
    assert {row["document_id"] for row in plan["documents"]} == expected
    state = json.loads(state_path(tmp_path).read_bytes())
    for identifier in expected:
        document = state["documents"][identifier]
        assert "corpus" not in document
        assert document["observed"]["version"] == VERSION
    assert state_path(tmp_path).read_bytes() != before  # compare evidence only
    assert not list((tmp_path / "corpus").glob("*.md"))


def test_admitted_full_run_publishes_corpus_without_export(tmp_path):
    config, receipt = admission_case(tmp_path, 3)
    applied = cli("apply", "--config", config, "--input", receipt["admitted"])
    assert applied.returncode == 0, applied.stderr
    assert not skylark_calls(tmp_path)
    manifest = json.loads(Path(json.loads(applied.stdout)["manifest"]).read_bytes())
    assert manifest["schema"] == "dogpdkb-compile-1"
    assert len(manifest["documents"]) == 3
    for row in manifest["documents"]:
        published = Path(row["path"])
        assert published.parent == tmp_path / "corpus"
        assert "Staged body." in published.read_text()
    state = json.loads(state_path(tmp_path).read_bytes())
    for remote in REMOTES:
        document = state["documents"][document_identity("team", remote)]
        assert document["corpus"]["version"] == VERSION


def tamper_state_version(root, receipt):
    state = json.loads(state_path(root).read_bytes())
    row = admitted_rows(root, receipt)[0]
    state["documents"][row["document_id"]]["raw"]["version"] = "2099-01-01T00:00:00Z"
    atomic_json(state_path(root), state)


def tamper_state_step_version(root, receipt):
    state = json.loads(state_path(root).read_bytes())
    row = admitted_rows(root, receipt)[0]
    state["documents"][row["document_id"]]["steps"]["export"]["version"] = (
        "2099-01-01T00:00:00Z"
    )
    atomic_json(state_path(root), state)


def tamper_state_metadata(root, receipt):
    state = json.loads(state_path(root).read_bytes())
    row = admitted_rows(root, receipt)[0]
    state["documents"][row["document_id"]]["raw"]["metadata"]["name"] = "tampered"
    atomic_json(state_path(root), state)


def replace_raw(root, receipt):
    row = admitted_rows(root, receipt)[0]
    Path(row["path"]).write_bytes(b"# replaced\n")


def delete_raw(root, receipt):
    row = admitted_rows(root, receipt)[0]
    Path(row["path"]).unlink()


def tamper_payload_bytes(root, receipt):
    path = Path(receipt["admitted"])
    path.write_bytes(path.read_bytes() + b"\n")


def tamper_payload_scope(root, receipt):
    path = Path(receipt["admitted"])
    data = json.loads(path.read_bytes())
    data["documents"].append(dict(data["documents"][0]))
    path.write_bytes(json.dumps(data, ensure_ascii=False).encode())


@pytest.mark.parametrize(
    ("tamper", "fragment"),
    [
        (tamper_state_version, "drifted"),
        (tamper_state_step_version, "drifted"),
        (tamper_state_metadata, "drifted"),
        (replace_raw, "drifted"),
        (delete_raw, "admitted"),
        (tamper_payload_bytes, "SHA-256"),
        (tamper_payload_scope, "SHA-256"),
    ],
    ids=[
        "state-version",
        "state-step-version",
        "state-metadata",
        "replaced-raw",
        "missing-raw",
        "payload-sha",
        "payload-scope",
    ],
)
def test_admitted_refuses_drift_before_any_fetch(tmp_path, tamper, fragment):
    config, receipt = admission_case(tmp_path, 2)
    tamper(tmp_path, receipt)
    state_before = state_path(tmp_path).read_bytes()
    applied = cli(
        "apply",
        "--config",
        config,
        "--input",
        receipt["admitted"],
        "--stop-after",
        "plan",
    )
    assert applied.returncode == 1
    assert fragment in applied.stderr
    assert not skylark_calls(tmp_path)
    assert state_path(tmp_path).read_bytes() == state_before
    assert not list((tmp_path / "corpus").glob("*.md"))


def test_admitted_resume_reuses_frozen_handoff(tmp_path):
    config, receipt = admission_case(tmp_path, 2)
    paused = cli(
        "apply",
        "--config",
        config,
        "--input",
        receipt["admitted"],
        "--stop-after",
        "plan",
    )
    assert paused.returncode == 0, paused.stderr
    resumed = cli(
        "apply", "--config", config, "--resume", json.loads(paused.stdout)["run"]
    )
    assert resumed.returncode == 0, resumed.stderr
    assert not skylark_calls(tmp_path)
    manifest = json.loads(Path(json.loads(resumed.stdout)["manifest"]).read_bytes())
    assert len(manifest["documents"]) == 2
