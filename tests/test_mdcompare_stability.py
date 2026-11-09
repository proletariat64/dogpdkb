"""CLI-observable fixed-input and report failure acceptance."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def document(path, document_id="1", **updates):
    value = {
        "knowledge_base": "book",
        "document_id": document_id,
        "content_kind": "raw",
        "path": str(path),
        "metadata": {
            "name": "Name",
            "logical_path": "folder",
            "source_ref": "source:1",
        },
        "resources": [],
    }
    value.update(updates)
    return value


def run_cli(
    tmp_path, baseline, incoming, *, output=None, human=None, hook=None, extra=()
):
    old = tmp_path / "baseline.json"
    new = tmp_path / "incoming.json"
    old.write_text(json.dumps({"schema_version": "1.0", "documents": baseline}))
    new.write_text(json.dumps({"schema_version": "1.0", "documents": incoming}))
    out = output or tmp_path / "decisions.json"
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src"))
    if hook:
        hooks = tmp_path / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "sitecustomize.py").write_text(hook)
        env["PYTHONPATH"] = str(hooks) + os.pathsep + env["PYTHONPATH"]
    args = [
        sys.executable,
        "-m",
        "mdcompare",
        "compare",
        "--baseline",
        str(old),
        "--incoming",
        str(new),
        "--output",
        str(out),
    ]
    args += list(extra)
    if human:
        args += ["--report", str(human)]
    result = subprocess.run(args, env=env, capture_output=True, text=True, check=False)
    return result, out


def test_failed_second_report_does_not_publish_first_report(tmp_path):
    human = tmp_path / "review.md"
    human.write_text("previous human report")
    output = tmp_path / "missing" / "decisions.json"
    result, _ = run_cli(tmp_path, [], [], output=output, human=human)
    assert result.returncode == 1
    assert human.read_text() == "previous human report"
    assert not output.exists()
    assert not list(tmp_path.glob(".mdcompare-*"))


@pytest.mark.parametrize("alias_kind", ["unread_resource", "symlink", "hardlink"])
def test_reports_cannot_overwrite_referenced_input_aliases(tmp_path, alias_kind):
    raw = tmp_path / "raw.md"
    raw.write_text("same")
    evidence = tmp_path / "private-evidence.bin"
    evidence.write_bytes(b"must remain unchanged")
    row = document(raw)
    if alias_kind == "unread_resource":
        row["resources"] = [
            {
                "resource_id": "r",
                "kind": "image",
                "url": "image.png",
                "path": str(evidence),
            }
        ]
        output = evidence
    elif alias_kind == "symlink":
        output = tmp_path / "output.json"
        output.symlink_to(raw)
    else:
        output = tmp_path / "output.json"
        os.link(raw, output)
    result, _ = run_cli(tmp_path, [row], [row], output=output)
    assert result.returncode == 1
    assert raw.read_text() == "same"
    assert evidence.read_bytes() == b"must remain unchanged"
    assert "REPORT_INPUT_COLLISION" in result.stderr


def mutation_hook(trigger, victim):
    return f"""
import builtins, io, os
_original_open = io.open
_changed = False
class ChangingFile:
    def __init__(self, stream): self.stream = stream
    def __enter__(self): return self
    def __exit__(self, *args): return self.stream.__exit__(*args)
    def __getattr__(self, name): return getattr(self.stream, name)
    def read(self, *args):
        global _changed
        data = self.stream.read(*args)
        if not _changed:
            _changed = True
            with builtins.open({str(victim)!r}, 'wb') as target:
                target.write(b'changed during comparison')
        return data

def changing_open(file, *args, **kwargs):
    stream = _original_open(file, *args, **kwargs)
    if not isinstance(file, int) and os.fspath(file) == {str(trigger)!r} and args and args[0] == 'rb':
        return ChangingFile(stream)
    return stream
io.open = changing_open
"""


def test_observed_input_change_is_unknown_not_a_text_update(tmp_path):
    raw = tmp_path / "raw.md"
    raw.write_text("same")
    result, output = run_cli(
        tmp_path, [document(raw)], [document(raw)], hook=mutation_hook(raw, raw)
    )
    assert result.returncode == 2, result.stderr
    report = json.loads(output.read_text())
    assert report["results"][0]["action"] == "UNKNOWN"
    assert "INPUT_CHANGED" in report["results"][0]["reason_codes"]


def test_late_change_invalidates_prior_document_without_losing_other_results(tmp_path):
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("first")
    second.write_text("second")
    docs = [document(first, "1"), document(second, "2")]
    result, output = run_cli(tmp_path, docs, docs, hook=mutation_hook(second, first))
    assert result.returncode == 2, result.stderr
    report = json.loads(output.read_text())
    assert [r["action"] for r in report["results"]] == ["UNKNOWN", "SKIP_VERIFIED"]
    assert report["summary"]["total"] == sum(report["summary"]["actions"].values()) == 2
    assert "INPUT_CHANGED" in report["results"][0]["reason_codes"]


def test_manifest_drift_is_global_failure_and_does_not_publish_report(tmp_path):
    raw = tmp_path / "raw.md"
    raw.write_text("same")
    result, output = run_cli(
        tmp_path,
        [document(raw)],
        [document(raw)],
        hook=mutation_hook(raw, tmp_path / "incoming.json"),
    )
    assert result.returncode == 1
    assert not output.exists()
    assert "MANIFEST_CHANGED" in result.stderr


def test_publication_replace_failure_restores_both_prior_reports(tmp_path):
    human = tmp_path / "review.md"
    output = tmp_path / "decisions.json"
    human.write_text("previous human report")
    output.write_text("previous machine report")
    hook = f"""
import os
_original_replace = os.replace
def failing_replace(source, destination, *args, **kwargs):
    if os.fspath(destination) == {str(output)!r}:
        raise OSError("injected private-path failure")
    return _original_replace(source, destination, *args, **kwargs)
os.replace = failing_replace
"""
    result, _ = run_cli(tmp_path, [], [], output=output, human=human, hook=hook)
    assert result.returncode == 1
    assert human.read_text() == "previous human report"
    assert output.read_text() == "previous machine report"
    assert not list(tmp_path.glob(".mdcompare-*"))
    assert result.stderr.strip() == "mdcompare: REPORT_WRITE_FAILED"


def test_unreadable_item_keeps_other_results_and_private_deterministic_reports(
    tmp_path,
):
    valid = tmp_path / "valid.md"
    valid.write_text("same")
    docs = [document(tmp_path / "missing-private-token.md", "2"), document(valid, "1")]
    human = tmp_path / "review.md"
    result, output = run_cli(tmp_path, docs, docs, human=human)
    first = output.read_bytes()
    report = json.loads(first)
    assert result.returncode == 2
    assert [r["document_id"] for r in report["results"]] == ["1", "2"]
    assert [r["action"] for r in report["results"]] == ["SKIP_VERIFIED", "UNKNOWN"]
    assert sum(report["summary"]["actions"].values()) == report["summary"]["total"] == 2
    assert output.stat().st_mode & 0o777 == human.stat().st_mode & 0o777 == 0o600
    assert "missing-private-token" not in result.stdout + result.stderr
    result, output = run_cli(tmp_path, docs, docs, human=human)
    assert result.returncode == 2
    assert output.read_bytes() == first


def test_nonregular_input_is_unknown_without_blocking(tmp_path):
    pipe = tmp_path / "raw.md"
    os.mkfifo(pipe)
    result, output = run_cli(tmp_path, [document(pipe)], [document(pipe)])
    assert result.returncode == 2
    report = json.loads(output.read_text())
    assert "INPUT_NOT_REGULAR_FILE" in report["results"][0]["reason_codes"]


def test_file_budget_limits_document_without_truncating_it_to_equal(tmp_path):
    raw = tmp_path / "raw.md"
    raw.write_bytes(b"123456789")
    result, output = run_cli(
        tmp_path, [document(raw)], [document(raw)], extra=("--max-file-bytes", "4")
    )
    assert result.returncode == 2
    report = json.loads(output.read_text())
    assert report["results"][0]["action"] == "UNKNOWN"
    assert "FILE_BUDGET_EXCEEDED" in report["results"][0]["reason_codes"]
    assert raw.read_bytes() == b"123456789"


def test_manifest_change_during_read_preserves_explicit_reason(tmp_path):
    manifest = tmp_path / "incoming.json"
    result, output = run_cli(tmp_path, [], [], hook=mutation_hook(manifest, manifest))
    assert result.returncode == 1
    assert result.stderr.strip() == "mdcompare: INPUT_CHANGED"
    assert result.stdout == ""
    assert not output.exists()
