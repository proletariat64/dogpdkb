"""Optional Pi-backed multimodal conversion bridge."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "openai/gpt-6.1-terra"
DEFAULT_EFFORT = "medium"
_PI_BRIDGE = Path(__file__).with_name("pi_bridge.mjs")


class LlmError(RuntimeError):
    """A public, machine-readable Pi bridge failure."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        reason: str = "bridge_error",
        usage_tokens: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.usage_tokens = usage_tokens


class LlmClient:
    def __init__(self, model: str, effort: str) -> None:
        self.model = model
        self.effort = effort
        self.env = os.environ.copy()

    def _invoke(self, request: dict[str, Any], *, timeout: int = 240) -> dict[str, Any]:
        try:
            process = subprocess.run(
                ["node", str(_PI_BRIDGE)],
                input=json.dumps(request),
                text=True,
                capture_output=True,
                timeout=timeout,
                env=self.env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            if request["op"] == "doctor":
                raise LlmError(
                    "Pi diagnostic timed out", reason="bridge_timeout"
                ) from exc
            # The worker may have submitted the provider request before timing out.
            raise LlmError(
                "Pi bridge timed out after possible submission; outcome is unknown",
                reason="unknown_outcome",
            ) from exc
        except OSError as exc:
            raise LlmError(
                "Pi bridge could not be started", reason="bridge_unavailable"
            ) from exc
        try:
            result = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise LlmError(
                "Pi bridge returned invalid protocol data",
                reason="unknown_outcome"
                if request["op"] == "convert"
                else "invalid_response",
            ) from exc
        if not isinstance(result, dict):
            raise LlmError(
                "Pi bridge returned invalid protocol data", reason="invalid_response"
            )
        if process.returncode or not result.get("ok"):
            err = result.get("error", {})
            raise LlmError(
                err.get("message", "Pi bridge failed"),
                status=err.get("status"),
                reason=err.get("reason", "bridge_error"),
                usage_tokens=err.get("usage_tokens"),
            )
        return result

    def doctor(self) -> dict[str, Any]:
        return self._invoke(
            {"op": "doctor", "model": self.model, "effort": self.effort}, timeout=15
        )

    def preflight(self) -> dict[str, Any]:
        return self._invoke(
            {"op": "preflight", "model": self.model, "effort": self.effort}
        )

    def convert(
        self, markdown: str, images: list[dict[str, str]], prompt: str
    ) -> dict[str, Any]:
        result = self._invoke(
            {
                "op": "convert",
                "model": self.model,
                "effort": self.effort,
                "markdown": markdown,
                "images": images,
                "prompt": prompt,
            }
        )
        expected = [item["id"] for item in images]
        patches = result.get("patches")
        if not isinstance(patches, list) or [p.get("id") for p in patches] != expected:
            raise LlmError(
                "Response image IDs do not exactly match planned occurrence order",
                reason="invalid_response",
            )
        if any(
            p.get("kind") not in {"mermaid", "table", "ui", "text", "mixed", "hold"}
            or not isinstance(p.get("markdown"), str)
            or not isinstance(p.get("uncertainty"), str)
            or "<!-- mdocr:" in p["markdown"].lower()
            for p in patches
        ):
            raise LlmError(
                "Response contains an invalid or reserved patch payload",
                reason="invalid_response",
            )
        usage = result.get("usage_tokens")
        if usage is not None and (not isinstance(usage, int) or usage < 0):
            raise LlmError("Response token usage is invalid", reason="invalid_response")
        return {"patches": patches, "usage_tokens": usage}

    def token_upper_bound(self, *_: Any, **__: Any) -> None:
        # No trustworthy provider/model bound for vision input is exposed here.
        return None
