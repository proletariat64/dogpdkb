"""Commit and verify the immutable artifacts at sync stage boundaries."""

import hashlib
from pathlib import Path

from dogpdkb.ocr_batch import file_digest
from dogpdkb.records import read_json

SYNC_PHASES = ("collect", "plan", "ocr", "corpus", "graph")


def seal_phase(run, phase, *artifacts):
    checkpoints = run.data.setdefault("checkpoints", {})
    sealed = {key: file_digest(Path(run.data["artifacts"][key])) for key in artifacts}
    if phase in checkpoints and checkpoints[phase] != sealed:
        raise ValueError(f"{phase} checkpoint changed; generate a new plan")
    checkpoints[phase] = sealed
    run.data["completed_phase"] = phase
    run.save(durable=True)


def read_checkpoint(run, phase, key):
    path = Path(run.data["artifacts"][key])
    expected = run.data.get("checkpoints", {}).get(phase, {}).get(key)
    if expected is not None and file_digest(path) != expected:
        raise ValueError(f"{phase} checkpoint changed; generate a new plan")
    return read_json(path)


def bind_graph_manifest(run, path, manifest):
    """Freeze compile rows authorized for explicit graph-only recovery."""
    run.data["graph_retry"] = {
        "manifest": str(path),
        "sha256": file_digest(Path(path)),
        "documents": {
            row["document_id"]: {
                "version": row["version"],
                "action": row["action"],
                "input_sha256": row.get("input_copy_sha256"),
                "input_copy": row.get("input_copy"),
            }
            for row in manifest["documents"]
        },
    }


def check_graph_manifest(run, path, *, content=None):
    bound = run.data.get("graph_retry")
    if bound is None:
        return
    digest = (
        file_digest(Path(path))
        if content is None
        else hashlib.sha256(content).hexdigest()
    )
    if str(Path(path).resolve()) != bound["manifest"] or digest != bound["sha256"]:
        raise ValueError("saved graph manifest changed; generate a new plan")


def graph_generation(store, book):
    graph = store.settings.graph_for(book)
    # Keep the saved handoff identity as well as current native output bytes.
    generation = graph.stat() if graph.is_dir() else None
    return {
        "writer": store.data.get("graph_writer"),
        "directory": [generation.st_dev, generation.st_ino] if generation else None,
        **{
            name: file_digest(graph / name) if (graph / name).is_file() else None
            for name in ("graph.json", "manifest.json")
        },
    }


def graph_handoff_matches(store, book, run):
    # A different run's dispatch makes this handoff stale, even if final
    # native bytes are identical. Own writes do not invalidate an explicit retry.
    return store.data.get("graph_writer") == run.id or run.data[
        "graph_generation"
    ] == graph_generation(store, book)


def pause(run, phase, stop_after, *, manifest=None):
    if phase != stop_after:
        return None
    if run.data.get("stage") != phase:
        run.stage(phase)
    run.finish("paused")
    return {"exit_code": 0, "paused": True, "manifest": manifest}
