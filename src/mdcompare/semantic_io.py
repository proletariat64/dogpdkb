"""Bounded-memory publication of full per-document semantic evidence."""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from .io import (
    InputError,
    InputReader,
    publish_reports,
    referenced_paths,
    validate_outputs,
)
from .manifest import load_manifest
from .model import CompareOptions
from .semantic import compare_semantic, load_recovery


def publish_semantic(args):
    options = CompareOptions(
        max_file_bytes=args.max_file_bytes, max_diff_work=args.max_diff_work
    )
    reader = InputReader(options.max_file_bytes)
    baseline = load_manifest(args.baseline, reader, body_only=True)
    incoming = load_manifest(args.incoming, reader, body_only=True)
    recovery, recovery_paths = load_recovery(args.recovery, reader)
    protected = reader.paths | {args.baseline, args.incoming, *recovery_paths}
    for document in baseline + incoming:
        protected.update(referenced_paths(document))
    validate_outputs([args.output, args.diff_output], protected)
    sources = {
        "baseline_manifest": str(args.baseline.absolute()),
        "incoming_manifest": str(args.incoming.absolute()),
    }
    if args.recovery:
        sources["recovery_manifest"] = str(args.recovery.absolute())
    # A unique generation keeps previously published indexes valid until the new
    # pair is committed. Never replace an evidence file underneath an old index.
    directory = Path(
        tempfile.mkdtemp(
            prefix=args.diff_output.stem + "-evidence-", dir=args.diff_output.parent
        )
    )
    count = 0

    def emit(evidence):
        nonlocal count
        path = directory / f"{count:06d}.json"
        count += 1
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        data = (json.dumps(evidence, ensure_ascii=False) + "\n").encode()
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        return {
            "knowledge_base": evidence["knowledge_base"],
            "document_id": evidence["document_id"],
            "evidence": str(path.relative_to(args.diff_output.parent)),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    try:
        report, diff = compare_semantic(
            baseline,
            incoming,
            options,
            reader,
            sources,
            recovery,
            args.recovery,
            diff_sink=emit,
        )
        if reader.changed_paths():
            raise InputError("INPUT_CHANGED")
        diff["evidence_directory"] = directory.name
        report_bytes = (
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        ).encode()
        diff["report_sha256"] = hashlib.sha256(report_bytes).hexdigest()
        publish_reports(
            [
                (args.output, report_bytes.decode()),
                (
                    args.diff_output,
                    json.dumps(diff, ensure_ascii=False, indent=2) + "\n",
                ),
            ]
        )
    except BaseException:
        shutil.rmtree(directory)
        raise
    return report
