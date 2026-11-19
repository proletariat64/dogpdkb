"""Own directory Markdown and catalogued local resources as one frozen input."""

import hashlib
import json
from os.path import normpath
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dogpdkb.records import version_key


def _digest(body):
    return hashlib.sha256(body).hexdigest()


def snapshot_version(body_digest, resources):
    if resources:
        witnesses = sorted(
            (r["resource_id"], r["relative_path"], r["sha256"]) for r in resources
        )
        encoded = json.dumps([body_digest, witnesses], ensure_ascii=True).encode(
            "ascii"
        )
        body_digest = _digest(encoded)
    return {"value": f"sha256:{body_digest}", "precision": "content"}


def _input_file(root, relative):
    path = Path(relative)
    if (
        path.is_absolute()
        or not path.parts
        or any(
            part in {"..", "_inventory"} or part.startswith(".") for part in path.parts
        )
    ):
        raise ValueError(f"invalid directory input path: {relative}")
    candidate = root / path
    if candidate.resolve() != candidate or not candidate.is_file():
        raise ValueError(
            f"directory input is missing or not a regular non-symlink file: {relative}"
        )
    return candidate


def observe_resources(root, document_path, resources):
    """Bind only catalog-resolved files inside this configured directory."""
    observed = []
    seen = set()
    for resource in resources:
        if not isinstance(resource, dict) or any(
            not isinstance(resource.get(key), str) or not resource[key]
            for key in ("resource_id", "url", "kind")
        ):
            raise ValueError("invalid directory resource catalog entry")
        if resource["resource_id"] in seen:
            raise ValueError("duplicate directory resource catalog ID")
        seen.add(resource["resource_id"])
        if "path" not in resource:
            continue
        if not isinstance(resource["path"], str):
            raise TypeError("invalid directory resource catalog path")
        url = urlsplit(resource["url"])
        if url.scheme or url.netloc:
            raise ValueError(
                f"unexpected catalog path for remote resource: {resource['url']}"
            )
        target = Path(resource["path"])
        if not target.is_relative_to(root):
            raise ValueError(
                f"resource is outside its directory source: {resource['url']}"
            )
        relative = target.relative_to(root).as_posix()
        candidate = _input_file(root, relative)
        lexical = document_path.parent / unquote(url.path)
        # A relative reference must resolve in the same copied layout. Do not
        # follow a symlink alias, even if it points to a file inside the root.
        if Path(normpath(lexical)) != candidate:
            raise ValueError(f"resource points through a symlink: {resource['url']}")
        observed.append(
            {
                **{key: resource[key] for key in ("resource_id", "url", "kind")},
                "relative_path": relative,
                "sha256": _digest(candidate.read_bytes()),
            }
        )
    return observed


def copy_snapshot(source_root, target_root, row):
    """Verify and copy the exact inputs; never fall back to live files on resume."""
    files = {row["source_document_id"]: row["directory_body_sha256"]}
    for resource in row["directory_resources"]:
        relative = resource["relative_path"]
        if files.setdefault(relative, resource["sha256"]) != resource["sha256"]:
            raise ValueError(f"contradictory directory input digest: {relative}")
    for relative, digest in files.items():
        body = _input_file(source_root, relative).read_bytes()
        if _digest(body) != digest:
            raise ValueError(f"frozen directory input changed: {relative}")
        target = target_root / relative
        if target.resolve() != target:
            raise ValueError(f"refusing to write a symlink snapshot path: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    return target_root / row["source_document_id"]


def validate_snapshot(root, row, body):
    body_digest = _digest(body)
    if body_digest != row["directory_body_sha256"] or (
        version_key(snapshot_version(body_digest, row["directory_resources"]))
        != version_key(row["version"])
    ):
        raise ValueError("directory content version changed")
    for resource in row["directory_resources"]:
        path = _input_file(root, resource["relative_path"])
        if _digest(path.read_bytes()) != resource["sha256"]:
            raise ValueError(
                f"frozen directory resource changed: {resource['relative_path']}"
            )


def catalog_paths(root, row):
    return [
        {key: resource[key] for key in ("resource_id", "kind", "url")}
        | {"path": str(root / resource["relative_path"])}
        for resource in row["directory_resources"]
    ]
