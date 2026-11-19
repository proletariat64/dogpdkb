"""Unattended authored-text routing through public CLI/artifact boundaries."""

import json
import sys
from pathlib import Path

import pytest

from tests.source_fixture import team_id
from tests.test_orchestrator_apply_cli import (
    VERSION,
    cli,
    instance,
    seed_body_baseline,
    two_sources,
)


def load(path):
    return json.loads(Path(path).read_text())


def pause(config, differences):
    result = cli(
        "apply", "--config", config, "--input", differences, "--stop-after", "plan"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    run = load(loads := json.loads(result.stdout)["run"])
    return loads, run, load(run["artifacts"]["plan"])


def wrap(config, tmp_path, code):
    script = tmp_path / "comparison-wrapper.py"
    script.write_text(code)
    config.write_text(
        config.read_text().replace(
            json.dumps([sys.executable, "-m", "mdcompare"]),
            json.dumps([sys.executable, str(script)]),
        )
    )


def test_missing_known_baseline_defers_without_affecting_new_document(tmp_path):
    config, differences = instance(tmp_path)
    two_sources(tmp_path, differences)
    seed_body_baseline(tmp_path, config, "# Title\n\nPrevious rule.\n")
    next((tmp_path / "corpus").glob("*.md")).unlink()
    _, run, plan = pause(config, differences)
    assert [(row["document_id"], row["action"]) for row in plan["documents"]] == [
        (team_id("42"), "defer"),
        (team_id("43"), "compile"),
    ]
    report = load(run["artifacts"]["semantic"])
    assert report["results"][0]["semantic_status"] == "UNAVAILABLE"
    assert report["results"][1]["semantic_status"] == "NEW"


def test_usable_uncertainty_compiles_and_complete_diff_survives_wash_purge(tmp_path):
    config, differences = instance(tmp_path)
    before, after = "旧业务规则。" * 3000, "新业务规则。" * 3000
    seed_body_baseline(tmp_path, config, "# Title\n\n" + before)
    remote = load(tmp_path / "remote.json")
    remote["42"]["body"] = "# Title\n\n" + after
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    wrap(
        config,
        tmp_path,
        "import subprocess,sys\na=sys.argv[1:]\nif a[0]=='semantic':a+=['--max-diff-work','0']\nraise SystemExit(subprocess.call([sys.executable,'-m','mdcompare',*a]))\n",
    )
    run_path, run, plan = pause(config, differences)
    assert plan["documents"][0]["action"] == "compile"
    assert plan["documents"][0]["semantic_status"] == "UNCERTAIN"
    state = load(tmp_path / "state/funds/state.json")
    assert state["documents"][team_id("42")]["steps"]["compare"]["status"] == "completed"
    index_path = Path(run["artifacts"]["semantic_diff"])
    index = load(index_path)
    detail = index_path.parent / index["documents"][0]["evidence"]
    payload = load(detail)
    assert any(unit["text"] == before for unit in payload["old_units"])
    assert any(unit["text"] == after for unit in payload["new_units"])
    assert payload["source_inputs"]["baseline"]["version"] == {
        "value": "2026-11-11",
        "precision": "day",
    }
    assert payload["source_inputs"]["incoming"]["version"] == VERSION
    purged = cli("purge", "--config", config, "--run", run_path, "--force")
    assert purged.returncode == 0, purged.stderr
    assert index_path.is_file() and detail.is_file()
    assert load(detail) == payload


def prepared_recovery(tmp_path):
    config, differences = instance(tmp_path)
    original = "# Title\n\n规则\n\n> **图片转写：**\n> OCR噪声\n业务正文\n"
    seed_body_baseline(tmp_path, config, original)
    witness = tmp_path / "original.md"
    witness.write_text(original)
    recovery = tmp_path / "repair.json"
    recovery.write_text(
        json.dumps(
            {
                "schema_version": "mdcompare-body-recovery-1",
                "documents": [
                    {
                        "knowledge_base": "funds",
                        "document_id": team_id("42"),
                        "side": "baseline",
                        "source_copy": "original.md",
                        "insertions": [
                            {
                                "byte_offset": original.encode().index(
                                    "业务正文".encode()
                                ),
                                "text": "> 转写说明：\n",
                            }
                        ],
                    }
                ],
            }
        )
    )
    config.write_text(
        config.read_text().replace(
            "[paths]\n", '[paths]\nbody_recovery="repair.json"\n'
        )
    )
    remote = load(tmp_path / "remote.json")
    remote["42"]["body"] = "# Title\n\n规则\n\n业务正文\n"
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    return config, differences, witness, recovery


def test_recovery_is_config_relative_and_resume_uses_frozen_evidence(tmp_path):
    config, differences, witness, recovery = prepared_recovery(tmp_path)
    run_path, run, plan = pause(config, differences)
    assert plan["documents"][0]["action"] == "reuse"
    assert Path(plan["body_recovery"]).is_relative_to(tmp_path / "state")
    recovery.write_text("LIVE CONFIG REPLACED")
    witness.write_text("LIVE WITNESS CHANGED")
    resumed = cli("apply", "--config", config, "--resume", run_path)
    assert resumed.returncode == 0, resumed.stderr
    assert (
        load(json.loads(resumed.stdout)["manifest"])["documents"][0]["action"]
        == "reuse"
    )
    state = load(tmp_path / "state/funds/state.json")["documents"][team_id("42")]
    assert "version" not in state["graph"]
    assert "ocr_report" not in run["artifacts"]


@pytest.mark.parametrize("remove_dependency", [False, True])
def test_changed_frozen_recovery_is_refused_before_corpus_publication(
    tmp_path, remove_dependency
):
    config, differences, _, _ = prepared_recovery(tmp_path)
    run_path, _, plan = pause(config, differences)
    snapshot = Path(plan["body_recovery"])
    frozen = snapshot.parent / load(snapshot)["documents"][0]["source_copy"]
    frozen.write_text("TAMPERED FROZEN BODY")
    if remove_dependency:
        (snapshot.parent / "dependencies.json").write_text("[]")
    original = next((tmp_path / "corpus").glob("*.md")).read_bytes()
    result = cli("apply", "--config", config, "--resume", run_path)
    assert result.returncode == 1
    assert "frozen body recovery missing or changed" in result.stderr
    assert next((tmp_path / "corpus").glob("*.md")).read_bytes() == original


@pytest.mark.parametrize(
    "mode",
    [
        "duplicate",
        "availability",
        "source",
        "missing",
        "reference",
        "duplicate_ref",
        "missing_ref",
        "report_digest",
        "sidecar_digest",
        "omitted_operations",
    ],
)
def test_invalid_authoritative_artifact_cannot_authorize_publication(tmp_path, mode):
    config, differences = instance(tmp_path)
    wrap(
        config,
        tmp_path,
        f"""import hashlib,json,subprocess,sys
from pathlib import Path
a=sys.argv[1:]
code=subprocess.call([sys.executable,'-m','mdcompare',*a])
if a[0]=='semantic' and code in (0,2):
 out=Path(a[a.index('--output')+1]);report=json.loads(out.read_text())
 diff=Path(a[a.index('--diff-output')+1]);index=json.loads(diff.read_text())
 evidence=diff.parent/index['documents'][0]['evidence']
 detail=json.loads(evidence.read_text())
 mode={mode!r}
 if mode=='duplicate':report['results'].append(report['results'][0])
 if mode=='availability':report['results'][0]['extraction']['available']=False
 if mode=='source':detail['source_inputs']['incoming']['path']='stale-source.md'
 if mode=='reference':detail['changes'][0]['new_ref']=999999
 if mode=='duplicate_ref':detail['changes'].append(detail['changes'][0])
 if mode=='missing_ref':detail['changes'][0].pop('new_ref')
 if mode=='omitted_operations':detail['changes']=[]
 if mode=='missing':evidence.unlink()
 else:
  evidence.write_text(json.dumps(detail))
  index['documents'][0]['sha256']=hashlib.sha256(evidence.read_bytes()).hexdigest() if mode!='sidecar_digest' else '0'*64
 out.write_text(json.dumps(report))
 index['report_sha256']=hashlib.sha256(out.read_bytes()).hexdigest() if mode!='report_digest' else '0'*64
 diff.write_text(json.dumps(index))
raise SystemExit(code)
""",
    )
    result = cli("apply", "--config", config, "--input", differences)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    expected = {
        "report_digest": "semantic report digest mismatch",
        "sidecar_digest": "semantic evidence digest mismatch",
        "omitted_operations": "incomplete semantic occurrence ownership",
    }
    if mode in expected:
        assert expected[mode] in result.stderr
    assert not list((tmp_path / "corpus").glob("*.md"))
    state = load(tmp_path / "state/funds/state.json")["documents"][team_id("42")]
    assert state["steps"]["compare"]["status"] == "unknown"
    assert not state.get("graph", {}).get("version")


def test_missing_diagnostic_reports_do_not_override_usable_authored_body(tmp_path):
    config, differences = instance(tmp_path)
    wrap(
        config,
        tmp_path,
        "import subprocess,sys\na=sys.argv[1:]\nif a[0]!='semantic':raise SystemExit(1)\nraise SystemExit(subprocess.call([sys.executable,'-m','mdcompare',*a]))\n",
    )
    _, run, plan = pause(config, differences)
    assert plan["documents"][0]["action"] == "compile"
    assert load(run["artifacts"]["semantic"])["results"][0]["extraction"]["available"]


def test_successful_graph_baseline_does_not_advance_on_equivalent_revision(tmp_path):
    config, differences = instance(tmp_path)
    graph = tmp_path / "graphify.py"
    graph.write_text(
        "import json,sys\nfrom pathlib import Path\nout=Path(sys.argv[sys.argv.index('--out')+1])/'graphify-out'\nout.mkdir(parents=True,exist_ok=True)\n(out/'graph.json').write_text(json.dumps({'nodes':[],'links':[]}))\n(out/'manifest.json').write_text('{}')\n"
    )
    config.write_text(
        config.read_text()
        + "graphify="
        + json.dumps([sys.executable, str(graph)])
        + "\n"
    )
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 0, first.stderr
    manifest = json.loads(first.stdout)["manifest"]
    compiled = cli("update-graph", "--config", config, "--input", manifest)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    first_state = load(tmp_path / "state/funds/state.json")["documents"][team_id("42")]
    baseline = first_state["graph_baseline"]
    assert baseline["version"] == VERSION
    witness = Path(baseline["path"]).read_bytes()
    remote = load(tmp_path / "remote.json")
    selection = load(differences)
    remote["42"].update(
        body="# Title\n\n**Full current knowledge.**\n", version="2026-11-13T10:00:00Z"
    )
    selection["documents"][0]["version"] = {
        "value": remote["42"]["version"],
        "precision": "instant",
        "utc": "2026-11-13T10:00:00+00:00",
    }
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    differences.write_text(json.dumps(selection))
    skipped = cli("apply", "--config", config, "--input", differences)
    assert skipped.returncode == 0, skipped.stderr
    assert (
        load(json.loads(skipped.stdout)["manifest"])["documents"][0]["action"]
        == "reuse"
    )
    state = load(tmp_path / "state/funds/state.json")["documents"][team_id("42")]
    assert state["graph_baseline"] == baseline
    assert Path(baseline["path"]).read_bytes() == witness
    assert state["graph"]["version"] == VERSION
