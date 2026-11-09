"""Exact raw comparison with bounded Myers explanations; no semantic cleaning."""

from collections.abc import Sequence
from typing import Any

from .model import CompareOptions


class DiffBudgetExceeded(Exception):
    pass


class WorkBudget:
    def __init__(self, remaining: int):
        self.remaining = remaining

    def spend(self) -> None:
        if self.remaining <= 0:
            raise DiffBudgetExceeded
        self.remaining -= 1


def _myers(
    old: Sequence[str], new: Sequence[str], budget: WorkBudget
) -> list[tuple[str, int, int]]:
    """Return shortest insert/delete edits with zero-based old/new positions.

    Frontier cells and matched snake steps bound work and stored trace size.
    Equal runs are omitted from returned edits, never from comparison.
    """
    frontier = {1: 0}
    trace = []
    old_size, new_size = len(old), len(new)
    for distance in range(old_size + new_size + 1):
        trace.append(frontier.copy())
        for diagonal in range(-distance, distance + 1, 2):
            budget.spend()
            if diagonal == -distance or (
                diagonal != distance
                and frontier.get(diagonal - 1, -1) < frontier.get(diagonal + 1, -1)
            ):
                x = frontier.get(diagonal + 1, 0)
            else:
                x = frontier[diagonal - 1] + 1
            y = x - diagonal
            while x < old_size and y < new_size and old[x] == new[y]:
                budget.spend()
                x += 1
                y += 1
            frontier[diagonal] = x
            if x >= old_size and y >= new_size:
                return _backtrack(trace, old_size, new_size)
    raise AssertionError("Myers path must reach the target")


def _backtrack(
    trace: list[dict[int, int]], x: int, y: int
) -> list[tuple[str, int, int]]:
    edits = []
    for distance in range(len(trace) - 1, -1, -1):
        frontier = trace[distance]
        diagonal = x - y
        if diagonal == -distance or (
            diagonal != distance
            and frontier.get(diagonal - 1, -1) < frontier.get(diagonal + 1, -1)
        ):
            previous_diagonal = diagonal + 1
        else:
            previous_diagonal = diagonal - 1
        previous_x = frontier.get(previous_diagonal, 0)
        previous_y = previous_x - previous_diagonal
        while x > previous_x and y > previous_y:
            x -= 1
            y -= 1
        if distance == 0:
            break
        if x == previous_x:
            y -= 1
            edits.append(("insert", x, y))
        else:
            x -= 1
            edits.append(("delete", x, y))
    edits.reverse()
    return edits


def _regions(edits: list[tuple[str, int, int]]) -> list[tuple[int, int, int, int]]:
    regions: list[tuple[int, int, int, int]] = []
    for operation, old_position, new_position in edits:
        if regions and regions[-1][2:] == (old_position, new_position):
            old_start, new_start, _, _ = regions.pop()
        else:
            old_start, new_start = old_position, new_position
        regions.append(
            (
                old_start,
                new_start,
                old_position + (operation == "delete"),
                new_position + (operation == "insert"),
            )
        )
    return regions


def _evidence(
    old: list[str],
    new: list[str],
    edits: list[tuple[str, int, int]],
    options: CompareOptions,
    budget: WorkBudget,
) -> tuple[list[dict[str, Any]], bool]:
    remaining = options.max_evidence_chars
    differences = []
    truncated = False
    for old_start, new_start, old_end, new_end in _regions(edits):
        removed = "".join(old[old_start:old_end])
        added = "".join(new[new_start:new_end])
        if remaining <= 0:
            truncated = True
            break
        visible_removed = removed[:remaining]
        remaining -= len(visible_removed)
        visible_added = added[:remaining]
        remaining -= len(visible_added)
        region: dict[str, Any] = {
            "old_start_line": old_start + 1,
            "new_start_line": new_start + 1,
            "removed": visible_removed,
            "added": visible_added,
            "characters": [],
            "characters_omitted": False,
        }
        if len(visible_removed) + len(visible_added) < len(removed) + len(added):
            truncated = True
            region["characters_omitted"] = True
        elif len(removed) + len(added) > options.max_char_region:
            region["characters_omitted"] = True
        else:
            for operation, old_position, new_position in _myers(removed, added, budget):
                if remaining <= 0:
                    truncated = True
                    region["characters_omitted"] = True
                    break
                value = (
                    removed[old_position]
                    if operation == "delete"
                    else added[new_position]
                )
                region["characters"].append(
                    {
                        "operation": operation,
                        "old_offset": old_position,
                        "new_offset": new_position,
                        "text": value,
                    }
                )
                remaining -= len(value)
        differences.append(region)
    return differences, truncated


def text_rules(options: CompareOptions) -> list[dict[str, str]]:
    if options.normalize_line_endings:
        return [
            {
                "name": "line-endings-lf",
                "version": "1",
                "contract": "CRLF and lone CR become LF; all other bytes are preserved",
            }
        ]
    return [{"name": "identity", "version": "1"}]


def compare_text(
    old: bytes | None, new: bytes, options: CompareOptions
) -> dict[str, Any]:
    if old is None:
        return {
            "status": "NEW",
            "reason_codes": ["NEW_DOCUMENT"],
            "differences": [],
            "comparison_complete": True,
            "evidence_truncated": False,
            "byte_equal": None,
        }
    same = len(old) == len(new) and old == new
    result: dict[str, Any] = {
        "status": "UNCHANGED" if same else "CHANGED",
        "reason_codes": ["TEXT_BYTES_EQUAL" if same else "TEXT_BYTES_DIFFER"],
        "differences": [],
        "comparison_complete": True,
        "evidence_truncated": False,
        "byte_equal": same,
    }
    if same:
        return result
    if options.normalize_line_endings:
        old = old.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        new = new.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        if old == new:
            result["status"] = "UNCHANGED"
            result["reason_codes"].append("TEXT_NORMALIZED_EQUAL")
            return result
    try:
        old_lines = old.decode("utf-8").splitlines(keepends=True)
        new_lines = new.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        result.update(status="UNKNOWN", comparison_complete=False)
        result["reason_codes"].append("TEXT_ENCODING_UNSUPPORTED")
        return result
    budget = WorkBudget(options.max_diff_work)
    try:
        edits = _myers(old_lines, new_lines, budget)
        if options.include_evidence:
            result["differences"], result["evidence_truncated"] = _evidence(
                old_lines, new_lines, edits, options, budget
            )
    except DiffBudgetExceeded:
        result.update(status="UNKNOWN", comparison_complete=False)
        result["reason_codes"].append("DIFF_BUDGET_EXCEEDED")
    return result
