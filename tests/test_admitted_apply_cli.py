"""Committed export admissions drive apply plan/Corpus adoption without refetch."""

import hashlib
import json
import sys
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


def admission_case(root, count, image_path=None):
    """Admit `count` staged exports committed against a frozen failed run.

    With `image_path`, the second admitted body embeds that local image via an
    absolute reference so the bytes survive copying to durable evidence.
    """
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
        text = f"# {remote}\n\nStaged body.\n"
        if image_path is not None and remote == REMOTES[1]:
            text += f"\n![chart]({image_path})\n"
        body = text.encode()
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
    # The apply pipeline must not rewrite the byte-exact admission record:
    # the committed recovery run still verifies and finalizes afterwards.
    resumed = cli("recover-export", "--config", config, "--resume", receipt["run"])
    assert resumed.returncode == 0, resumed.stderr
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


def test_admitted_local_image_compiles_with_ocr_decision_and_no_refetch(tmp_path):
    from PIL import Image

    from tests.ocr_fixture import write_ocr_fixture

    png = tmp_path / "images" / "chart.png"
    png.parent.mkdir()
    Image.new("RGB", (8, 8), (12, 34, 56)).save(png)
    config, receipt = admission_case(tmp_path, 2, image_path=png)
    tool = tmp_path / "ocr_fixture.py"
    write_ocr_fixture(
        tool,
        "import json,sys\n"
        "fixture_result({'schema_version': 'mdocr-result-2', "
        "'documents': [{'status': 'completed'} for _ in fixture_plan['documents']]})\n",
    )
    config.write_text(
        config.read_text().replace(
            json.dumps([sys.executable, str(tmp_path / "remote.py"), "mdocr"]),
            json.dumps([sys.executable, str(tool)]),
        )
    )
    admitted = {row["document_id"] for row in admitted_rows(tmp_path, receipt)}
    rows_before = {
        key: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        for key, value in json.loads(state_path(tmp_path).read_bytes())[
            "documents"
        ].items()
    }
    applied = cli("apply", "--config", config, "--input", receipt["admitted"])
    assert applied.returncode == 0, applied.stderr
    assert not skylark_calls(tmp_path)
    run = json.loads(Path(json.loads(applied.stdout)["run"]).read_bytes())
    plan = json.loads(Path(run["artifacts"]["plan"]).read_bytes())
    assert {row["document_id"] for row in plan["documents"]} == admitted
    assert all(row["action"] == "compile" for row in plan["documents"])
    manifest = json.loads(Path(json.loads(applied.stdout)["manifest"]).read_bytes())
    assert len(manifest["documents"]) == 2
    bodies = {}
    for row in manifest["documents"]:
        published = Path(row["path"])
        assert published.parent == tmp_path / "corpus"
        assert row["ocr_status"] == "completed"
        bodies[row["document_id"]] = published.read_text()
    (image_doc,) = [
        key for key, body in bodies.items() if "mdocr:ocr-begin" in body
    ]
    assert image_doc in admitted
    (text_doc,) = [key for key in admitted if key != image_doc]
    assert bodies[text_doc] == "# alpha\n\nStaged body.\n"
    state = json.loads(state_path(tmp_path).read_bytes())
    changed = {
        key
        for key, value in state["documents"].items()
        if hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        != rows_before.get(key)
    }
    assert changed == admitted


def refuse_case(tmp_path, tamper, fragment, count=2):
    config, receipt = admission_case(tmp_path, count)
    tamper(tmp_path, receipt)
    state_before = state_path(tmp_path).read_bytes()
    corpus_before = {path.name for path in (tmp_path / "corpus").glob("*.md")}
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
    assert {path.name for path in (tmp_path / "corpus").glob("*.md")} == corpus_before


def tamper_excluded_subscription(root, receipt):
    books = root / "books.json"
    data = json.loads(books.read_bytes())
    staged = json.loads((root / "staged.json").read_bytes())["documents"]
    excluded = {row["remote_id"] for row in staged[:1]}
    data[0]["sources"][0]["document_ids"] = [
        remote for remote in REMOTES if remote not in excluded
    ]
    atomic_json(books, data)


def tamper_untracked_destination(root, receipt):
    row = admitted_rows(root, receipt)[0]
    destination = root / "corpus" / f"source-[{row['document_id']}].md"
    destination.parent.mkdir(exist_ok=True)
    destination.write_bytes(b"stray\n")


def tamper_absence(root, receipt):
    state = json.loads(state_path(root).read_bytes())
    row = admitted_rows(root, receipt)[0]
    state["documents"][row["document_id"]]["absence"] = {"run_id": "later-scan"}
    atomic_json(state_path(root), state)


def tamper_observed_version(root, receipt):
    state = json.loads(state_path(root).read_bytes())
    row = admitted_rows(root, receipt)[0]
    state["documents"][row["document_id"]]["observed"]["version"] = (
        "2099-01-01T00:00:00Z"
    )
    atomic_json(state_path(root), state)


def tamper_staged_report(root, receipt):
    row = admitted_rows(root, receipt)[0]
    doc_dir = Path(row["path"]).parent
    staged = doc_dir / "staged-report.json"
    staged.write_bytes(staged.read_bytes() + b"\n")


def test_admitted_refuses_subscription_exclusion_before_fetch(tmp_path):
    refuse_case(tmp_path, tamper_excluded_subscription, "subscription")


def test_admitted_refuses_untracked_destination_before_fetch(tmp_path):
    refuse_case(tmp_path, tamper_untracked_destination, "corpus destination conflict")


def test_admitted_refuses_recorded_absence_before_fetch(tmp_path):
    refuse_case(tmp_path, tamper_absence, "absence")


def test_admitted_refuses_unobserved_frozen_version_before_fetch(tmp_path):
    refuse_case(tmp_path, tamper_observed_version, "frozen version no longer observed")


def test_admitted_refuses_changed_durable_evidence_before_fetch(tmp_path):
    refuse_case(tmp_path, tamper_staged_report, "durable evidence changed")


def test_admitted_plan_pause_resume_after_interruption_reuses_witnesses(tmp_path):
    config, receipt = admission_case(tmp_path, 2)
    failed = cli(
        "apply",
        "--config",
        config,
        "--input",
        receipt["admitted"],
        "--stop-after",
        "plan",
    )
    assert failed.returncode == 0, failed.stderr
    record_path = Path(json.loads(failed.stdout)["run"])
    record = json.loads(record_path.read_bytes())
    plan_path = Path(record["artifacts"]["plan"])
    plan_path.unlink()
    del record["artifacts"]["plan"]
    record.update(status="running", exit_code=None, ended_at=None)
    record_path.write_text(json.dumps(record))
    resumed = cli("apply", "--config", config, "--resume", record["run_id"])
    assert resumed.returncode == 0, resumed.stderr
    plan = json.loads(plan_path.read_bytes())
    assert plan["schema"] == "dogpdkb-plan-1"
    assert len(plan["documents"]) == 2
    assert not skylark_calls(tmp_path)


def test_admitted_resume_refuses_superseded_raw_fact(tmp_path):
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
    run_id = json.loads(paused.stdout)["run"]
    state = json.loads(state_path(tmp_path).read_bytes())
    row = admitted_rows(tmp_path, receipt)[0]
    state["documents"][row["document_id"]]["raw"] = {
        **state["documents"][row["document_id"]]["raw"],
        "version": "2099-01-01T00:00:00Z",
    }
    atomic_json(state_path(tmp_path), state)
    resumed = cli("apply", "--config", config, "--resume", run_id)
    assert resumed.returncode == 1
    assert "drifted" in resumed.stderr
    assert not skylark_calls(tmp_path)
    assert not list((tmp_path / "corpus").glob("*.md"))


def test_admitted_resume_refuses_membership_change_after_pause(tmp_path):
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
    books = tmp_path / "books.json"
    data = json.loads(books.read_bytes())
    staged = json.loads((tmp_path / "staged.json").read_bytes())["documents"]
    excluded = {row["remote_id"] for row in staged[:1]}
    data[0]["sources"][0]["document_ids"] = [
        remote for remote in REMOTES if remote not in excluded
    ]
    atomic_json(books, data)
    resumed = cli(
        "apply", "--config", config, "--resume", json.loads(paused.stdout)["run"]
    )
    assert resumed.returncode == 1
    assert "subscription" in resumed.stderr
    assert not skylark_calls(tmp_path)


def test_admitted_resume_refuses_replaced_handoff_after_pause(tmp_path):
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
    run = json.loads(Path(json.loads(paused.stdout)["run"]).read_bytes())
    frozen = Path(run["artifacts"]["differences"])
    frozen.write_bytes(frozen.read_bytes() + b"\n")
    resumed = cli(
        "apply", "--config", config, "--resume", json.loads(paused.stdout)["run"]
    )
    assert resumed.returncode == 1
    assert "changed after planning" in resumed.stderr
    assert not skylark_calls(tmp_path)


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
