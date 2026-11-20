"""Optional Pi-backed multimodal conversion bridge."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

DEFAULT_MODEL = "openai/gpt-6.1-terra"
DEFAULT_EFFORT = "medium"
IMAGE_SCHEMA = "mdocr-image-content-1"
_PI_BRIDGE = Path(__file__).with_name("pi_bridge.mjs")


def _valid_usage(value: Any) -> bool:
    return value is None or (type(value) is int and value >= 0)


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
        def invalid_protocol() -> LlmError:
            return LlmError(
                "Pi bridge returned invalid protocol data",
                reason="unknown_outcome"
                if request["op"] == "convert"
                else "bridge_error",
            )

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
            raise invalid_protocol() from exc
        if not isinstance(result, dict):
            raise invalid_protocol()
        if process.returncode == 0 and result.get("ok") is True:
            if request["op"] == "convert" and (
                not isinstance(result.get("image"), dict)
                or not _valid_usage(result.get("usage_tokens"))
            ):
                raise invalid_protocol()
            return result
        if process.returncode != 1 or result.get("ok") is not False:
            raise invalid_protocol()
        err = result.get("error")
        if not isinstance(err, dict) or not all(
            isinstance(err.get(field), str) and err[field]
            for field in ("reason", "message")
        ):
            raise invalid_protocol()
        status, usage = err.get("status"), err.get("usage_tokens")
        if (status is not None and type(status) is not int) or not _valid_usage(usage):
            raise invalid_protocol()
        raise LlmError(
            err["message"], status=status, reason=err["reason"], usage_tokens=usage
        )

    def doctor(self) -> dict[str, Any]:
        return self._invoke(
            {"op": "doctor", "model": self.model, "effort": self.effort}, timeout=15
        )

    def preflight(self) -> dict[str, Any]:
        return self._invoke(
            {"op": "preflight", "model": self.model, "effort": self.effort}
        )

    def convert(
        self, original: bytes, prompt: str, *, mermaid_diagnostics=False, repair=None
    ) -> dict[str, Any]:
        # Image dependencies belong to conversion; doctor probes them separately.
        from .llm_image import vision_image
        from .prepare import ImageError

        try:
            data, mime = vision_image(original)
        except ImageError as exc:
            raise LlmError(
                "Cannot prepare vision image", reason="invalid_image"
            ) from exc
        with TemporaryDirectory(prefix="mdocr-vision-") as tmp:
            path = Path(tmp) / "attachment"
            path.write_bytes(data)
            result = self._invoke(
                {
                    "op": "convert",
                    "model": self.model,
                    "effort": self.effort,
                    "image": {"path": str(path), "mime_type": mime},
                    "prompt": prompt,
                    "mermaid_diagnostics": mermaid_diagnostics,
                    "repair": repair,
                }
            )
        output = {"image": result["image"], "usage_tokens": result.get("usage_tokens")}
        if mermaid_diagnostics:
            output["mermaid_errors"] = self._mermaid_errors(result)
        return output

    @staticmethod
    def _mermaid_errors(result):
        errors = result.get("mermaid_errors")
        if not isinstance(errors, list) or not all(
            isinstance(e, dict)
            and set(e) == {"block", "message"}
            and type(e["block"]) is int
            and e["block"] > 0
            and isinstance(e["message"], str)
            for e in errors
        ):
            raise LlmError(
                "Pi bridge returned invalid diagnostics", reason="unknown_outcome"
            )
        return errors

    def mermaid_diagnostics(self, patch):
        return self._mermaid_errors(
            self._invoke({"op": "mermaid_diagnostics", "patch": patch})
        )

    def token_upper_bound(self, *_: Any, **__: Any) -> None:
        # No trustworthy provider/model bound for vision input is exposed here.
        return None
