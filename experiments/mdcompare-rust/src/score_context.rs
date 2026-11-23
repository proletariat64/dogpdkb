use anyhow::{Context, Result, bail, ensure};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{BTreeSet, HashSet},
    sync::LazyLock,
};

const SIDES: [&str; 2] = ["old_units", "new_units"];
const SOURCE_SIDES: [&str; 2] = ["baseline", "incoming"];
const WINDOW: usize = 2;

#[derive(Deserialize)]
struct Change {
    operation: String,
    #[serde(deserialize_with = "Deserialize::deserialize")]
    old_ref: Option<usize>,
    #[serde(deserialize_with = "Deserialize::deserialize")]
    new_ref: Option<usize>,
}

impl Change {
    fn refs(&self) -> [Option<usize>; 2] {
        [self.old_ref, self.new_ref]
    }
}

#[derive(Deserialize)]
struct Unit {
    kind: String,
    context: Value,
    key: Value,
    text: String,
    #[serde(default)]
    exporter_title: bool,
}

impl Unit {
    fn same_contextual_content(&self, other: &Self) -> bool {
        self.kind == other.kind && self.context == other.context && self.key == other.key
    }
}

#[derive(Deserialize)]
struct Evidence {
    source_inputs: SourceInputs,
    old_units: Vec<Unit>,
    new_units: Vec<Unit>,
    changes: Vec<Change>,
}

#[derive(Deserialize)]
struct SourceInputs {
    baseline: Option<Source>,
    incoming: Option<Source>,
}

#[derive(Deserialize)]
struct Source {
    content_kind: String,
    metadata: Value,
}

pub struct Packet {
    pub evidence: String,
    pub operations: usize,
}

static RESIDUE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\[PaddleOCR image region: untranscribed\]|<!--\s*(?:mdocr:ocr-(?:begin|end)|这是一张图片[，,]\s*ocr\s*内容为[：:])|!\[[^\]\n]*\]\([^\n)]*\)|<img\b[^>]*>",
    )
    .expect("valid residue pattern")
});

fn has_residue(text: &str) -> bool {
    text.contains('\u{fffd}')
        || RESIDUE.find_iter(text).any(|hit| {
            !hit.as_str().starts_with("![")
                || hit.start() == 0
                || text.as_bytes()[hit.start() - 1] != b'\\'
        })
}

fn include(wanted: &mut BTreeSet<usize>, units: &[Unit], index: usize) {
    wanted.extend(
        (index.saturating_sub(WINDOW)..(index + WINDOW + 1).min(units.len()))
            .filter(|&i| !units[i].exporter_title),
    );
}

fn optional_joined_text(unit: &Unit) -> Option<String> {
    (unit.kind != "code" && !unit.exporter_title)
        .then(|| unit.text.split_whitespace().collect::<Vec<_>>().join(" "))
        .filter(|text| !text.is_empty())
}

pub fn prepare(bytes: &[u8]) -> Result<Result<Packet, &'static str>> {
    let evidence: Evidence = serde_json::from_slice(bytes)?;
    let (Some(old), Some(new)) = (
        &evidence.source_inputs.baseline,
        &evidence.source_inputs.incoming,
    ) else {
        return Ok(Err("SOURCE_NOT_SCOREABLE"));
    };
    if !matches!(old.content_kind.as_str(), "raw" | "enriched")
        || new.content_kind != "raw"
        || ["name", "logical_path"]
            .iter()
            .any(|field| old.metadata[*field] != new.metadata[*field])
    {
        return Ok(Err("SOURCE_NOT_SCOREABLE"));
    }
    let units = [evidence.old_units.as_slice(), evidence.new_units.as_slice()];
    let mut owned = [HashSet::new(), HashSet::new()];
    let mut pairs = Vec::new();
    for change in &evidence.changes {
        let expected = match change.operation.as_str() {
            "add" => [false, true],
            "delete" => [true, false],
            "modify" | "move" | "equal" => [true, true],
            "uncertain" => return Ok(Err("ALIGNMENT_UNCERTAIN")),
            _ => bail!("Invalid score evidence operation"),
        };
        let refs = change.refs();
        ensure!(
            refs.map(|value| value.is_some()) == expected,
            "Invalid score evidence sides"
        );
        for (side, reference) in refs.into_iter().enumerate() {
            if let Some(index) = reference {
                ensure!(
                    units[side]
                        .get(index)
                        .is_some_and(|unit| !unit.exporter_title)
                        && owned[side].insert(index),
                    "Invalid score evidence ownership"
                );
            }
        }
        if let [Some(old), Some(new)] = refs {
            pairs.push([old, new]);
        }
    }
    for (index, (old, new)) in units[0].iter().zip(units[1]).enumerate() {
        if !old.exporter_title
            && !new.exporter_title
            && !owned[0].contains(&index)
            && !owned[1].contains(&index)
            && old.same_contextual_content(new)
        {
            pairs.push([index, index]);
        }
    }
    pairs.sort_by_key(|pair| pair[1]);
    let order_preserved = pairs.windows(2).all(|pair| pair[0][0] < pair[1][0]);
    let mut by_old = pairs.clone();
    by_old.sort_by_key(|pair| pair[0]);
    let anchors_by_ref = [&by_old, &pairs];
    let incoming: Vec<_> = units[1]
        .iter()
        .enumerate()
        .filter_map(|(index, unit)| Some((index, optional_joined_text(unit)?)))
        .collect();
    let mut operations = Vec::new();
    let mut excluded = 0usize;
    let mut anchors = Vec::new();
    let mut wanted = [BTreeSet::new(), BTreeSet::new()];
    let mut targets = [HashSet::new(), HashSet::new()];
    for change in &evidence.changes {
        if change.operation == "equal" {
            continue;
        }
        let refs = change.refs();
        if change.operation == "move" && order_preserved {
            let [Some(old), Some(new)] = refs else {
                bail!("Missing move references")
            };
            if units[0][old].same_contextual_content(&units[1][new]) {
                excluded += 1;
                continue;
            }
        }
        let op_index = operations.len();
        let mut operation = json!({
            "operation": change.operation,
            "operation_id": op_index,
            "old_ref": change.old_ref,
            "new_ref": change.new_ref,
        });
        if change.operation == "delete"
            && let Some(index) = change.old_ref
            && let Some(text) = optional_joined_text(&units[0][index])
        {
            let mut candidates = incoming
                .iter()
                .filter(|(_, larger)| text.len() < larger.len() && larger.contains(&text));
            if let Some((index, _)) = candidates.next()
                && candidates.next().is_none()
            {
                operation["retained_text_candidate_new_ref"] = json!(index);
                wanted[1].insert(*index);
            }
        }
        for side in 0..2 {
            if let Some(index) = refs[side] {
                include(&mut wanted[side], units[side], index);
                targets[side].insert(index);
            } else {
                let opposite = 1 - side;
                let position = refs[opposite].context("Missing score evidence reference")?;
                let pairs = anchors_by_ref[opposite];
                let before = pairs.partition_point(|pair| pair[opposite] < position);
                let after = pairs.partition_point(|pair| pair[opposite] <= position);
                let neighbors = before
                    .checked_sub(1)
                    .and_then(|i| pairs.get(i))
                    .into_iter()
                    .chain(pairs.get(after));
                if before == 0 && after == pairs.len() {
                    wanted[side]
                        .extend((0..units[side].len()).filter(|&i| !units[side][i].exporter_title));
                } else {
                    for pair in neighbors {
                        include(&mut wanted[side], units[side], pair[side]);
                        anchors.push(json!({
                            "operation": op_index,
                            "side": SIDES[side],
                            "anchor_ref": pair[side],
                            "opposite_ref": pair[opposite],
                        }));
                    }
                }
            }
        }
        operations.push(operation);
    }
    if operations.is_empty() {
        return Ok(Err("NO_SCOREABLE_OPERATIONS"));
    }
    let count = operations.len();
    let mut result = json!({
        "schema": "authored-model-diff-1",
        "operations": operations,
        "context_window_units_each_side": WINDOW,
        "paired_unit_order_preserved": order_preserved,
        "excluded_position_shift_moves_with_equal_normalized_content_and_context": excluded,
        "opposite_context_anchors": anchors,
        "source_body": {},
    });
    let mut partial = false;
    for side in 0..2 {
        let available = units[side]
            .iter()
            .filter(|unit| !unit.exporter_title)
            .count();
        let included = wanted[side].len();
        partial |= included < available;
        result["source_body"][SOURCE_SIDES[side]] = json!({
            "status": if available == 0 { "NO_TEXT" } else { "AVAILABLE" },
            "available_units": available,
            "included_units": included,
        });
        let mut projected = Vec::new();
        for &index in &wanted[side] {
            let unit = &units[side][index];
            if has_residue(&unit.text) {
                return Ok(Err("UNSAFE_OR_INCOMPLETE_EVIDENCE"));
            }
            projected.push(json!({
                "ref": index,
                "kind": unit.kind,
                "context": unit.context,
                "text": unit.text,
                "role": if targets[side].contains(&index) { "delta_target" } else { "context_only" },
            }));
        }
        result[SIDES[side]] = json!(projected);
    }
    result["context_is_partial"] = json!(partial);
    Ok(Ok(Packet {
        evidence: serde_json::to_string(&result)?,
        operations: count,
    }))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawReply {
    evaluation_status: String,
    #[serde(deserialize_with = "Deserialize::deserialize")]
    change_score: Option<u8>,
    checked_operations: Vec<usize>,
    changes: Vec<Claim>,
    reason: String,
    missing_evidence: Vec<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Claim {
    operation_id: usize,
    before: String,
    after: String,
    #[serde(deserialize_with = "Deserialize::deserialize")]
    ambiguity: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReplyValidation {
    Shape,
    OperationCoverage,
    Assessment,
    ClaimOperation,
    ClaimContent,
    Ambiguity,
    InsufficientInput,
}

impl ReplyValidation {
    pub fn reason_code(self) -> &'static str {
        match self {
            Self::InsufficientInput => "insufficient_input",
            _ => "invalid_response",
        }
    }
}

pub fn validate_reply(raw: &str, count: usize) -> Result<(u8, bool), ReplyValidation> {
    use ReplyValidation::*;

    let reply: RawReply = serde_json::from_str(raw).map_err(|_| Shape)?;
    if reply.checked_operations.len() != count
        || reply
            .checked_operations
            .iter()
            .copied()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .ne(0..count)
    {
        return Err(OperationCoverage);
    }
    if reply.reason.trim().is_empty() {
        return Err(Assessment);
    }
    if reply.evaluation_status == "insufficient_input" {
        if reply.change_score.is_some()
            || !reply.changes.is_empty()
            || reply.missing_evidence.is_empty()
            || reply
                .missing_evidence
                .iter()
                .any(|item| item.trim().is_empty())
        {
            return Err(Assessment);
        }
        return Err(InsufficientInput);
    }
    let score = reply
        .change_score
        .filter(|score| (1..=5).contains(score))
        .ok_or(Assessment)?;
    if reply.evaluation_status != "evaluated"
        || !reply.missing_evidence.is_empty()
        || reply.changes.is_empty() == (score >= 3)
    {
        return Err(Assessment);
    }
    let mut seen = HashSet::new();
    let mut ambiguous = false;
    let mut clear = false;
    for claim in reply.changes {
        if claim.operation_id >= count || !seen.insert(claim.operation_id) {
            return Err(ClaimOperation);
        }
        if claim.before.trim().is_empty()
            || claim.after.trim().is_empty()
            || claim
                .ambiguity
                .as_deref()
                .is_some_and(|text| text.trim().is_empty())
        {
            return Err(ClaimContent);
        }
        ambiguous |= claim.ambiguity.is_some();
        clear |= claim.ambiguity.is_none();
    }
    if (score == 3 && !ambiguous) || (score >= 4 && !clear) {
        return Err(Ambiguity);
    }
    Ok((score, ambiguous))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validation_distinguishes_duplicate_claims_from_missing_operation_coverage() {
        let mut reply = json!({
            "evaluation_status": "evaluated",
            "change_score": 4,
            "checked_operations": [0, 1],
            "changes": [
                {"operation_id": 0, "before": "15 days", "after": "10 days", "ambiguity": null}
            ],
            "reason": "The settlement period shortened.",
            "missing_evidence": []
        });
        assert_eq!(validate_reply(&reply.to_string(), 2), Ok((4, false)));
        let claim = reply["changes"][0].clone();
        reply["changes"].as_array_mut().unwrap().push(claim);
        assert_eq!(
            validate_reply(&reply.to_string(), 2),
            Err(ReplyValidation::ClaimOperation)
        );

        reply["checked_operations"] = json!([0, 0]);
        assert_eq!(
            validate_reply(&reply.to_string(), 2),
            Err(ReplyValidation::OperationCoverage)
        );
    }
}
