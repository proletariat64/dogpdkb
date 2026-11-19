"""Local diagnostics for the independent Markdown comparison CLI."""

import json
import platform

from . import __version__
from .semantic_score import _bridge_request

_HINTS = {
    "node_unavailable": "Install Node.js meeting Pi's engine requirement and put node on PATH.",
    "sdk_unavailable": "Install Pi or set MDCOMPARE_PI_SDK_DIR to its SDK package root.",
    "unsupported_model": "Select an installed physical OpenAI or OpenAI Codex Responses model that supports the selected effort.",
    "unsupported_effort": "Select a reasoning effort supported by the model.",
    "oauth_required": "Use Pi /login for the selected provider. API keys are not used.",
    "proxy_unavailable": "Check HTTPS_PROXY and Pi's undici dependency.",
    "bridge_timeout": "The local Pi diagnostic did not finish within 15 seconds.",
}


def check(args):
    checks = [{"name": "python", "status": "pass", "detail": platform.python_version()}]
    try:
        from PIL.Image import __version__ as pillow_version
    except (ImportError, OSError):
        checks.append(
            {
                "name": "pillow",
                "status": "fail",
                "code": "dependency_unavailable",
                "detail": "Reinstall Pillow in the same Python environment as mdcompare.",
            }
        )
    else:
        checks.append({"name": "pillow", "status": "pass", "detail": pillow_version})
    if args.mode == "llm":
        result = _bridge_request(
            {"op": "doctor", "model": args.model, "effort": args.effort}, timeout=15
        )
        if result.get("ok") is True and result.get("oauth") in ("stored", "expired"):
            expired = result["oauth"] == "expired"
            checks.append(
                {
                    "name": "pi_runtime",
                    "status": "warn" if expired else "pass",
                    "code": "oauth_expired" if expired else "oauth_stored",
                    "detail": "Stored Pi OAuth is expired; the next live run must refresh it."
                    if expired
                    else "Model/effort and stored Pi OAuth are present.",
                }
            )
        else:
            reason = result.get("reason")
            code = (
                reason
                if isinstance(reason, str) and reason in _HINTS
                else "bridge_error"
            )
            checks.append(
                {
                    "name": "pi_runtime",
                    "status": "fail",
                    "code": code,
                    "detail": _HINTS.get(
                        code,
                        "Could not run the installed Pi SDK with this Node runtime.",
                    ),
                }
            )
    ok = all(item["status"] != "fail" for item in checks)
    report = {
        "schema": "mdcompare-doctor-1",
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
        print(f"mdcompare {__version__} doctor ({args.mode}, local only)")
        for item in checks:
            print(f"{item['status'].upper()} {item['name']}: {item['detail']}")
    return 0 if ok else 2
