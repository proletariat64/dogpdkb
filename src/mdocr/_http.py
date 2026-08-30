"""Duck-typed HTTP client contract (requests.Session-compatible).

mdocr only needs ``get`` and ``request``; tests inject fake sessions that
implement this shape without inheriting from ``requests.Session``. Params are
loose because requests' own stubs use TypedDict kwargs that are impractical to
replicate; the protocol only enforces that the two methods exist.
"""

from __future__ import annotations

from typing import Any, Protocol


class HttpClient(Protocol):
    def get(self, *args: Any, **kwargs: Any) -> Any: ...

    def request(self, *args: Any, **kwargs: Any) -> Any: ...
