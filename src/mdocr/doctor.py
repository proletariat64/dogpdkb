"""Local diagnostics for preparation and both OCR backends."""

import json
import os
import platform
import shutil
from importlib import import_module
from importlib.metadata import version

from . import __version__
from .llm import LlmClient, LlmError

_HINTS = {
    "sdk_unavailable": "Install Pi or set MDOCR_PI_SDK_DIR to its SDK package root.",
    "unsupported_model": "Select a supported physical OpenAI or OpenAI Codex Responses model with image input.",
    "unsupported_effort": "Select a reasoning effort supported by the model.",
    "oauth_required": "Use Pi /login for the selected provider. API keys are not used.",
    "proxy_unavailable": "Check HTTPS_PROXY and Pi's undici dependency.",
    "mermaid_parser_unavailable": "Reinstall the managed release or set MDOCR_NODE_DEPENDENCIES to a working Mermaid/jsdom npm prefix.",
    "bridge_timeout": "The local Pi diagnostic did not finish within 15 seconds.",
}


def _llm_check(model, effort):
    if not shutil.which("node"):
        return {
            "name": "pi_runtime",
            "status": "fail",
            "code": "node_unavailable",
            "detail": "Install Node.js meeting Pi's engine requirement and put node on PATH.",
        }
    try:
        result = LlmClient(model, effort).doctor()
    except LlmError as exc:
        code = exc.reason if exc.reason in _HINTS else "bridge_error"
        return {
            "name": "pi_runtime",
            "status": "fail",
            "code": code,
            "detail": _HINTS.get(
                code,
                "Could not run the installed Pi SDK and Mermaid parser with this Node runtime.",
            ),
        }
    if result.get("oauth") not in ("stored", "expired"):
        return {
            "name": "pi_runtime",
            "status": "fail",
            "code": "bridge_error",
            "detail": "Pi diagnostic did not report local OAuth status.",
        }
    expired = result["oauth"] == "expired"
    return {
        "name": "pi_runtime",
        "status": "warn" if expired else "pass",
        "code": "oauth_expired" if expired else "oauth_stored",
        "detail": "Mermaid works; stored Pi OAuth is expired and needs refresh on the next live run."
        if expired
        else "Model/effort, stored Pi OAuth and Mermaid parser are present.",
    }


def check(args):
    checks = [{"name": "python", "status": "pass", "detail": platform.python_version()}]
    for name, module in (
        ("pillow", "PIL.Image"),
        ("markdown-it-py", "markdown_it"),
        ("requests", None),
    ):
        try:
            # urllib3 probes a loopback socket at import time; check Requests
            # distribution metadata rather than opening sockets in doctor.
            detail = (
                f"installed {version(name)}"
                if module is None
                else getattr(import_module(module), "__version__", "imported")
            )
        except (ImportError, OSError):
            checks.append(
                {
                    "name": name,
                    "status": "fail",
                    "code": "dependency_unavailable",
                    "detail": f"Reinstall {name} in the same Python environment as mdocr.",
                }
            )
        else:
            checks.append(
                {
                    "name": name,
                    "status": "pass",
                    "detail": detail,
                }
            )
    if args.mode == "paddle":
        token_present = bool(os.environ.get("PADDLEOCR_TOKEN", "").strip())
        checks.append(
            {
                "name": "paddle_token",
                "status": "pass" if token_present else "fail",
                "code": "token_present" if token_present else "token_missing",
                "detail": "PADDLEOCR_TOKEN is set."
                if token_present
                else "Set PADDLEOCR_TOKEN before submitting a new PaddleOCR job.",
            }
        )
    if args.mode == "llm":
        checks.append(_llm_check(args.model, args.effort))
    ok = all(item["status"] != "fail" for item in checks)
    report = {
        "schema": "mdocr-doctor-1",
        "version": __version__,
        "mode": args.mode,
        "ok": ok,
        "network_checked": False,
        "checks": checks,
    }
    if args.mode == "llm":
        report.update(model=args.model, effort=args.effort)
    if args.json:
        print(json.dumps(report))
    else:
        print(f"mdocr {__version__} doctor ({args.mode}, local only)")
        for item in checks:
            print(f"{item['status'].upper()} {item['name']}: {item['detail']}")
    return 0 if ok else 2
