"""Commit and verify the immutable artifacts at sync stage boundaries."""

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


def graph_generation(store, book):
    graph = store.settings.graph_for(book)
    # Native publication exchanges the entire directory. Its identity also
    # detects a new cache/report generation with identical graph/manifest bytes.
    generation = graph.stat() if graph.is_dir() else None
    return {
        "directory": [generation.st_dev, generation.st_ino] if generation else None,
        **{
            name: file_digest(graph / name) if (graph / name).is_file() else None
            for name in ("graph.json", "manifest.json")
        },
    }


def pause(run, phase, stop_after, *, manifest=None):
    if phase != stop_after:
        return None
    run.stage(phase)
    run.finish("paused")
    return {"exit_code": 0, "paused": True, "manifest": manifest}
