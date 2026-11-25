//! Decode and project an authored snapshot, keeping source coordinates and
//! explicit repair evidence attached to that projection.

use std::borrow::Cow;
use std::collections::HashMap;
use std::path::{Path, PathBuf, absolute};

use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};

use crate::input::{Document, Identity, MANIFEST_LIMIT, Reader, parse_utf8_json};
use crate::knowledge_units::{
    Unit, declared_marker_positions, extract_units, is_source_line_end, source_lines,
};

/// The repair replay and extraction stages are distinct so semantic reports
/// can retain verified repair evidence even when body extraction is unavailable.
pub(crate) struct BodyProjection<'a> {
    document: &'a Document,
    original: String,
    projected: Option<String>,
    repairs: Vec<Repair<'a>>,
}

impl<'a> BodyProjection<'a> {
    pub(crate) fn read(
        document: &'a Document,
        recovery: Option<&'a RecoveryRecord>,
        reader: &mut Reader,
    ) -> Result<Self, &'static str> {
        let original = String::from_utf8(reader.read(&document.path)?)
            .map_err(|_| "TEXT_ENCODING_UNSUPPORTED")?;
        let (projected, repairs) = match recovery {
            Some(record) => {
                let (text, repairs) = repair(&original, record, reader)?;
                (Some(text), repairs)
            }
            None => (None, Vec::new()),
        };
        Ok(Self {
            document,
            original,
            projected,
            repairs,
        })
    }

    pub(crate) fn recovery_evidence(&self, side: Side) -> impl Iterator<Item = Value> + '_ {
        self.repairs.iter().map(move |item| {
            json!({
                "side": side.label(), "byte_offset": item.byte_offset,
                "text": item.text, "source_line": item.source_line,
            })
        })
    }

    pub(crate) fn extract(self) -> Result<LocatedUnits, &'static str> {
        let projected = self.projected.as_deref().unwrap_or(&self.original);
        let scope = project_declared_scope(
            &self.original,
            projected,
            &self.repairs,
            &self.document.raw["body_scope"],
        )?;
        let explicit_legacy_repair = self
            .repairs
            .iter()
            .any(|repair| source_lines(repair.text, false).any(legacy_closer));
        let units =
            extract_document_body(self.document, projected, &scope, explicit_legacy_repair)?;
        let coordinates = (!self.repairs.is_empty())
            .then(|| coordinate_map(&self.original, projected, &self.repairs));
        Ok(LocatedUnits { units, coordinates })
    }
}

pub(crate) struct LocatedUnits {
    units: Vec<Unit>,
    coordinates: Option<Vec<usize>>,
}

impl LocatedUnits {
    pub(crate) fn evidence(&self) -> Vec<Value> {
        self.units
            .iter()
            .map(|unit| unit_evidence(unit, self.coordinates.as_deref()))
            .collect()
    }

    pub(crate) fn into_units(self) -> Vec<Unit> {
        self.units
    }
}

#[derive(Clone, Copy, Eq, Hash, PartialEq)]
pub(crate) enum Side {
    Baseline,
    Incoming,
}

impl Side {
    fn parse(value: &Value) -> Option<Self> {
        match value.as_str()? {
            "baseline" => Some(Self::Baseline),
            "incoming" => Some(Self::Incoming),
            _ => None,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::Baseline => "baseline",
            Self::Incoming => "incoming",
        }
    }
}

pub(crate) struct RecoveryRecord {
    source_copy: PathBuf,
    insertions: Vec<Value>,
}

pub(crate) type Recovery = HashMap<(Identity, Side), RecoveryRecord>;

pub(crate) fn load_recovery(path: Option<&Path>, reader: &mut Reader) -> Result<Recovery> {
    const INVALID: &str = "RECOVERY_MANIFEST_INVALID";
    let Some(path) = path else {
        return Ok(Recovery::new());
    };
    let bytes = reader
        .read_with_limit(path, MANIFEST_LIMIT)
        .map_err(|_| anyhow::anyhow!(INVALID))?;
    let value = parse_utf8_json(&bytes).map_err(|_| anyhow::anyhow!(INVALID))?;
    ensure!(
        value["schema_version"] == "mdcompare-body-recovery-1",
        INVALID
    );
    let root = path.parent().context(INVALID)?;
    let mut recovery = Recovery::new();
    for row in value["documents"].as_array().context(INVALID)? {
        let field = |key| {
            row[key]
                .as_str()
                .filter(|value| !value.is_empty())
                .context(INVALID)
        };
        let side = Side::parse(&row["side"]).context(INVALID)?;
        let identity = Identity {
            knowledge_base: field("knowledge_base")?.to_owned(),
            document_id: field("document_id")?.to_owned(),
        };
        let record = RecoveryRecord {
            source_copy: root.join(field("source_copy")?),
            insertions: row["insertions"].as_array().context(INVALID)?.clone(),
        };
        ensure!(recovery.insert((identity, side), record).is_none(), INVALID);
    }
    Ok(recovery)
}

struct Repair<'a> {
    byte_offset: usize,
    text: &'a str,
    source_line: usize,
}

fn line_offsets(text: &str) -> Vec<usize> {
    std::iter::once(0)
        .chain(source_lines(text, true).scan(0, |offset, line| {
            *offset += line.len();
            Some(*offset)
        }))
        .collect()
}

fn legacy_closer(line: &str) -> bool {
    matches!(line, "> 转写说明：" | "> **转写说明：**" | "-->")
}

fn repair<'a>(
    original: &str,
    record: &'a RecoveryRecord,
    reader: &mut Reader,
) -> std::result::Result<(String, Vec<Repair<'a>>), &'static str> {
    const INVALID: &str = "RECOVERY_INSERTION_INVALID";
    if reader.read(&record.source_copy)? != original.as_bytes() {
        return Err("RECOVERY_SOURCE_DRIFT");
    }
    let mut offsets = line_offsets(original);
    if !original.is_empty() && !original.ends_with(is_source_line_end) {
        offsets.pop();
    }
    let mut repairs = Vec::new();
    let mut previous = 0;
    for entry in &record.insertions {
        let byte_offset = entry["byte_offset"]
            .as_u64()
            .and_then(|value| usize::try_from(value).ok())
            .ok_or(INVALID)?;
        let text = entry["text"]
            .as_str()
            .filter(|text| text.ends_with('\n'))
            .ok_or(INVALID)?;
        if byte_offset < previous
            || !original.is_char_boundary(byte_offset)
            || !source_lines(text, false).all(|line| {
                matches!(
                    line,
                    "" | "<!-- mdocr:ocr-begin -->" | "<!-- mdocr:ocr-end -->"
                ) || legacy_closer(line)
            })
        {
            return Err(INVALID);
        }
        previous = byte_offset;
        let source_line = offsets.partition_point(|&offset| offset <= byte_offset);
        repairs.push(Repair {
            byte_offset,
            text,
            source_line,
        });
    }
    let mut projected = String::new();
    let mut position = 0;
    for repair in &repairs {
        projected.push_str(&original[position..repair.byte_offset]);
        projected.push_str(repair.text);
        position = repair.byte_offset;
    }
    projected.push_str(&original[position..]);
    Ok((projected, repairs))
}

fn project_declared_scope<'a>(
    original: &str,
    projected: &str,
    repairs: &[Repair<'_>],
    scope: &'a Value,
) -> std::result::Result<Cow<'a, Value>, &'static str> {
    if repairs.is_empty() || scope.is_null() {
        return Ok(Cow::Borrowed(scope));
    }
    let markers = declared_marker_positions(original, scope)?;
    if markers.is_empty() {
        return Ok(Cow::Borrowed(scope));
    }
    let mut inserted = 0;
    let shifts: Vec<_> = repairs
        .iter()
        .map(|repair| {
            inserted += repair.text.len();
            (repair.byte_offset, inserted)
        })
        .collect();
    let offsets = line_offsets(projected);
    let locate = |marker| {
        let count = shifts.partition_point(|&(offset, _)| offset <= marker);
        let shift = count.checked_sub(1).map_or(0, |index| shifts[index].1);
        offsets.partition_point(|&offset| offset <= marker + shift)
    };
    let mut projection = scope.clone();
    let regions = projection["regions"]
        .as_array_mut()
        .expect("validated scope");
    for (region, (opening, closing)) in regions.iter_mut().zip(markers) {
        region["start_line"] = json!(locate(opening));
        region["end_line"] = json!(locate(closing));
    }
    Ok(Cow::Owned(projection))
}

fn coordinate_map(original: &str, projected: &str, repairs: &[Repair<'_>]) -> Vec<usize> {
    let original_offsets = line_offsets(original);
    let mut inserted = 0;
    let mut inserts = repairs.iter().peekable();
    line_offsets(projected)
        .into_iter()
        .map(|position| {
            let mut source = position - inserted;
            while let Some(next) = inserts.peek() {
                let start = next.byte_offset + inserted;
                if position < start {
                    break;
                }
                if position < start + next.text.len() {
                    source = next.byte_offset;
                    break;
                }
                inserted += next.text.len();
                inserts.next();
                source = position - inserted;
            }
            original_offsets
                .partition_point(|&offset| offset <= source)
                .min(original_offsets.len().saturating_sub(1).max(1))
        })
        .collect()
}

fn unit_evidence(unit: &Unit, coordinates: Option<&[usize]>) -> Value {
    let mut evidence = json!(unit);
    if let Some(coordinates) = coordinates {
        evidence["projected_start_line"] = json!(unit.start_line);
        evidence["projected_end_line"] = json!(unit.end_line);
        evidence["start_line"] = json!(coordinates[unit.start_line - 1]);
        evidence["end_line"] = json!(coordinates[unit.end_line - 1]);
    }
    evidence
}

fn extract_document_body(
    document: &Document,
    text: &str,
    body_scope: &Value,
    explicit_legacy_repair: bool,
) -> Result<Vec<Unit>, &'static str> {
    let name = document.metadata["name"].as_str().unwrap_or_default();
    let normalized = text.replace("\r\n", "\n").replace('\r', "\n");
    let mut lines = source_lines(&normalized, true);
    let mut header = None;
    if let (Some(title), Some(origin)) = (lines.next(), lines.next()) {
        let origin_text = origin.trim_end_matches('\n');
        let origin_prefix = format!("doc_id: {} · book: ", document.identity.document_id);
        static EXPORTER: std::sync::LazyLock<regex::Regex> = std::sync::LazyLock::new(|| {
            regex::Regex::new(r"^.+ \(book_id [0-9]+\) · word_count: [0-9]+$").unwrap()
        });
        if !name.is_empty()
            && title.trim_end_matches('\n').strip_prefix("# ") == Some(name)
            && origin_text
                .strip_prefix(&origin_prefix)
                .is_some_and(|rest| EXPORTER.is_match(rest))
        {
            header = Some((title.len(), title.len() + origin_text.len()));
        }
    }
    drop(lines);
    let mut normalized = normalized;
    if let Some((start, end)) = header {
        normalized.replace_range(start..end, "");
    }
    let mut units = extract_units(&normalized, body_scope, explicit_legacy_repair)?;
    if let Some((title, rest)) = units
        .split_first_mut()
        .filter(|(title, _)| title.kind == "heading" && title.start_line == 1 && title.text == name)
    {
        title.exporter_title = header.is_some();
        for unit in rest {
            if unit.context.first() == Some(&title.key) {
                unit.context.remove(0);
            }
        }
    }
    Ok(units)
}

pub(crate) fn source_evidence(document: Option<&Document>) -> Result<Value> {
    document.map_or(Ok(Value::Null), |doc| {
        Ok(json!({
            "path": absolute(&doc.path)?,
            "content_kind": doc.content_kind(),
            "metadata": doc.metadata,
            "version": doc.raw["provenance"]["version"],
        }))
    })
}

pub(crate) fn unit_reference(document: &Document, unit: &Unit) -> Value {
    json!({
        "knowledge_base": document.identity.knowledge_base, "document_id": document.identity.document_id,
        "start_line": unit.start_line, "end_line": unit.end_line,
    })
}
