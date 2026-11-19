"""Bounded Pi-OAuth assessment of an immutable authored-diff generation."""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from .io import InputError, publish_reports, validate_outputs
from .semantic_context import prepare
from .semantic_route import _digest, _generation, _identity

DEFAULT_MODEL = "openai/gpt-6.1-terra"
DEFAULT_EFFORT = "medium"
_DIR = Path(__file__).parent
_RESIDUE = re.compile(
    r"\[PaddleOCR image region: untranscribed\]|<!--\s*(?:mdocr:ocr-(?:begin|end)|这是一张图片[，,]\s*ocr\s*内容为[：:])"
    r"|(?<!\\)!\[[^\]\n]*\]\([^\n)]*\)|<img\b[^>]*>",
    re.IGNORECASE,
)
_BRIDGE = Path(__file__).with_name("pi_score.mjs")
_STOP = {
    "auth_error",
    "oauth_required",
    "sdk_unavailable",
    "proxy_unavailable",
    "bridge_error",
    "unsupported_model",
    "unsupported_transport",
    "unknown_outcome",
}


def score_eligible(row):
    return (
        row["semantic_status"] == "CHANGED"
        and row["action"] == "COMPILE"
        and row["comparison_complete"] is True
        and row["extraction"]["available"] is True
    )


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _packet(data):
    old, new = (data["source_inputs"][side] for side in ("baseline", "incoming"))
    if (
        old is None
        or new is None
        or old["content_kind"] not in {"raw", "historical_enriched"}
        or new["content_kind"] != "raw"
        or any(
            old["metadata"].get(field) != new["metadata"].get(field)
            for field in ("name", "logical_path")
        )
    ):
        return None, "SOURCE_NOT_SCOREABLE"
    expected = {
        "add": (False, True),
        "delete": (True, False),
        "modify": (True, True),
        "move": (True, True),
        "equal": (True, True),
    }
    used = {"old_ref": set(), "new_ref": set()}
    for row in data["changes"]:
        if row["operation"] == "uncertain":
            return None, "ALIGNMENT_UNCERTAIN"
        if tuple(row[f] is not None for f in used) != expected.get(row["operation"]):
            raise ValueError("operation has invalid sides")
        for field, side in (("old_ref", "old_units"), ("new_ref", "new_units")):
            ref = row[field]
            if ref is None:
                continue
            units = data[side]
            if (
                type(ref) is not int
                or not 0 <= ref < len(units)
                or ref in used[field]
                or units[ref].get("exporter_title")
            ):
                raise ValueError("invalid operation ownership")
            used[field].add(ref)
    packet = prepare(data)
    if not packet["operations"]:
        return None, "NO_SCOREABLE_OPERATIONS"
    for side, field in (("old_units", "old_ref"), ("new_units", "new_ref")):
        targets = {op[field] for op in packet["operations"] if op[field] is not None}
        for unit in packet[side]:
            unit["role"] = "delta_target" if unit["ref"] in targets else "context_only"
            if _RESIDUE.search(unit["text"]) or "\ufffd" in unit["text"]:
                return None, "UNSAFE_OR_INCOMPLETE_EVIDENCE"
    return _json(dict(packet, schema="authored-model-diff-1")), None


def _unique_fields(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate response field")
    return result


def _validate_reply(raw, count):
    value = json.loads(raw, object_pairs_hook=_unique_fields)
    if not isinstance(value, dict) or set(value) != {
        "evaluation_status",
        "change_score",
        "checked_operations",
        "changes",
        "reason",
        "missing_evidence",
    }:
        raise ValueError
    checked = value["checked_operations"]
    if (
        not isinstance(checked, list)
        or len(checked) != count
        or any(type(i) is not int for i in checked)
        or set(checked) != set(range(count))
        or not isinstance(value["reason"], str)
        or not value["reason"].strip()
    ):
        raise ValueError
    score, claims, missing = (
        value["change_score"],
        value["changes"],
        value["missing_evidence"],
    )
    if not isinstance(claims, list) or not isinstance(missing, list):
        raise TypeError
    if value["evaluation_status"] == "insufficient_input":
        if (
            score is not None
            or claims
            or not missing
            or any(not isinstance(m, str) or not m.strip() for m in missing)
        ):
            raise ValueError
        return {"ok": False, "reason": "insufficient_input"}
    if (
        value["evaluation_status"] != "evaluated"
        or type(score) is not int
        or not 1 <= score <= 5
        or missing
        or bool(claims) != (score >= 3)
    ):
        raise ValueError
    known = set()
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {
            "operation_id",
            "before",
            "after",
            "ambiguity",
        }:
            raise ValueError
        ref = claim["operation_id"]
        if type(ref) is not int or not 0 <= ref < count or ref in known:
            raise ValueError
        known.add(ref)
        if any(
            not isinstance(claim[key], str) or not claim[key].strip()
            for key in ("before", "after")
        ):
            raise ValueError
        if claim["ambiguity"] is not None and (
            not isinstance(claim["ambiguity"], str) or not claim["ambiguity"].strip()
        ):
            raise ValueError
    ambiguous = any(claim["ambiguity"] is not None for claim in claims)
    if (score == 3 and not ambiguous) or (
        score >= 4 and all(claim["ambiguity"] is not None for claim in claims)
    ):
        raise ValueError
    return {"ok": True, "score": score, "ambiguous": ambiguous}


def _bridge_request(request, *, timeout, pass_fds=()):
    diagnostic = request.get("op") == "doctor"
    try:
        proc = subprocess.run(
            ["node", str(_BRIDGE)],
            input=_json(request),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            pass_fds=pass_fds,
            env={
                k: v
                for k, v in os.environ.items()
                if k not in {"OPENAI_API_KEY", "CODEX_API_KEY"}
            },
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "reason": "node_unavailable" if diagnostic else "sdk_unavailable",
        }
    except OSError:
        return {"ok": False, "reason": "sdk_unavailable"}
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "reason": "bridge_timeout" if diagnostic else "unknown_outcome",
        }
    try:
        result = json.loads(proc.stdout)
        if not isinstance(result, dict) or proc.returncode:
            raise ValueError
        if result.get("ok") is not True and not isinstance(result.get("reason"), str):
            raise ValueError
        return result
    except ValueError:
        return {
            "ok": False,
            "reason": "invalid_response" if diagnostic else "unknown_outcome",
        }


def _assess(model, effort, evidence, prompt, lock_fd):
    result = _bridge_request(
        {"model": model, "effort": effort, "prompt": prompt, "evidence": evidence},
        timeout=180,
        # An orphaned bridge keeps the run locked until it exits.
        pass_fds=(lock_fd,),
    )
    if result.get("ok") is True:
        try:
            result.update(
                _validate_reply(
                    result["reply"], len(json.loads(evidence)["operations"])
                )
            )
        except (ValueError, KeyError, TypeError):
            result.update(ok=False, reason="invalid_response")
    return result


def _save(path, value):
    publish_reports([(path, _json(value) + "\n")])


def _save_journal(path, value):
    _save(path, value)
    # Renaming a synced file does not make the directory entry crash durable.
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _record_outcome(state, attempt, reply):
    if "reply" in reply:
        attempt["reply"] = reply["reply"]
    attempt["usage_tokens"] = reply.get("usage_tokens")
    if reply.get("ok") is True:
        attempt.update(
            status="scored", score=reply["score"], ambiguous=reply["ambiguous"]
        )
        return
    reason = reply["reason"]
    status = reply.get("http_status")
    attempt.update(status="unscored", reason=reason)
    if status is not None:
        attempt["http_status"] = status
    # Only the first 429 at a tier lowers it. Other requests from that tier drain.
    if status == 429 and attempt["concurrency"] == state["concurrency"]:
        if state["concurrency"] > 1:
            state["concurrency"] -= 1
        else:
            state["stop"] = state["stop"] or "rate_limited"
    elif reason in _STOP:
        state["stop"] = state["stop"] or reason


def _score_pending(args, prompt, directory, journal, state, candidates, lock_fd):
    attempts = state["attempts"]
    held = state["held"]
    known = {_identity(row) for row in attempts + held}
    active = {}

    def settle():
        done, _ = wait(active, return_when=FIRST_COMPLETED)
        for future in tuple(active):
            if future in done:
                _record_outcome(state, active.pop(future), future.result())
                _save_journal(journal, state)

    def await_capacity():
        while active and (
            state["stop"]
            or next(iter(active.values()))["concurrency"] > state["concurrency"]
            or len(active) >= state["concurrency"]
            or any(f.done() for f in active)
        ):
            settle()

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for key, binding in candidates.items():
            if key in known:
                continue
            if state["stop"] or len(attempts) >= args.request_budget:
                break
            data = (directory / binding["evidence"]).read_bytes()
            if _digest(data) != binding["evidence_sha256"]:
                raise InputError("SCORE_EVIDENCE_DRIFT")
            packet, hold = _packet(json.loads(data))
            if packet is None or len(packet.encode()) > args.max_evidence_bytes:
                held.append(dict(binding, reason=hold or "OVERSIZED_EVIDENCE"))
                continue
            await_capacity()
            if state["stop"]:
                break
            attempt = dict(
                binding,
                status="admitted",
                packet_sha256=_digest(packet.encode()),
                concurrency=state["concurrency"],
            )
            attempts.append(attempt)
            _save_journal(journal, state)
            future = pool.submit(
                _assess, args.model, args.effort, packet, prompt, lock_fd
            )
            active[future] = attempt
        while active:
            settle()

    recorded = {_identity(row): row for row in attempts + held}
    return [
        {k: v for k, v in recorded[key].items() if k != "reply"}
        if key in recorded
        else dict(binding, reason=state["stop"] or "REQUEST_BUDGET_EXHAUSTED")
        for key, binding in candidates.items()
    ]


def publish_semantic_scores(args):
    output = args.output.resolve()
    journal = output.with_name(output.name + ".execution.json")
    lock_path = output.with_name(output.name + ".lock")
    inputs = {args.report, args.diff_index}
    try:
        report_bytes, index_bytes = (
            args.report.read_bytes(),
            args.diff_index.read_bytes(),
        )
        report, index = json.loads(report_bytes), json.loads(index_bytes)
        if (
            report["schema_version"] != "semantic-1.0"
            or report["scope"] != "authored_body"
            or index["schema_version"] != "mdcompare-semantic-diff-1"
            or index["report_sha256"] != _digest(report_bytes)
        ):
            raise ValueError("invalid authored diff generation")
        directory = args.diff_index.resolve().parent
        bindings = _generation(report, index, directory, inputs)
        validate_outputs([output, journal, lock_path], inputs)
    except (KeyError, TypeError, ValueError, OSError, AttributeError) as exc:
        raise InputError("SCORE_BINDING_INVALID") from exc
    prompt = (_DIR / "semantic_score_prompt.txt").read_text(encoding="utf-8")
    config = {
        "report_sha256": _digest(report_bytes),
        "index_sha256": _digest(index_bytes),
        "model": args.model,
        "effort": args.effort,
        "request_budget": args.request_budget,
        "concurrency": args.concurrency,
        "max_evidence_bytes": args.max_evidence_bytes,
        "prompt_sha256": _digest(prompt.encode()),
        "format_sha256": _digest((_DIR / "semantic_score_format.json").read_bytes()),
    }
    candidates = {}
    for row in report["results"]:
        if score_eligible(row):
            key = _identity(row)
            candidates[key] = {
                "knowledge_base": key[0],
                "document_id": key[1],
                "evidence": bindings[key][0],
                "evidence_sha256": bindings[key][1],
            }
    if args.document_id is not None:
        selected = set(args.document_id)
        candidates = {
            key: binding for key, binding in candidates.items() if key[1] in selected
        }
        if (
            len(selected) != len(args.document_id)
            or len(candidates) != len(selected)
            or {key[1] for key in candidates} != selected
        ):
            raise InputError("SCORE_DOCUMENT_SCOPE_INVALID")
        config["document_ids"] = sorted(selected)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if journal.exists():
            state = json.loads(journal.read_bytes())
            if state["configuration"] != config:
                raise InputError("SCORE_EXECUTION_BINDING_INVALID")
        else:
            if os.fstat(lock.fileno()).st_size:
                raise InputError("SCORE_EXECUTION_JOURNAL_MISSING")
            if output.exists():
                raise InputError("SCORE_OUTPUT_UNOWNED")
            state = {
                "configuration": config,
                "attempts": [],
                "held": [],
                "stop": None,
                "concurrency": args.concurrency,
            }
        # Keep the lock inode as a durable witness against a lost journal.
        # The first journal save syncs the directory before any request.
        if not os.fstat(lock.fileno()).st_size:
            lock.write(b"mdcompare-semantic-score\n")
            lock.flush()
            os.fsync(lock.fileno())
        attempts = state["attempts"]
        for previous in attempts:
            if previous["status"] == "admitted":
                previous.update(status="unscored", reason="unknown_outcome")
        public = _score_pending(
            args, prompt, directory, journal, state, candidates, lock.fileno()
        )
        scored = [a for a in public if a.get("status") == "scored"]
        unscored = [a for a in public if a.get("status") != "scored"]
        _save_journal(journal, state)
        _save(
            output,
            {
                "schema_version": "mdcompare-semantic-scores-1",
                "diff_index": os.path.relpath(args.diff_index.resolve(), output.parent),
                "scores": scored,
                "unscored": unscored,
                "execution": {
                    "configuration": config,
                    "requests": len(attempts),
                    "concurrency": state["concurrency"],
                    "stop": state["stop"],
                },
            },
        )
    return 2 if unscored else 0
