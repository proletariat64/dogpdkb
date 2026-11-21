"""Publication retention through public CLIs and real filesystem faults."""

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from scripts.smoke_install import GRAPHIFY_PREFLIGHT, GRAPHIFY_PROOF
from tests.source_fixture import team_id
from tests.test_orchestrator_apply_cli import cli
from tests.test_wash_durable_inputs import enriched_instance

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("absolute", [False, True])
@pytest.mark.parametrize("has_ocr_output", [False, True])
def test_local_fragments_survive_ocr_input_publication_and_purge(
    tmp_path, absolute, has_ocr_output
):
    import re

    config, differences = enriched_instance(tmp_path)
    remote_path = tmp_path / "remote.json"
    remote = json.loads(remote_path.read_text())
    remote["42"]["body"] += (
        '\n<svg><use href="RESOURCE#check"></use>'
        '<use xlink:href="RESOURCE#cross%20mark"></use></svg>\n'
    )
    remote_path.write_text(json.dumps(remote))
    exporter = tmp_path / "skylark.py"
    exporter.write_text(
        exporter.read_text().replace(
            "path.write_text(doc['body'])",
            "image=path.parent.parent/'images'/'icons #.svg'; "
            "image.parent.mkdir(parents=True,exist_ok=True); "
            'image.write_text(\'<svg><symbol id="check"/><symbol id="cross mark"/></svg>\'); '
            "target=__import__('urllib.parse',fromlist=['quote']).quote("
            + ("str(image)" if absolute else "'../images/'+image.name")
            + ",safe='/'); path.write_text(doc['body'].replace('RESOURCE',target))",
        )
    )
    ocr = tmp_path / "enrich.py"
    script = ocr.read_text().replace(
        "output.write_text(",
        "Path(__file__).with_name('observed-ocr.md').write_text(Path(doc['path']).read_text()); output.write_text(",
    )
    if not has_ocr_output:
        script = (
            script.replace("'output':str(output)", "'output':None").replace(
                "'status':'completed'", "'status':'partial'"
            )
            + "\nraise SystemExit(2)\n"
        )
    ocr.write_text(script)

    result = cli("apply", "--config", config, "--input", differences)
    assert result.returncode == (0 if has_ocr_output else 2), result.stderr
    output = json.loads(result.stdout)
    run = Path(output["run"]).parent
    artifacts = json.loads(Path(output["run"]).read_text())["artifacts"]
    request = json.loads(Path(artifacts["ocr_request"]).read_text())["documents"][0]
    observed = (tmp_path / "observed-ocr.md").read_text()
    targets = re.findall(r'(?:xlink:)?href="([^"]+)"', observed)
    assert [urlsplit(target).fragment for target in targets] == [
        "check",
        "cross%20mark",
    ]
    for target in targets:
        path = (Path(request["path"]).parent / unquote(urlsplit(target).path)).resolve()
        assert path.is_file(), target
    document = json.loads(Path(output["manifest"]).read_text())["documents"][0]
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    raw = Path(state["documents"][team_id("42")]["raw"]["path"])
    assert raw.read_bytes() == (run / "raw/0.md").read_bytes()
    published = Path(document["path"])
    targets = re.findall(r'(?:xlink:)?href="([^"]+)"', published.read_text())
    assert [urlsplit(target).fragment for target in targets] == [
        "check",
        "cross%20mark",
    ]
    removed = cli("purge", "--config", config, "--run", output["run"])
    assert removed.returncode == 0, removed.stderr
    assert not run.exists()
    for target in targets:
        assert "%20%23.svg#" in target
        path = (published.parent / unquote(urlsplit(target).path)).resolve()
        assert (
            path.read_text()
            == '<svg><symbol id="check"/><symbol id="cross mark"/></svg>'
        )


def image_instance(tmp_path):
    config, differences = enriched_instance(tmp_path)
    remote_path = tmp_path / "remote.json"
    remote = json.loads(remote_path.read_text())
    remote["42"]["body"] += "\n![diagram](image.png)\n"
    remote_path.write_text(json.dumps(remote))
    tool = tmp_path / "skylark.py"
    tool.write_text(
        tool.read_text().replace(
            "path.write_text(doc['body'])",
            "path.write_text(doc['body']); (path.parent/'image.png').write_bytes(doc['version'].encode())",
        )
    )
    return config, differences


def next_version(tmp_path, differences):
    version = "2026-11-13T10:00:00Z"
    remote_path = tmp_path / "remote.json"
    remote = json.loads(remote_path.read_text())
    remote["42"].update(version=version, body="# Title\n\nReplacement knowledge.\n")
    remote_path.write_text(json.dumps(remote))
    selected = json.loads(differences.read_text())
    selected["documents"][0]["version"] = {
        "value": version,
        "utc": "2026-11-13T10:00:00+00:00",
        "precision": "instant",
    }
    differences.write_text(json.dumps(selected))


def fault_cli(arguments, fault):
    """Deny an external filesystem operation, without replacing product calls."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys,runpy\nfrom pathlib import Path\n"
            + fault
            + "\nsys.argv=['dogpdkb',*sys.argv[1:]]\n"
            "runpy.run_module('dogpdkb',run_name='__main__')\n",
            *map(str, arguments),
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def test_failed_asset_retention_keeps_previous_published_image(tmp_path):
    config, differences = image_instance(tmp_path)
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 0, first.stderr
    manifest = json.loads(Path(json.loads(first.stdout)["manifest"]).read_text())
    document = manifest["documents"][0]
    corpus = Path(document["path"])
    before = corpus.read_bytes()
    image = Path(document["ocr"]["images"][0]["source_path"])
    old_image = image.read_bytes()
    next_version(tmp_path, differences)
    remote_path = tmp_path / "remote.json"
    remote = json.loads(remote_path.read_bytes())
    remote["42"]["body"] += "\n![replacement diagram](image.png)\n"
    remote_path.write_text(json.dumps(remote))
    failed = fault_cli(
        ["apply", "--config", config, "--input", differences],
        """def deny_retention(event,args):
 if event=='shutil.copyfile' and '/.raw-' in str(args[1]) and str(args[1]).endswith('.png'):
  raise OSError('INJECTED_ASSET_COPY_FAILURE')
sys.addaudithook(deny_retention)
""",
    )
    assert failed.returncode == 1, failed.stderr
    assert "INJECTED_ASSET_COPY_FAILURE" in failed.stderr
    assert corpus.read_bytes() == before
    assert image.is_file(), "last published corpus lost its prepared image"
    assert image.read_bytes() == old_image


@pytest.mark.parametrize("interrupt_state_save", [False, True])
def test_old_corpus_witness_remains_immutable_across_publication(
    tmp_path, interrupt_state_save
):
    config, differences = image_instance(tmp_path)
    graphify = tmp_path / "graphify.py"
    graphify.write_text(
        GRAPHIFY_PREFLIGHT
        + """import json,sys
from pathlib import Path
Path(__file__).with_name('graph-called').touch()
args=sys.argv[1:]; output=Path(args[args.index('--out')+1])/'graphify-out'
output.mkdir(parents=True,exist_ok=True)
(output/'graph.json').write_text(json.dumps({'nodes':[],'links':[]}))
(output/'manifest.json').write_text('{}')
"""
        + GRAPHIFY_PROOF
    )
    config.write_text(
        config.read_text()
        + "graphify="
        + json.dumps([sys.executable, str(graphify)])
        + "\n"
    )
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 0, first.stderr
    manifest_path = Path(json.loads(first.stdout)["manifest"])
    document = json.loads(manifest_path.read_text())["documents"][0]
    corpus, witness = Path(document["path"]), Path(document["input_copy"])
    old_bytes = witness.read_bytes()
    image = Path(document["ocr"]["images"][0]["source_path"])
    old_image = image.read_bytes()
    next_version(tmp_path, differences)
    arguments = ["apply", "--config", config, "--input", differences]
    if interrupt_state_save:
        state = tmp_path / "state/funds/state.json"
        second = fault_cli(
            arguments,
            f"""def deny_state_commit(event,args):
 if event=='os.rename' and Path(args[1])==Path({str(state)!r}):
  if 'Replacement knowledge.' in Path({str(corpus)!r}).read_text():
   raise OSError('INJECTED_CORPUS_STATE_FAILURE')
sys.addaudithook(deny_state_commit)
""",
        )
        assert second.returncode == 1, second.stderr
        assert "INJECTED_CORPUS_STATE_FAILURE" in second.stderr
    else:
        second = cli(*arguments)
        assert second.returncode == 0, second.stderr
        latest = json.loads(Path(json.loads(second.stdout)["manifest"]).read_text())[
            "documents"
        ][0]
        assert latest["input_copy"] != str(witness)
        assert Path(latest["input_copy"]).read_bytes() == corpus.read_bytes()
    assert corpus.read_bytes() != old_bytes
    assert witness.read_bytes() == old_bytes
    assert image.read_bytes() == old_image
    rejected = cli("update-graph", "--config", config, "--input", manifest_path)
    assert rejected.returncode == 1, rejected.stderr
    assert not (tmp_path / "graph-called").exists()


def test_ocr_request_survives_cleanup_after_earlier_export_failure(tmp_path):
    from tests.test_wash_cleanup_cli import instance

    config, tool = instance(tmp_path)
    script = tool.read_text().replace(
        "    write(flag('--output'), {'schema':'skylark-export-result-1'",
        "    if rows[0]['document_id']=='1': rows[0]['status']='failed'\n"
        "    write(flag('--output'), {'schema':'skylark-export-result-1'",
    )
    script = script.replace(
        "if tool in ('mdcompare', 'mdocr'):",
        "if tool == 'mdocr' and args[0] == 'run':\n"
        "    from mdocr.plan import read_plan\n"
        "    from mdocr.runner import Execution, _sha\n"
        "    p=pathlib.Path(args[args.index('--plan')+1]); r=pathlib.Path(args[args.index('--result')+1])\n"
        "    plan=read_plan(p); e=Execution(plan,_sha(p.read_bytes()),p.with_name(p.name+'.execution.json'),r,p.with_name(p.name+'.evidence'),False)\n"
        "    _,_,layout=e.plan_jobs(); e.publish([pathlib.Path(d['path']).read_bytes().decode() for d in plan['documents']],layout)\n"
        "    data=json.loads(r.read_bytes()); data['status']='partial'; data['documents'][0].update(status='partial',output=None,ocr=None,reason='FIXTURE_PARTIAL')\n"
        "    for target in (r, p.with_name(p.name+'.execution.json')): target.write_text(json.dumps(data))\n"
        "    raise SystemExit(2)\n"
        "if tool in ('mdcompare', 'mdocr'):",
    )
    tool.write_text(script)
    result = cli("sync", "--config", config)
    assert result.returncode == 2, result.stderr
    run_path = Path(json.loads(result.stdout)["run"])
    record = json.loads(run_path.read_text())
    assert record["cleanup"]["status"] == "completed"
    assert {failure["step"] for failure in record["failures"]} >= {"export", "ocr"}
    request = Path(record["artifacts"]["ocr_request"])
    assert request.is_file(), "cleanup lost the actual failed OCR invocation"
    assert json.loads(request.read_text())["documents"][0]["document_id"] == team_id(
        "2"
    )
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    assert state["documents"][team_id("2")]["steps"]["ocr"]["input"] == str(request)


def test_later_run_uses_new_publication_generation(tmp_path):
    from tests.test_orchestrator_ocr_batch_cli import _native_instance

    config, differences = _native_instance(tmp_path)
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 2, first.stderr
    first_result = json.loads(first.stdout)
    before = json.loads(Path(first_result["manifest"]).read_text())
    old_document = next(
        row for row in before["documents"] if row["document_id"] == team_id("43")
    )
    assert (
        old_document["pending_steps"] == []
    )  # Enrichment is advisory, not required graph work.
    assert any(failure["step"] == "ocr" for failure in before["failures"])
    old_witness = Path(old_document["input_copy"])
    old_bytes = old_witness.read_bytes()
    resumed = cli("apply", "--config", config, "--input", differences)
    assert resumed.returncode == 0, resumed.stderr
    resumed_result = json.loads(resumed.stdout)
    assert resumed_result["run"] != first_result["run"]
    after = json.loads(Path(resumed_result["manifest"]).read_text())
    new_document = next(
        row for row in after["documents"] if row["document_id"] == team_id("43")
    )
    assert new_document["pending_steps"] == []
    assert new_document["input_copy"] != str(old_witness)
    assert old_witness.read_bytes() == old_bytes
    assert Path(new_document["input_copy"]).read_bytes() != old_bytes


@pytest.mark.parametrize(
    "markup",
    [
        '<img src="ABSOLUTE_IMAGE" alt="Diagram">',
        "<img src='ABSOLUTE_IMAGE' alt='Diagram'>",
        "<img src=ABSOLUTE_IMAGE>",
        '![diagram][image]\n[image]: ../images/diagram.png "Original"',
        '![diagram][image]\n[image]: <../images/diagram.png> "Original"',
        "![diagram][`sample`]\n\n[`sample`]: ../images/diagram.png",
        '<img srcset="ABSOLUTE_IMAGE 1x, ../images/diagram.png 2x">',
        "<img alt=\"literal src='../images/diagram.png'\" src='../images/diagram.png'>",
        '<img alt="literal ![example](../images/diagram.png)" src="../images/diagram.png">',
        'Inline <img alt="literal ![example](../images/diagram.png)" src="../images/diagram.png">',
        '<img srcset="1x 1x, ../images/diagram.png 2x">',
    ],
)
def test_catalogued_noninline_resource_survives_wash_purge(tmp_path, markup):
    config, differences = enriched_instance(tmp_path)
    remote_path = tmp_path / "remote.json"
    remote = json.loads(remote_path.read_text())
    remote["42"]["body"] += "\n" + markup + "\n\nliteral target: ABSOLUTE_IMAGE\n"
    remote_path.write_text(json.dumps(remote))
    exporter = tmp_path / "skylark.py"
    exporter.write_text(
        exporter.read_text().replace(
            "path.write_text(doc['body'])",
            "image=path.parent.parent/'images'/'diagram.png'; "
            "image.parent.mkdir(parents=True,exist_ok=True); "
            "__import__('PIL.Image',fromlist=['Image']).new('RGB',(2,2),'red').save(image); "
            "__import__('PIL.Image',fromlist=['Image']).new('RGB',(2,2),'red').save(path.parent/'1x',format='PNG'); "
            "path.write_text(doc['body'].replace('ABSOLUTE_IMAGE',str(image)))",
        )
    )
    result = cli("apply", "--config", config, "--input", differences)
    assert result.returncode == 0, result.stderr
    run = Path(json.loads(result.stdout)["run"])
    document = json.loads(Path(json.loads(result.stdout)["manifest"]).read_text())[
        "documents"
    ][0]
    raw_source = next((run.parent / "raw").glob("*.md")).read_bytes()
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    assert (
        Path(state["documents"][team_id("42")]["raw"]["path"]).read_bytes()
        == raw_source
    )
    original_image = run.parent / "images/diagram.png"
    published = Path(document["path"]).read_text()
    assert f"literal target: {original_image}" in published
    if "literal src=" in markup:
        assert "alt=\"literal src='../images/diagram.png'\"" in published
    if "literal ![example]" in markup:
        assert 'alt="literal ![example](../images/diagram.png)"' in published
        assert document["ocr"]["images"] == []
    if 'srcset="1x 1x,' in markup:
        import re

        match = re.search(r'srcset="([^"]+)"', published)
        assert match is not None
        srcset = match.group(1)
        assert [candidate.split()[1] for candidate in srcset.split(",")] == ["1x", "2x"]
    removed = cli("purge", "--config", config, "--run", run)
    assert removed.returncode == 0, removed.stderr
    assert not run.parent.exists()
    descriptors = tmp_path / "published.json"
    descriptors.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "documents": [
                    {
                        "knowledge_base": "funds",
                        "document_id": "42",
                        "content_kind": "raw",
                        "path": document["path"],
                        "metadata": document["metadata"],
                    }
                ],
            }
        )
    )
    catalog = tmp_path / "catalog.json"
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
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert checked.returncode == 0, checked.stderr
    resources = json.loads(catalog.read_text())["documents"][0]["resources"]
    assert resources
    if "literal ![example]" in markup:
        # The catalog conservatively also reports Markdown-looking alt text.
        # The HTML destination itself must survive; the literal stays literal.
        import re
        from urllib.parse import unquote

        live = re.search(r'\bsrc="([^"]+)"', published)
        assert live and Path(unquote(live.group(1))).is_file()
    else:
        assert all(
            resource.get("path") and Path(resource["path"]).is_file()
            for resource in resources
        )


@pytest.mark.parametrize(
    "literal",
    [
        '```html\n<img src="../images/diagram.png">\n![example](../images/diagram.png)\n[example]: ../images/diagram.png\n```',
        '~~~html\n<img src="../images/diagram.png">\n![example](../images/diagram.png)\n[example]: ../images/diagram.png\n~~~',
        '`<img src="../images/diagram.png">` and `![example](../images/diagram.png)`',
        "```![example](../images/diagram.png)```",
        '<!--\n<img src="../images/diagram.png">\n![example](../images/diagram.png)\n[example]: ../images/diagram.png\n-->',
    ],
)
def test_resource_relocation_preserves_literal_examples(tmp_path, literal):
    import re
    from urllib.parse import unquote

    config, differences = enriched_instance(tmp_path)
    remote_path = tmp_path / "remote.json"
    remote = json.loads(remote_path.read_text())
    remote["42"]["body"] += (
        '\n<img alt="live" src="../images/diagram.png">\n\n' + literal + "\n"
    )
    remote_path.write_text(json.dumps(remote))
    exporter = tmp_path / "skylark.py"
    exporter.write_text(
        exporter.read_text().replace(
            "path.write_text(doc['body'])",
            "image=path.parent.parent/'images'/'diagram.png'; "
            "image.parent.mkdir(parents=True,exist_ok=True); "
            "__import__('PIL.Image',fromlist=['Image']).new('RGB',(2,2),'red').save(image); "
            "path.write_text(doc['body'])",
        )
    )
    result = cli("apply", "--config", config, "--input", differences)
    assert result.returncode in {0, 2}, result.stderr
    run = Path(json.loads(result.stdout)["run"])
    document = json.loads(Path(json.loads(result.stdout)["manifest"]).read_text())[
        "documents"
    ][0]
    published = Path(document["path"]).read_text()
    assert literal in published, "relocation changed a literal example"
    live = re.search(r'<img alt="live" src="([^"]+)">', published)
    assert live
    target = Path(unquote(live.group(1)))
    assert target.is_absolute()
    removed = cli("purge", "--config", config, "--run", run)
    assert removed.returncode == 0, removed.stderr
    assert target.is_file()
    assert literal in Path(document["path"]).read_text()


def test_unmatched_backtick_does_not_hide_image_in_later_paragraph(tmp_path):
    import re
    from urllib.parse import unquote

    config, differences = enriched_instance(tmp_path)
    remote_path = tmp_path / "remote.json"
    remote = json.loads(remote_path.read_text())
    remote["42"]["body"] += (
        "\n`unfinished\n\n![live](../images/diagram.png)\n\n`example`\n"
    )
    remote_path.write_text(json.dumps(remote))
    exporter = tmp_path / "skylark.py"
    exporter.write_text(
        exporter.read_text().replace(
            "path.write_text(doc['body'])",
            "image=path.parent.parent/'images'/'diagram.png'; "
            "image.parent.mkdir(parents=True,exist_ok=True); "
            "__import__('PIL.Image',fromlist=['Image']).new('RGB',(2,2),'red').save(image); "
            "path.write_text(doc['body'])",
        )
    )
    result = cli("apply", "--config", config, "--input", differences)
    assert result.returncode in {0, 2}, result.stderr
    output = json.loads(result.stdout)
    document = json.loads(Path(output["manifest"]).read_text())["documents"][0]
    published = Path(document["path"]).read_text()
    assert "`unfinished" in published and "`example`" in published
    live = re.search(r"!\[live\]\(([^)]+)\)", published)
    assert live
    image = Path(unquote(live.group(1)))
    assert image.is_absolute(), "an unmatched code delimiter hid a live destination"
    removed = cli("purge", "--config", config, "--run", output["run"])
    assert removed.returncode == 0, removed.stderr
    assert image.is_file()
