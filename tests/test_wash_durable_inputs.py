"""Durable input guarantees through public CLI and real Markdown artifacts."""

import json
import shutil
import sys
from pathlib import Path

import pytest

from tests.ocr_fixture import write_ocr_fixture
from tests.test_orchestrator_apply_cli import cli, instance


def enriched_instance(tmp_path):
    config, differences = instance(tmp_path)
    tool = tmp_path / "enrich.py"
    write_ocr_fixture(
        tool,
        """import json,sys
from pathlib import Path
args=sys.argv[1:]
def option(name):return Path(args[args.index(name)+1])
doc=json.loads(fixture_request.read_text())['documents'][0]
output=fixture_output;output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(Path(doc['path']).read_text()+'\\nTool-added evidence.\\n')
fixture_result({'schema_version':'mdocr-result-2','status':'completed','documents':[{'document_id':doc['document_id'],'status':'completed','output':str(output),'resources':[]}]})
with Path(__file__).with_name('ocr-calls').open('a') as log: log.write('call\\n')
""",
    )
    config.write_text(
        config.read_text().replace(
            "mdocr=" + json.dumps([sys.executable, "-m", "mdocr.cli"]),
            "mdocr=" + json.dumps([sys.executable, str(tool)]),
        )
    )
    return config, differences


def test_new_version_compares_raw_baseline_after_old_wash_is_deleted(tmp_path):
    config, differences = enriched_instance(tmp_path)
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 0, first.stderr
    shutil.rmtree(Path(json.loads(first.stdout)["run"]).parent)
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["42"]["version"] = "2026-11-13T10:00:00Z"
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    selected = json.loads(differences.read_text())
    selected["documents"][0]["version"] = {
        "value": "2026-11-13T10:00:00Z",
        "utc": "2026-11-13T10:00:00+00:00",
        "precision": "second",
    }
    differences.write_text(json.dumps(selected))
    second = cli("apply", "--config", config, "--input", differences)
    assert second.returncode == 0, second.stderr
    manifest = json.loads(Path(json.loads(second.stdout)["manifest"]).read_text())
    assert manifest["documents"][0]["action"] == "reuse"


def test_same_version_ocr_reuse_and_corpus_witness_survive_old_wash_deletion(tmp_path):
    config, differences = enriched_instance(tmp_path)
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 0, first.stderr
    first_result = json.loads(first.stdout)
    manifest = json.loads(Path(first_result["manifest"]).read_text())
    shutil.rmtree(Path(first_result["run"]).parent)
    witness = Path(manifest["documents"][0]["input_copy"])
    assert witness.is_file()
    assert witness.read_bytes() == Path(manifest["documents"][0]["path"]).read_bytes()
    second = cli("apply", "--config", config, "--input", differences)
    assert second.returncode == 0, second.stderr
    assert (tmp_path / "ocr-calls").read_text().splitlines() == ["call"]
    assert (tmp_path / "events").read_text().splitlines() == ["export"]


def test_raw_relative_images_remain_readable_after_wash_deletion(tmp_path):
    config, differences = enriched_instance(tmp_path)
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["42"]["body"] += "\n![diagram](../images/diagram.png)\n"
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    tool = tmp_path / "skylark.py"
    tool.write_text(
        tool.read_text().replace(
            "path.write_text(doc['body'])",
            "path.write_text(doc['body']); image=path.parent.parent/'images'/'diagram.png'; image.parent.mkdir(parents=True,exist_ok=True); __import__('PIL.Image',fromlist=['Image']).new('RGB',(2,2),'red').save(image)",
        )
    )
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 0, first.stderr
    shutil.rmtree(Path(json.loads(first.stdout)["run"]).parent)
    state = json.loads((tmp_path / "state" / "funds" / "state.json").read_text())
    raw = state["documents"]["42"]["raw"]
    assert (Path(raw["path"]).parent / "../images/diagram.png").is_file()
    assert Path(raw["resources"][0]["path"]).is_file()
    remote["42"]["version"] = "2026-11-13T10:00:00Z"
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    selected = json.loads(differences.read_text())
    selected["documents"][0]["version"] = {
        "value": "2026-11-13T10:00:00Z",
        "utc": "2026-11-13T10:00:00+00:00",
        "precision": "second",
    }
    differences.write_text(json.dumps(selected))
    second = cli("apply", "--config", config, "--input", differences)
    assert second.returncode == 0, second.stderr
    run = Path(json.loads(second.stdout)["run"]).parent
    comparison = json.loads((run / "compare-report.json").read_text())
    assert comparison["results"][0]["resources"]["status"] == "UNCHANGED"
    knowledge = json.loads((run / "knowledge-report.json").read_text())
    assert knowledge["results"][0]["status"] == "REUSE"


def test_unknown_ocr_report_remains_partial_and_retains_raw_baseline(tmp_path):
    config, differences = enriched_instance(tmp_path)
    tool = tmp_path / "enrich.py"
    tool.write_text(
        "import sys\nfrom pathlib import Path\na=sys.argv[1:]\nPath(a[a.index('--report')+1]).write_text('invalid')\nraise SystemExit(2)\n"
    )
    result = cli("apply", "--config", config, "--input", differences)
    assert result.returncode == 2, result.stderr
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    assert state["documents"]["42"]["steps"]["ocr"]["status"] == "unknown"
    shutil.rmtree(Path(json.loads(result.stdout)["run"]).parent)
    assert Path(state["documents"]["42"]["raw"]["path"]).is_file()


def test_successful_graph_witness_and_report_survive_wash_deletion(tmp_path):
    from tests.test_orchestrator_graph_cli import cli as graph_cli
    from tests.test_orchestrator_graph_cli import fixture

    config, manifest, source = fixture(tmp_path)
    result = graph_cli(config, manifest)
    assert result.returncode == 0, result.stderr
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    step = state["documents"]["42"]["steps"]["graph"]
    shutil.rmtree(manifest.parent)
    assert Path(step["input_copy"]).read_bytes() == source.read_bytes()
    report = json.loads(Path(step["report"]).read_text())
    assert report["status"] == "completed"
    assert report["candidate"] is None


def test_declared_ocr_images_are_relocated_before_corpus_publication(tmp_path):
    config, differences = enriched_instance(tmp_path)
    tool = tmp_path / "enrich.py"
    write_ocr_fixture(
        tool,
        """import json,sys
from pathlib import Path
from urllib.parse import quote
args=sys.argv[1:]
def option(name):return Path(args[args.index(name)+1])
doc=json.loads(fixture_request.read_text())['documents'][0]
output=fixture_output;output.parent.mkdir(parents=True,exist_ok=True)
image=output.parent/'prepared image.png';image.write_bytes(b'original image bytes')
output.write_text(Path(doc['path']).read_text()+'\\n![prepared]('+quote(str(image),safe='/')+')\\n')
resource={'resource_id':'image','status':'success','source_path':str(image),'prepared_image':str(image)}
fixture_result({'schema_version':'mdocr-result-2','documents':[{'document_id':doc['document_id'],'status':'completed','output':str(output),'resources':[resource],'ocr':{'images':[resource]}}]})
""",
    )
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 0, first.stderr
    manifest = json.loads(Path(json.loads(first.stdout)["manifest"]).read_text())
    document = manifest["documents"][0]
    shutil.rmtree(Path(json.loads(first.stdout)["run"]).parent)
    image = Path(document["ocr"]["images"][0]["prepared_image"])
    assert image.read_bytes() == b"original image bytes"
    from urllib.parse import quote

    assert quote(str(image), safe="/") in Path(document["path"]).read_text()
    assert (
        Path(document["input_copy"]).read_bytes() == Path(document["path"]).read_bytes()
    )


def test_unattempted_ocr_reads_retained_absolute_image_after_old_wash_deleted(tmp_path):
    from tests.test_orchestrator_apply_cli import two_sources

    config, differences = enriched_instance(tmp_path)
    two_sources(tmp_path, differences)
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["43"]["body"] += "\n![diagram](ABSOLUTE_IMAGE)\n"
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    tool = tmp_path / "skylark.py"
    tool.write_text(
        tool.read_text().replace(
            "path.write_text(doc['body'])",
            "image=path.parent.parent/'images'/('diagram-'+item['document_id']+'.png'); image.parent.mkdir(parents=True,exist_ok=True); __import__('PIL.Image',fromlist=['Image']).new('RGB',(2,2),'red').save(image); path.write_text(doc['body'].replace('ABSOLUTE_IMAGE',str(image)))",
        )
    )
    ocr = tmp_path / "enrich.py"
    write_ocr_fixture(
        ocr,
        """import json,sys
from pathlib import Path
args=sys.argv[1:]
def option(name):return Path(args[args.index(name)+1])
doc=json.loads(fixture_request.read_text())['documents'][0]
body=Path(doc['path']).read_text()
if not Path(__file__).with_name('allow').exists():
 fixture_result({'schema_version':'mdocr-result-2','causes':['OCR_AUTH_UNAVAILABLE'],'documents':[{'document_id':doc['document_id'],'status':'partial','output':None,'resources':[]}]});raise SystemExit(2)
if doc['document_id']=='43':
 target=body.split('](')[1].split(')')[0]
 assert (Path(doc['path']).parent/target).is_file(),target
 with Path(__file__).with_name('valid-images').open('a') as log:log.write('43\\n')
output=fixture_output;output.parent.mkdir(parents=True,exist_ok=True);output.write_text(body)
fixture_result({'schema_version':'mdocr-result-2','documents':[{'document_id':doc['document_id'],'status':'completed','output':str(output),'resources':[]}]})
""",
    )
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 2, first.stderr
    run = Path(json.loads(first.stdout)["run"]).parent
    original_catalog = json.loads((run / "incoming.json").read_text())
    identifier = original_catalog["documents"][1]["resources"][0]["resource_id"]
    shutil.rmtree(run)
    (tmp_path / "allow").touch()
    second = cli("apply", "--config", config, "--input", differences)
    assert second.returncode == 2, second.stderr
    assert (tmp_path / "valid-images").read_text().splitlines() == ["43"]
    next_run = Path(json.loads(second.stdout)["run"]).parent
    comparison = json.loads((next_run / "compare-report.json").read_text())
    item = next(row for row in comparison["results"] if row["document_id"] == "43")
    assert item["resources"]["status"] == "UNCHANGED"
    assert item["resources"]["items"][0]["resource_id"] == identifier


@pytest.mark.parametrize("has_ocr_output", [True, False])
def test_partial_relative_image_is_readable_in_formal_corpus_after_wash_deleted(
    tmp_path,
    has_ocr_output,
):
    config, differences = enriched_instance(tmp_path)
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["42"]["body"] += "\n![diagram](../images/diagram.png)\n"
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    tool = tmp_path / "skylark.py"
    tool.write_text(
        tool.read_text().replace(
            "path.write_text(doc['body'])",
            "path.write_text(doc['body']); image=path.parent.parent/'images'/'diagram.png'; image.parent.mkdir(parents=True,exist_ok=True); __import__('PIL.Image',fromlist=['Image']).new('RGB',(2,2),'red').save(image)",
        )
    )
    ocr = tmp_path / "enrich.py"
    ocr.write_text(
        ocr.read_text().replace("'status':'completed'", "'status':'partial'")
        + "\nraise SystemExit(2)\n"
    )
    if not has_ocr_output:
        ocr.write_text(ocr.read_text().replace("'output':str(output)", "'output':None"))
    result = cli("apply", "--config", config, "--input", differences)
    assert result.returncode == 2, result.stderr
    document = json.loads(Path(json.loads(result.stdout)["manifest"]).read_text())[
        "documents"
    ][0]
    shutil.rmtree(Path(json.loads(result.stdout)["run"]).parent)
    descriptors = tmp_path / "published-input.json"
    descriptors.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "documents": [
                    {
                        "knowledge_base": "funds",
                        "document_id": "42",
                        "path": document["path"],
                        "content_kind": "raw",
                        "metadata": document["metadata"],
                    }
                ],
            }
        )
    )
    import os
    import subprocess

    catalog = tmp_path / "published-catalog.json"
    checked = subprocess.run(
        [
            sys.executable,
            "-m",
            "mdcompare",
            "catalog",
            "--input",
            str(descriptors),
            "--output",
            str(catalog),
        ],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
    resource = json.loads(catalog.read_text())["documents"][0]["resources"][0]
    assert resource.get("path"), resource
    assert Path(resource["path"]).is_file()


def test_real_mdocr_partial_keeps_formal_image_after_wash_deletion(
    tmp_path, monkeypatch
):
    import os
    import subprocess

    monkeypatch.delenv("PADDLEOCR_TOKEN", raising=False)
    config, differences = instance(tmp_path)
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["42"]["body"] += "\n![diagram](../images/diagram.png)\n"
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    tool = tmp_path / "skylark.py"
    tool.write_text(
        tool.read_text().replace(
            "path.write_text(doc['body'])",
            "path.write_text(doc['body']); image=path.parent.parent/'images'/'diagram.png'; image.parent.mkdir(parents=True,exist_ok=True); __import__('PIL.Image',fromlist=['Image']).new('RGB',(8,8),'red').save(image)",
        )
    )
    result = cli("apply", "--config", config, "--input", differences)
    assert result.returncode == 2, result.stderr
    document = json.loads(Path(json.loads(result.stdout)["manifest"]).read_text())[
        "documents"
    ][0]
    shutil.rmtree(Path(json.loads(result.stdout)["run"]).parent)
    source = tmp_path / "published-input.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "documents": [
                    {
                        "knowledge_base": "funds",
                        "document_id": "42",
                        "path": document["path"],
                        "content_kind": "raw",
                        "metadata": document["metadata"],
                    }
                ],
            }
        )
    )
    catalog = tmp_path / "published-catalog.json"
    checked = subprocess.run(
        [
            sys.executable,
            "-m",
            "mdcompare",
            "catalog",
            "--input",
            str(source),
            "--output",
            str(catalog),
        ],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
    resource = json.loads(catalog.read_text())["documents"][0]["resources"][0]
    assert resource.get("path") and Path(resource["path"]).is_file(), resource


@pytest.mark.parametrize("remove_old_wash", [False, True])
def test_real_mdocr_retained_mixed_image_targets_remain_supported(
    tmp_path, monkeypatch, remove_old_wash
):
    from tests.test_orchestrator_apply_cli import two_sources

    monkeypatch.delenv("PADDLEOCR_TOKEN", raising=False)
    config, differences = instance(tmp_path)
    two_sources(tmp_path, differences)
    remote = json.loads((tmp_path / "remote.json").read_text())
    if remove_old_wash:
        remote["42"]["body"] += "\n![stop](../images/relative-42.png)\n"
    remote["43"]["body"] += (
        "\n![absolute](ABSOLUTE_IMAGE)\n![relative](../images/relative-43.png)\n"
    )
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    tool = tmp_path / "skylark.py"
    tool.write_text(
        tool.read_text().replace(
            "path.write_text(doc['body'])",
            "images=path.parent.parent/'images'; images.mkdir(parents=True,exist_ok=True); image=__import__('PIL.Image',fromlist=['Image']).new('RGB',(8,8),'red'); absolute=images/('absolute-'+item['document_id']+'.png'); image.save(absolute); image.save(images/('relative-'+item['document_id']+'.png')); path.write_text(doc['body'].replace('ABSOLUTE_IMAGE',str(absolute)))",
        )
    )
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 2, first.stderr
    if remove_old_wash:
        shutil.rmtree(Path(json.loads(first.stdout)["run"]).parent)
        second = cli("apply", "--config", config, "--input", differences)
        assert second.returncode == 2, second.stderr
    else:
        second = first
    directory = Path(json.loads(second.stdout)["run"]).parent
    report = next(
        json.loads(p.read_text())
        for p in directory.glob("ocr-*-report.json")
        if json.loads(
            p.with_name(p.name.replace("-report.json", "-request.json")).read_text()
        )["documents"][0]["document_id"]
        == "43"
    )
    assert report.get("causes"), report
    resources = report["documents"][0]["resources"]
    assert resources and not any(
        "UNSUPPORTED" in r.get("reason", "") for r in resources
    ), resources
