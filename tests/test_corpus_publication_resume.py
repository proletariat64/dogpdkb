"""Real CLI recovery of interrupted corpus publication without tool re-entry."""

import json
from pathlib import Path

import pytest

from dogpdkb import apply
from dogpdkb.records import Store
from tests.source_fixture import team_id
from tests.test_orchestrator_apply_cli import VERSION, cli, instance, two_sources


def setup_plan(root):
    config, differences = instance(root)
    two_sources(root, differences)
    corpus = root / "corpus"
    corpus.mkdir()
    (corpus / "First-2026-11-10-[42].md").write_text("Old first\n")
    suffix = "-2026-11-10-[43].md"
    name = "知" * 70 + "A" * (229 - 210 - len(suffix)) + suffix
    (corpus / name).write_text("Old second\n")
    result = cli("init-state", "--config", config)
    assert result.returncode == 0, result.stderr
    result = cli(
        "apply", "--config", config, "--input", differences, "--stop-after", "plan"
    )
    assert result.returncode == 0, result.stderr
    run_path = Path(json.loads(result.stdout)["run"])
    return config, run_path, corpus / name


@pytest.mark.parametrize("save_facts", [True, False])
def test_resume_after_replacement_without_repeating_tools(
    tmp_path, monkeypatch, save_facts
):
    config, run_path, long_output = setup_plan(tmp_path)
    replace, save = Path.replace, Store.save

    def fail_second(path, target):
        if Path(target) == long_output:
            raise OSError("injected interruption after first replacement")
        return replace(path, target)

    def fail_save(store, *, durable=False):
        if (
            not save_facts
            and store.document(team_id("42")).get("corpus", {}).get("version")
            == VERSION
        ):
            raise OSError("injected state save interruption")
        return save(store, durable=durable)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_second)
        patch.setattr(Store, "save", fail_save)
        assert apply.main(["--config", str(config), "--resume", str(run_path)]) == 1
    run = json.loads(run_path.read_text())
    assert "publication" in run["artifacts"]
    assert "manifest" not in run["artifacts"]
    assert (tmp_path / "corpus/First-2026-11-10-[42].md").read_text() != "Old first\n"
    assert long_output.read_text() == "Old second\n"
    events = (tmp_path / "events").read_bytes()

    # Any OCR re-entry fails this test even when it could reuse prior outputs.
    real_ocr = apply.ocr_inputs
    with monkeypatch.context() as patch:
        patch.setattr(apply, "ocr_inputs", lambda *args: pytest.fail("OCR re-entered"))
        assert apply.main(["--config", str(config), "--resume", str(run_path)]) == 0
    assert apply.ocr_inputs is real_ocr
    assert (tmp_path / "events").read_bytes() == events
    assert "Second" in long_output.read_text()
    assert len(long_output.name.encode()) == 229
    assert not list(long_output.parent.glob(".dogpdkb-*.tmp"))
    manifest = json.loads(
        Path(json.loads(run_path.read_text())["artifacts"]["manifest"]).read_text()
    )
    assert len(manifest["documents"]) == 2
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    assert all(
        state["documents"][team_id(key)]["corpus"]["version"] == VERSION
        for key in ("42", "43")
    )


@pytest.mark.parametrize("mutation", ["external", "symlink", "newer"])
def test_resume_protects_targets_and_versions(tmp_path, monkeypatch, mutation):
    config, run_path, long_output = setup_plan(tmp_path)
    replace = Path.replace

    def fail_second(path, target):
        if Path(target) == long_output:
            raise OSError("stop publication")
        return replace(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_second)
        assert apply.main(["--config", str(config), "--resume", str(run_path)]) == 1
    if mutation == "external":
        long_output.write_text("External change\n")
    elif mutation == "symlink":
        target = tmp_path / "outside.md"
        target.write_text("Protected\n")
        long_output.unlink()
        long_output.symlink_to(target)
    else:
        state_path = tmp_path / "state/funds/state.json"
        state = json.loads(state_path.read_text())
        state["documents"][team_id("43")]["corpus"]["version"] = {
            "value": "2026-11-14",
            "precision": "day",
        }
        state_path.write_text(json.dumps(state))
    expected = long_output.read_bytes()
    result = cli("apply", "--config", config, "--resume", run_path)
    assert result.returncode == 1, result.stderr
    assert long_output.read_bytes() == expected
    assert "generate a new plan" in result.stderr or "symlink" in result.stderr


def test_long_final_filename_succeeds_in_public_cli(tmp_path):
    config, run_path, output = setup_plan(tmp_path)
    result = cli("apply", "--config", config, "--resume", run_path)
    assert result.returncode == 0, result.stderr
    assert "Second" in output.read_text()


def test_final_partial_manifest_resume_does_not_retry_ocr(tmp_path, monkeypatch):
    config, run_path, _ = setup_plan(tmp_path)
    result = cli("apply", "--config", config, "--resume", run_path)
    assert result.returncode == 0, result.stderr
    run = json.loads(run_path.read_text())
    path = Path(run["artifacts"]["manifest"])
    manifest = json.loads(path.read_text())
    manifest["documents"][0]["pending_steps"] = ["ocr"]
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(
        apply, "ocr_inputs", lambda *args: pytest.fail("OCR budget restarted")
    )
    assert apply.main(["--config", str(config), "--resume", str(run_path)]) == 0
