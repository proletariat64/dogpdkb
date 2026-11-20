"""Durably admit jobs, settle exact evidence, and publish complete OCR Markdown."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import quote

import requests

from . import __version__
from .llm import IMAGE_SCHEMA, LlmClient, LlmError
from .markdown import IMAGE_RE, parse_image_match
from .paddle import _MODEL, OcrRequestOutcome, PaddleClient
from .plan import read_plan
from .prepare import JPEG_QUALITY, ImageError, normalize_image


class NotCompleted(RuntimeError):
    """A classified provider or image outcome; never includes provider text."""

    def __init__(self, reason, status=None, unknown=False):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.unknown = unknown


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(value) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def _atomic(path, data, *, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".mdocr-run-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(name, path)
        else:
            os.replace(name, path)
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        Path(name).unlink(missing_ok=True)


def _block(image, mode, patch):
    original = quote(image["original_link"], safe="/:%?&=+@#;,.~!$'*-_")
    uncertainty = patch.get("uncertainty", "").strip()
    body = patch["markdown"].strip()
    if uncertainty:
        body += "\n\n" + "\n".join(f"> {line}" for line in uncertainty.splitlines())
    return (
        f"<!-- mdocr:ocr-begin v=2 id={image['id']} -->\n"
        f"<!-- mdocr:meta mode={mode} sha256={image['sha256']} -->\n"
        f"> Original image: [source](<{original}>)\n\n"
        f"{body}\n<!-- mdocr:ocr-end -->"
    )


def _patches(value, count):
    if not isinstance(value, list) or len(value) != count:
        raise NotCompleted("OCR_RESPONSE_INVALID")
    for patch in value:
        if not isinstance(patch, dict):
            raise NotCompleted("OCR_RESPONSE_INVALID")
        keys = ("markdown", "uncertainty")
        if any(not isinstance(patch.get(k), str) for k in keys):
            raise NotCompleted("OCR_RESPONSE_INVALID")
        if any(re.search(r"<!--\s*mdocr:", patch[k], re.IGNORECASE) for k in keys):
            raise NotCompleted("OCR_DELIMITER_RESERVED")
        if "warnings" in patch and patch["warnings"] != [
            "response_mermaid_syntax_invalid"
        ]:
            raise NotCompleted("OCR_RESPONSE_INVALID")
        if patch.get("kind") == "hold":
            if patch["markdown"] or not patch["uncertainty"].strip():
                raise NotCompleted("OCR_RESPONSE_INVALID")
        elif not patch["markdown"].strip():
            raise NotCompleted("OCR_EMPTY")
    return value


def _text(path, digest, reason):
    data = Path(path).read_bytes()
    if _sha(data) != digest:
        raise ValueError(reason)
    return data


def _validate(plan, plan_path, result_path, ledger, artifacts, lock_paths):
    settings, docs = plan["settings"], plan["documents"]
    if (
        settings["mode"] not in {"paddle", "llm"}
        or type(settings["concurrency"]) is not int
        or not 1 <= settings["concurrency"] <= 10
    ):
        raise ValueError("invalid plan settings")
    if settings["mode"] == "llm" and settings.get("llm_schema") != IMAGE_SCHEMA:
        # A legacy document-level admission cannot authorize per-image retries.
        raise ValueError("unsupported LLM plan schema; legacy plans cannot be executed")
    fix = settings.get("fix", 0)
    if type(fix) is not int or fix < 0:
        raise ValueError("invalid batch fix limit")
    rounds = settings.get("mermaid_repair_rounds", 0)
    if type(rounds) is not int or rounds < 0 or (rounds and settings["mode"] != "llm"):
        raise ValueError("invalid Mermaid repair rounds")
    for budget in (settings["token_budget"], settings["request_budget"]):
        if budget is not None and (type(budget) is not int or budget < 0):
            raise ValueError("invalid plan budget")
    if not 0 <= settings["max_seconds"] < float("inf"):
        raise ValueError("invalid plan deadline")
    controls = {
        path.resolve()
        for path in (
            plan_path,
            result_path,
            ledger,
            *lock_paths,
        )
    }
    if len(controls) != 5:
        raise ValueError("overlapping execution artifacts")
    sources, outputs, images = set(), set(), set()
    texts = []
    for doc in docs:
        source, output = (
            Path(doc["path"]).resolve(strict=True),
            Path(doc["output"]).resolve(),
        )
        if source in sources or output in outputs or doc["document_id"] != str(source):
            raise ValueError("duplicate or invalid planned input/output identity")
        sources.add(source)
        outputs.add(output)
        text = _text(source, doc["sha256"], "SOURCE_DRIFT").decode()
        texts.append(text)
        end, seen = 0, set()
        for image in doc["images"]:
            start, stop, key = image["start"], image["end"], image["id"]
            if (
                not isinstance(key, str)
                or not re.fullmatch(r"[a-zA-Z0-9-]+", key)
                or key in seen
                or not end <= start < stop <= len(text)
            ):
                raise ValueError("invalid planned image coordinates")
            seen.add(key)
            end = stop
            match = IMAGE_RE.fullmatch(text[start:stop])
            if match is None or parse_image_match(match).target != image["resource_id"]:
                raise ValueError("image occurrence no longer agrees with plan")
            original = image["original_link"]
            if (
                (
                    not isinstance(original, str)
                    or not original
                    or not original.startswith(("/", "http://", "https://"))
                )
                and image["selected"]
                and not image["error"]
            ):
                raise ValueError("invalid original image destination")
            if image["source_path"] is not None:
                path = Path(image["source_path"]).resolve()
                images.add(path)
                if image["selected"] and not image["error"]:
                    _text(path, image["sha256"], "IMAGE_DRIFT")
    protected = (
        sources | images | {Path(x).resolve() for x in plan.get("protected_inputs", [])}
    )
    if controls & (protected | outputs) or outputs & protected:
        raise ValueError(
            "output, result, plan, or lock overlaps another input/destination"
        )
    if any(
        x == artifacts or artifacts in x.parents for x in protected | outputs | controls
    ):
        raise ValueError("planned path overlaps the execution artifact directory")
    if settings["cache_dir"]:
        cache = Path(settings["cache_dir"]).resolve()
        if any(
            x == cache or cache in x.parents for x in protected | outputs | controls
        ):
            raise ValueError("planned path overlaps the OCR cache")
    return texts


# Only known unsuccessful provider attempts where submitting the same image
# can help. A typed connection interruption is retried separately below.
_FIXABLE_FAILURES = {
    "OCR_RATE_LIMITED",
    "rate_limited",
    "response_incomplete",
    "response_json_invalid",
    "response_mermaid_fence_invalid",
    "response_output_limit",
    "response_schema_invalid",
    "response_table_invalid",
    "response_unexpected_stop",
}


class Execution:
    def __init__(self, plan, digest, ledger, result, artifacts, retry):
        self.docs = plan["documents"]
        self.settings = plan["settings"]
        self.mode = self.settings["mode"]
        self.repair_rounds = self.settings.get("mermaid_repair_rounds", 0)
        self.repairs = {}
        self.repair_attempts = {}
        self.fix_limit = self.settings.get("fix", 0)
        self.fixes = {}
        self.result, self.ledger, self.artifacts = result, ledger, artifacts
        self.retry = retry
        self.deadline = time.monotonic() + self.settings["max_seconds"]
        self.limit = self.settings["concurrency"]
        self.config = {"mode": self.mode, "version": __version__}
        if self.mode == "llm":
            self.config |= {
                "model": self.settings["model"],
                "effort": self.settings["effort"],
                "prompt_sha256": _sha(self.settings["prompt"].encode()),
                "schema": IMAGE_SCHEMA,
            }
        else:
            self.config |= {
                "model": _MODEL,
                "jpeg_quality": JPEG_QUALITY,
                "endpoint": self.settings["endpoint"],
            }
        self.cache_config = self.config
        if self.repair_rounds:
            self.cache_config = self.config | {
                "mermaid_repair_rounds": self.repair_rounds
            }
        self.client = None
        self.causes = []
        self.stop = None
        self.cache = (
            Path(self.settings["cache_dir"]) if self.settings["cache_dir"] else None
        )
        if (
            result.exists()
            and json.loads(result.read_bytes()).get("plan_sha256") != digest
        ):
            raise ValueError("result is owned by another plan")
        if ledger.exists():
            self.state = json.loads(ledger.read_bytes())
            if (self.state.get("plan_sha256"), self.state.get("result_path")) != (
                digest,
                str(result),
            ):
                raise ValueError(
                    "execution is bound to a different plan or result path"
                )
        else:
            if result.exists() or any(Path(d["output"]).exists() for d in self.docs):
                raise ValueError(
                    "preexisting unowned execution result or document output"
                )
            self.state = {
                "schema_version": "mdocr-result-2",
                "plan_sha256": digest,
                "result_path": str(result),
                "status": "running",
                "causes": [],
                "concurrency": {
                    "initial": self.limit,
                    "final": self.limit,
                    "transitions": [],
                },
                "budget": {"requests": 0, "tokens": 0, "reserved_tokens": 0},
                "requests": [],
                "documents": [],
            }
        self.limit = self.state["concurrency"]["final"]
        self.latest = {}
        for previous in self.state["requests"]:
            if "fix_round" in previous:
                self.fixes[previous["key"]] = previous["fix_round"]
            if previous["status"] == "admitted":
                previous.update(status="unknown", reason="OCR_OUTCOME_UNKNOWN")
            if "repair_round" in previous:
                self.repair_attempts[previous["key"]] = previous
                if "patches" not in previous or previous.get("warnings"):
                    continue
            self.latest[previous["key"]] = previous
        self.reused = self.state.setdefault("reused", {})
        self.latest.update(self.reused)
        self.budget = self.state["budget"]
        self.state["status"] = "running"

    def save(self):
        data = _json(self.state)
        _atomic(self.ledger, data)
        _atomic(self.result, data)

    def plan_jobs(self):
        jobs, layout, pipelines = {}, [], []
        for doc in self.docs:
            selected = [
                im for im in doc["images"] if im["selected"] and not im["error"]
            ]
            bindings = {}
            for image in selected:
                key = _sha(_json([self.cache_config, image["sha256"]]))
                jobs.setdefault(key, image)
                bindings[image["id"]] = key
            context = None
            if self.mode == "llm" and selected:
                # Document evidence only: never sent to the engine or used as a cache key.
                context = _sha(
                    _json(
                        [
                            doc["sha256"],
                            [(im["id"], im["sha256"]) for im in selected],
                        ]
                    )
                )
                pipelines.append(list(bindings.values()))
            elif self.mode == "paddle":
                pipelines.extend([key] for key in bindings.values())
            layout.append((bindings, context))
        if (
            not self.latest.keys() <= jobs.keys()
            or not self.repair_attempts.keys() <= jobs.keys()
        ):
            raise ValueError(
                "saved OCR request identities do not match this runtime; no requests sent"
            )
        return jobs, pipelines, layout

    def cached(self, key):
        if self.cache is None:
            return None
        path = self.cache / f"{key}.json"
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_bytes())
            patches = record["patches"]
            if record["key"] != key or record["sha256"] != _sha(_json(patches)):
                return None
            return _patches(patches, 1)
        except (KeyError, TypeError, ValueError, NotCompleted):
            return None

    def reserve(self):
        if self.stop:
            return None
        if time.monotonic() >= self.deadline:
            self.stop = "BATCH_DEADLINE_EXCEEDED"
            return None
        requests_limit = self.settings["request_budget"]
        if requests_limit is not None and self.budget["requests"] >= requests_limit:
            self.stop = "OCR_REQUEST_BUDGET_EXHAUSTED"
            return None
        token_limit = self.settings["token_budget"]
        if token_limit is None:
            return 0
        client = self.client
        if not isinstance(client, LlmClient):
            raise TypeError("only LLM supports token reservations")
        bound = client.token_upper_bound()
        if type(bound) is not int or bound <= 0:
            self.stop = "OCR_TOKEN_BOUND_UNAVAILABLE"
        elif (
            self.budget["tokens"] + self.budget["reserved_tokens"] + bound > token_limit
        ):
            self.stop = "OCR_TOKEN_BUDGET_EXHAUSTED"
        else:
            return bound
        return None

    def convert(self, image, repair=None):
        client = self.client
        try:
            data = _text(image["source_path"], image["sha256"], "IMAGE_DRIFT")
        except (OSError, ValueError) as exc:
            raise NotCompleted("IMAGE_DRIFT") from exc
        if self.mode == "llm":
            if not isinstance(client, LlmClient):
                raise TypeError("LLM job admitted before preflight")
            result = client.convert(
                data,
                self.settings["prompt"],
                mermaid_diagnostics=bool(self.repair_rounds),
                repair=repair,
            )
            usage = result["usage_tokens"]
            try:
                patches = _patches([result["image"]], 1)
                errors = result.get("mermaid_errors", [])
                if self.repair_rounds and bool(errors) != bool(
                    patches[0].get("warnings")
                ):
                    raise NotCompleted("OCR_RESPONSE_INVALID")
                return patches, usage, errors
            except NotCompleted as exc:
                raise LlmError(
                    exc.reason, reason=exc.reason, usage_tokens=usage
                ) from exc
        if not isinstance(client, str):
            raise TypeError("Paddle job admitted without a token")
        try:
            jpeg = normalize_image(data)
        except ImageError as exc:
            raise NotCompleted("IMAGE_PROCESSING_FAILED") from exc
        with (
            tempfile.TemporaryDirectory(prefix="mdocr-image-") as tmp,
            requests.Session() as session,
        ):
            path = Path(tmp) / "prepared.jpg"
            path.write_bytes(jpeg)
            paddle = PaddleClient(
                client,
                session=session,
                endpoint=self.settings["endpoint"],
                adaptive=True,
            )
            try:
                text = paddle.convert(path)
            except OcrRequestOutcome as exc:
                reason = {
                    401: "OCR_AUTH_UNAVAILABLE",
                    403: "OCR_QUOTA_EXHAUSTED",
                    429: "OCR_RATE_LIMITED",
                }.get(exc.status or 0, "OCR_PROVIDER_FAILED")
                raise NotCompleted(
                    reason, status=exc.status, unknown=exc.unknown
                ) from exc
            except (OSError, ValueError, RuntimeError) as exc:
                raise NotCompleted("OCR_OUTCOME_UNKNOWN", unknown=True) from exc
        return _patches([{"markdown": text, "uncertainty": ""}], 1), None, []

    def settle(self, active):
        done, _ = wait(active, return_when=FIRST_COMPLETED)
        for future in tuple(active):
            if future not in done:
                continue
            key, attempt, admitted = active.pop(future)
            reservation = attempt["reserved_tokens"]
            status, unknown, usage = None, False, None
            try:
                patches, usage, errors = future.result()
            except (LlmError, NotCompleted) as exc:
                status = exc.status
                unknown = getattr(exc, "unknown", False) or exc.reason in {
                    "unknown_outcome",
                    "connection_outcome_unknown",
                }
                usage = getattr(exc, "usage_tokens", None)
                attempt.update(
                    status="unknown" if unknown else "failed", reason=exc.reason
                )
            else:
                attempt.update(status="success", patches=patches)
                if "repair_round" not in attempt or not errors:
                    self.latest[key] = attempt
                if errors:
                    attempt["warnings"] = ["response_mermaid_syntax_invalid"]
                    self.repairs[key] = {
                        k: patches[0][k] for k in ("kind", "markdown", "uncertainty")
                    } | {"errors": errors}
                if (
                    self.cache is not None
                    and not errors
                    and not any(p.get("kind") == "hold" for p in patches)
                ):
                    _atomic(
                        self.cache / f"{key}.json",
                        _json(
                            {
                                "key": key,
                                "patches": patches,
                                "sha256": _sha(_json(patches)),
                            }
                        ),
                    )
            if usage is not None:
                if type(usage) is not int or usage < 0:
                    raise ValueError("invalid provider token usage")
                self.budget["tokens"] += usage
                self.budget["reserved_tokens"] -= reservation
                attempt["usage_tokens"] = usage
                if self.settings["token_budget"] is not None and usage > reservation:
                    self.stop = "OCR_TOKEN_BOUND_BREACH"
            elif status in {401, 403, 429} and not unknown:
                self.budget["reserved_tokens"] -= reservation
            if status in {401, 403} and not self.stop:
                self.stop = (
                    "OCR_AUTH_UNAVAILABLE" if status == 401 else "OCR_QUOTA_EXHAUSTED"
                )
            if status == 429 and admitted == self.limit and not self.stop:
                if self.limit == 1:
                    self.stop = "OCR_RATE_LIMITED"
                else:
                    self.limit -= 1
                    self.state["concurrency"]["final"] = self.limit
                    self.state["concurrency"]["transitions"].append(
                        {"from": admitted, "to": self.limit}
                    )
            self.save()

    def admit(self, pool, active, key, image, *, repair=None, fix=False):
        if self.client is None:
            # Zero new requests is evaluated before checking credentials.
            if self.settings["request_budget"] == 0:
                self.stop = "OCR_REQUEST_BUDGET_EXHAUSTED"
                return
            if self.mode == "paddle":
                self.client = os.environ.get("PADDLEOCR_TOKEN")
                if not self.client:
                    self.stop = "OCR_AUTH_UNAVAILABLE"
                    return
            else:
                if self.settings["token_budget"] == 0:
                    self.stop = "OCR_TOKEN_BUDGET_EXHAUSTED"
                    return
                self.client = LlmClient(self.settings["model"], self.settings["effort"])
                try:
                    self.client.preflight()
                except LlmError as exc:
                    self.stop = exc.reason
                    return
        reservation = self.reserve()
        if reservation is None:
            return
        attempt = {"key": key, "status": "admitted", "reserved_tokens": reservation}
        if repair is None:
            self.latest[key] = attempt
            if fix:
                self.fixes[key] = attempt["fix_round"] = self.fixes.get(key, 0) + 1
        else:
            attempt["repair_round"] = (
                self.repair_attempts.get(key, {}).get("repair_round", 0) + 1
            )
            self.repair_attempts[key] = attempt
        self.state["requests"].append(attempt)
        self.budget["requests"] += 1
        self.budget["reserved_tokens"] += reservation
        self.save()
        active[pool.submit(self.convert, image, repair)] = (key, attempt, self.limit)

    def restore_repairs(self):
        if not self.repair_rounds:
            return
        pending = {
            key: attempt["patches"][0]
            for key, latest in self.latest.items()
            if (attempt := self.repair_attempts.get(key, latest))["status"] == "success"
            and attempt.get("repair_round", 0) < self.repair_rounds
            and attempt.get("warnings")
            and attempt.get("patches")
        }
        if not pending:
            return
        parser = LlmClient(self.settings["model"], self.settings["effort"])
        for key, patch in pending.items():
            try:
                errors = parser.mermaid_diagnostics(patch)
            except LlmError as exc:
                raise ValueError(
                    f"cannot restore Mermaid diagnostics: {exc.reason}"
                ) from exc
            if errors:
                self.repairs[key] = {
                    k: patch[k] for k in ("kind", "markdown", "uncertainty")
                } | {"errors": errors}

    def dispatch(self, jobs, pipelines):
        self.restore_repairs()
        pending = {}
        for key, image in jobs.items():
            previous = self.latest.get(key)
            if previous and (previous["status"] != "failed" or not self.retry):
                continue
            cached = self.cached(key)
            if cached is None:
                pending[key] = image
            else:
                self.reused[key] = self.latest[key] = {
                    "key": key,
                    "status": "reused",
                    "patches": cached,
                }
        self.save()
        self.dispatch_pending(pending, pipelines)
        while self.fix_limit and not self.stop:
            pending = {
                key: image
                for key, image in jobs.items()
                if self.fixes.get(key, 0) < self.fix_limit
                and (last := self.latest.get(key)) is not None
                and (
                    (
                        last["status"] == "failed"
                        and last.get("reason") in _FIXABLE_FAILURES
                    )
                    or (
                        last["status"] == "unknown"
                        and last.get("reason") == "connection_outcome_unknown"
                    )
                )
            }
            if not pending:
                break
            self.dispatch_pending(pending, pipelines, fix=True)
        while self.repairs and not self.stop:
            repairs = {
                key: repair
                for key, repair in self.repairs.items()
                if self.repair_attempts.get(key, {}).get("repair_round", 0)
                < self.repair_rounds
            }
            self.repairs = {}
            self.dispatch_pending(
                {key: jobs[key] for key in repairs}, pipelines, repairs
            )
        self.repairs.clear()

    def dispatch_pending(self, pending, pipelines, repairs=None, *, fix=False):
        repairs = {} if repairs is None else repairs
        waiting = deque(
            deque(key for key in pipeline if key in pending) for pipeline in pipelines
        )
        slots = []
        with ThreadPoolExecutor(max_workers=self.limit) as pool:
            active = {}
            while (waiting or slots) and not self.stop:
                # After a 429, drain the prior tier. Park overflow pipelines and
                # finish them before admitting more documents.
                while active and any(
                    tier > self.limit for _, _, tier in active.values()
                ):
                    self.settle(active)
                if self.stop:
                    break
                in_flight = {key for key, _, _ in active.values()}
                for slot in slots:
                    while slot and slot[0] not in pending and slot[0] not in in_flight:
                        slot.popleft()
                slots = [slot for slot in slots if slot]
                while waiting and len(slots) < self.limit:
                    slot = waiting.popleft()
                    while slot and slot[0] not in pending and slot[0] not in in_flight:
                        slot.popleft()
                    if slot:
                        slots.append(slot)
                for slot in slots[: self.limit]:
                    key = slot[0]
                    if key in in_flight:
                        continue
                    self.admit(
                        pool,
                        active,
                        key,
                        pending.pop(key),
                        repair=repairs.get(key),
                        fix=fix,
                    )
                    if self.stop:
                        break
                    in_flight.add(key)
                if active:
                    self.settle(active)
            while active:
                self.settle(active)

    def publish(self, texts, layout):
        rows = []
        for position, (doc, text, (bindings, context)) in enumerate(
            zip(self.docs, texts, layout)
        ):
            records, replacements, evidence = [], [], {}
            for image in doc["images"]:
                identifier = image["id"]
                record = {
                    "id": identifier,
                    "resource_id": image["resource_id"],
                    "status": "unselected",
                }
                records.append(record)
                if not image["selected"]:
                    continue
                attempt = self.latest.get(bindings.get(identifier))
                if (
                    image["error"]
                    or not attempt
                    or attempt["status"] not in {"success", "reused"}
                ):
                    status = (
                        "failed"
                        if image["error"]
                        else attempt["status"]
                        if attempt
                        else "unattempted"
                    )
                    reason = image["error"] or (
                        attempt.get("reason") if attempt else self.stop
                    )
                    record.update(status=status, reason=reason)
                    continue
                (patch,) = attempt["patches"]
                if patch.get("kind") == "hold":
                    record.update(
                        status="held",
                        reason="IMAGE_CONVERSION_HELD",
                        uncertainty=patch["uncertainty"],
                    )
                    continue
                record["status"] = attempt["status"]
                if patch.get("warnings"):
                    record["warnings"] = patch["warnings"]
                block = _block(image, self.mode, patch)
                replacements.append((image["start"], image["end"], f"\n\n{block}\n\n"))
                resource = image["resource_id"]
                if resource not in evidence:
                    artifact = self.artifacts / str(position) / f"{identifier}.md"
                    block_bytes = block.encode()
                    evidence[resource] = {
                        "resource_id": resource,
                        "status": "success",
                        "source_path": image["source_path"],
                        "source_sha256": image["sha256"],
                        "artifact": str(artifact),
                        "artifact_sha256": _sha(block_bytes),
                    }
                    if not artifact.exists():
                        _atomic(artifact, block_bytes, exclusive=True)
                    elif artifact.read_bytes() != block_bytes:
                        raise ValueError("OCR evidence has drifted")
            row = {
                "document_id": doc["document_id"],
                "path": doc["path"],
                "status": "partial",
                "output": None,
                "resources": records,
            }
            if all(r["status"] in {"success", "reused", "unselected"} for r in records):
                for start, end, replacement in reversed(replacements):
                    text = text[:start] + replacement + text[end:]
                output = Path(doc["output"])
                content = text.encode()
                if output.exists():
                    if output.read_bytes() != content:
                        raise ValueError("OCR destination has drifted")
                else:
                    _atomic(output, content, exclusive=True)
                ocr = {
                    "contract": "mdocr-ocr-2",
                    "configuration": self.config,
                    "images": list(evidence.values()),
                }
                if context is not None:
                    ocr["context_sha256"] = context
                row.update(
                    status="completed",
                    output=str(output),
                    output_sha256=_sha(content),
                    ocr=ocr,
                )
            rows.append(row)
        self.state["documents"] = rows
        if self.stop and self.stop not in self.state["causes"]:
            self.state["causes"].append(self.stop)
        complete = all(r["status"] == "completed" for r in rows)
        self.state["status"] = "completed" if complete else "partial"
        self.save()
        return 0 if complete else 2


def run_command(args) -> int:
    plan_path = Path(args.plan).resolve(strict=True)
    result = Path(args.result).resolve()
    plan = read_plan(plan_path)
    digest = _sha(plan_path.read_bytes())
    ledger = plan_path.with_name(plan_path.name + ".execution.json")
    artifacts = plan_path.with_name(plan_path.name + ".evidence").resolve()
    lock_paths = tuple(
        path.with_name(path.name + ".lock") for path in (plan_path, result)
    )
    texts = _validate(plan, plan_path, result, ledger, artifacts, lock_paths)
    result.parent.mkdir(parents=True, exist_ok=True)
    with (
        lock_paths[0].open("a+b") as plan_lock,
        lock_paths[1].open("a+b") as result_lock,
    ):
        for name, lock in (("plan", plan_lock), ("result", result_lock)):
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError(f"{name} is already being executed") from exc
        execution = Execution(
            plan, digest, ledger, result, artifacts, args.retry_failed
        )
        jobs, pipelines, layout = execution.plan_jobs()
        execution.dispatch(jobs, pipelines)
        return execution.publish(texts, layout)
