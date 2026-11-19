"""Build bounded model views from complete public IR; never infer absent text."""

from bisect import bisect_left, bisect_right
from itertools import pairwise


def prepare(detail, window=2):
    pairs = sorted(
        (
            op
            for op in detail["changes"]
            if op["old_ref"] is not None and op["new_ref"] is not None
        ),
        key=lambda op: op["new_ref"],
    )
    # Public diffs may omit unchanged units at identical indices. Such units
    # are safe context anchors only when neither occurrence is owned by an op.
    owned_old = {op["old_ref"] for op in detail["changes"] if op["old_ref"] is not None}
    owned_new = {op["new_ref"] for op in detail["changes"] if op["new_ref"] is not None}
    for index in range(min(len(detail["old_units"]), len(detail["new_units"]))):
        if index in owned_old or index in owned_new:
            continue
        old, new = detail["old_units"][index], detail["new_units"][index]
        if all(old[field] == new[field] for field in ("kind", "context", "key")):
            pairs.append({"old_ref": index, "new_ref": index})
    pairs.sort(key=lambda pair: pair["new_ref"])
    old_order = [op["old_ref"] for op in pairs]
    order_preserved = all(a < b for a, b in pairwise(old_order))
    anchors_by_ref = {}
    for field in ("old_ref", "new_ref"):
        ordered = sorted(pairs, key=lambda pair: pair[field])
        anchors_by_ref[field] = ([pair[field] for pair in ordered], ordered)
    incoming = [
        (ref, " ".join(unit["text"].split()))
        for ref, unit in enumerate(detail["new_units"])
        if unit["kind"] != "code" and not unit.get("exporter_title")
    ]
    operations, excluded = [], 0
    for op in detail["changes"]:
        if op["operation"] == "equal":
            continue
        if op["operation"] == "move" and order_preserved:
            old, new = (
                detail["old_units"][op["old_ref"]],
                detail["new_units"][op["new_ref"]],
            )
            if all(old[field] == new[field] for field in ("kind", "context", "key")):
                excluded += 1
                continue
        projected = {key: op[key] for key in ("old_ref", "new_ref", "operation")}
        if op["operation"] == "delete":
            unit = detail["old_units"][op["old_ref"]]
            if unit["kind"] != "code" and (text := " ".join(unit["text"].split())):
                candidate = None
                for ref, larger in incoming:
                    if len(text) < len(larger) and text in larger:
                        if candidate is not None:
                            break
                        candidate = ref
                else:
                    if candidate is not None:
                        projected["retained_text_candidate_new_ref"] = candidate
        operations.append(dict(projected, operation_id=len(operations)))
    wanted = {"old_units": set(), "new_units": set()}
    anchors = []

    def include(side, index):
        units = detail[side]
        wanted[side].update(
            i
            for i in range(max(0, index - window), min(len(units), index + window + 1))
            if not units[i].get("exporter_title")
        )

    for op_index, op in enumerate(operations):
        linked_ref = op.get("retained_text_candidate_new_ref")
        if linked_ref is not None:
            if (
                op["operation"] != "delete"
                or type(linked_ref) is not int
                or not 0 <= linked_ref < len(detail["new_units"])
                or detail["new_units"][linked_ref].get("exporter_title")
            ):
                raise ValueError("invalid incoming retained-text evidence ref")
            # This is an explicit evidence link, not a second operation target
            # or a global increase of the neighboring context window.
            wanted["new_units"].add(linked_ref)
        for side, own, opposite in (
            ("old_units", "old_ref", "new_ref"),
            ("new_units", "new_ref", "old_ref"),
        ):
            if op[own] is not None:
                include(side, op[own])
                continue
            # Pure deletion/addition has no reference on one side. Project the
            # nearest preceding/following aligned anchors onto that side.
            position = op[opposite]
            refs, candidates = anchors_by_ref[opposite]
            before = bisect_left(refs, position)
            after = bisect_right(refs, position)
            neighbors = (
                candidates[max(0, before - 1) : before] + candidates[after : after + 1]
            )
            if neighbors:
                for pair in neighbors:
                    include(side, pair[own])
                    anchors.append(
                        {
                            "operation": op_index,
                            "side": side,
                            "anchor_ref": pair[own],
                            "opposite_ref": pair[opposite],
                        }
                    )
            else:
                # No trustworthy alignment: provide all available body units,
                # rather than presenting an unshown opposite body as empty.
                wanted[side].update(
                    i
                    for i, unit in enumerate(detail[side])
                    if not unit.get("exporter_title")
                )
    result = {
        "operations": operations,
        "context_window_units_each_side": window,
        "paired_unit_order_preserved": order_preserved,
        "excluded_position_shift_moves_with_equal_normalized_content_and_context": excluded,
        "opposite_context_anchors": anchors,
        "source_body": {},
    }
    for side, source_side in (("old_units", "baseline"), ("new_units", "incoming")):
        units = detail[side]
        available = sum(not unit.get("exporter_title") for unit in units)
        status = (
            "ABSENT_DOCUMENT"
            if detail["source_inputs"][source_side] is None
            else ("AVAILABLE" if available else "NO_TEXT")
        )
        result["source_body"][source_side] = {
            "status": status,
            "available_units": available,
            "included_units": len(wanted[side]),
        }
        result[side] = [
            {
                "ref": i,
                **{field: units[i][field] for field in ("kind", "context", "text")},
            }
            for i in sorted(wanted[side])
        ]
    result["context_is_partial"] = any(
        side["included_units"] < side["available_units"]
        for side in result["source_body"].values()
    )
    return result
