"""Public staged-export admission does not run the export or publication pipeline."""

import fcntl
import hashlib
import json
from pathlib import Path

from dogpdkb.records import Run, Store, atomic_json
from dogpdkb.settings import document_identity, load_config, select_book
from tests.test_orchestrator_apply_cli import cli
from tests.test_wash_cleanup_cli import instance

VERSION = "2026-11-12T10:00:00Z"


def export_case(root):
    config, _ = instance(root)
    books = root / "books.json"
    data = json.loads(books.read_bytes())
    data[0]["sources"][0]["document_ids"] = ["alpha", "beta"]
    atomic_json(books, data)
    settings = load_config(config)
    store = Store(settings, select_book(settings, None))
    run = Run.create(store, "sync")
    selected, specs = [], []
    for i, remote in enumerate(("alpha", "beta", "outside")):
        doc_id = document_identity("team", remote)
        row = {
            "document_id": doc_id,
            "source_key": "team",
            "source_document_id": remote,
            "version": VERSION,
            "format": "md",
        }
        selected.append(row)
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
            "documents": selected,
        },
    )
    run.data["artifacts"]["differences"] = str(differences)
    run.finish("partial", 2)
    return config, store, run, specs


def test_generic_raw_only_set_rejects_bad_scope_version_and_hash(tmp_path):
    config, store, original, specs = export_case(tmp_path)
    initial = store.path.read_bytes()
    witness = {path: path.read_bytes() for path in original.directory.glob("*.json")}
    staged = tmp_path / "staged.json"
    root = ("recover-export", "--config", config)

    def prep(set_, dest):
        atomic_json(staged, {"schema": "dogpdkb-staged-export-1", "documents": set_})
        return cli(
            *root,
            "--run",
            original.id,
            "--input",
            staged,
            "--output-dir",
            tmp_path / dest,
        )

    excluded = prep(specs, "not-allowed")
    assert (
        excluded.returncode == 1
        and "outside the frozen source subscription" in excluded.stderr
    )
    assert not (tmp_path / "not-allowed").exists()
    report = Path(specs[1]["report"])
    correct = report.read_bytes()
    bad_version = json.loads(correct)
    bad_version["documents"][0]["version"] = "2099-01-01T00:00:00Z"
    atomic_json(report, bad_version)
    assert prep(specs[:2], "wrong-version").returncode == 1
    report.write_bytes(correct)
    ready = prep(specs[:2], "prepared")
    assert ready.returncode == 0, ready.stderr
    result = json.loads(ready.stdout)
    command = (
        *root,
        "--publish",
        result["receipt"],
        "--receipt-sha256",
        result["receipt_sha256"],
    )
    report.write_bytes(correct + b"\n")
    drift = cli(*command)
    assert drift.returncode == 1 and "changed after prepare" in drift.stderr
    report.write_bytes(correct)
    lock = store.settings.graph_for(store.book) / ".dogpdkb.lock"
    lock.parent.mkdir(exist_ok=True, parents=True)
    with lock.open("a+b") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        blocked = cli(*command)
    assert blocked.returncode == 1 and "another writer" in blocked.stderr
    assert store.path.read_bytes() == initial
    done = cli(*command)
    assert done.returncode == 0, done.stderr
    receipt = json.loads(done.stdout)
    assert receipt["documents"] == 2
    admitted = Path(receipt["admitted"])
    assert (
        hashlib.sha256(admitted.read_bytes()).hexdigest() == receipt["admitted_sha256"]
    )
    before, after = [
        json.loads(data)["documents"] for data in (initial, store.path.read_bytes())
    ]
    ids = {document_identity("team", item) for item in ("alpha", "beta")}
    assert {key for key in before if before[key] != after[key]} == ids
    for key in ids:
        assert "corpus" not in after[key] and "graph" not in after[key]
        assert before[key]["observed"] == after[key]["observed"]
        assert after[key]["steps"]["export"]["status"] == "completed"
    assert all(path.read_bytes() == data for path, data in witness.items())
    assert not (tmp_path / "commands.jsonl").exists()

    # Crash after state commit: resume finalizes only the run, not state.
    record_path = Path(receipt["run"])
    record = json.loads(record_path.read_bytes())
    record.update(status="running", exit_code=None, ended_at=None)
    atomic_json(record_path, record)
    committed = store.path.read_bytes()
    resumed = cli(*root, "--resume", record["run_id"])
    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(resumed.stdout) == receipt
    assert json.loads(record_path.read_bytes())["status"] == "completed"
    assert store.path.read_bytes() == committed
