use std::collections::{HashMap, HashSet};

use serde::Serialize;
use serde_json::Value;

macro_rules! rx {
    ($pattern:literal) => {{
        static REGEX: std::sync::LazyLock<regex::Regex> =
            std::sync::LazyLock::new(|| regex::Regex::new($pattern).expect("valid body pattern"));
        &*REGEX
    }};
}

mod body_view;
mod formatting;
pub(crate) use body_view::declared_marker_positions;
pub use formatting::{prose_key, table_key};

#[derive(Clone, Debug, Serialize)]
pub struct Unit {
    pub kind: &'static str,
    pub text: String,
    pub key: String,
    pub context: Vec<String>,
    pub start_line: usize,
    pub end_line: usize,
    pub exporter_title: bool,
}

impl Unit {
    pub fn exact_key(&self) -> (&'static str, &[String], &str) {
        (self.kind, &self.context, &self.key)
    }
}

fn fence() -> &'static regex::Regex {
    rx!(r"^ {0,3}(`{3,}|~{3,})(.*)$")
}

fn fence_end(line: &str, delimiter: &str) -> bool {
    let trim = line.trim_start_matches(' ');
    line.len() - trim.len() <= 3
        && trim
            .trim_end()
            .bytes()
            .all(|value| value == delimiter.as_bytes()[0])
        && trim.trim_end().len() >= delimiter.len()
}

fn heading() -> &'static regex::Regex {
    rx!(r"^ {0,3}(#{1,6})[ \t]+(.*)$")
}

fn heading_text(text: &str) -> &str {
    let text = text.trim_end_matches([' ', '\t']);
    // A pure hash run following the opening delimiter is an empty heading.
    if text.bytes().all(|byte| byte == b'#') {
        return "";
    }
    let closing = rx!(r"[ \t]+#+$");
    closing
        .find(text)
        .map_or(text, |matched| &text[..matched.start()])
}

fn ordered() -> &'static regex::Regex {
    rx!(r"^\s*\d+[.)]\s+")
}

fn bullet() -> &'static regex::Regex {
    rx!(r"^(\s*)[-+*]\s+(.*)$")
}

fn table_separator() -> &'static regex::Regex {
    rx!(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
}

fn workflow() -> &'static regex::Regex {
    rx!(r"(?i)流程|步骤|顺序|\b(?:workflow|procedure|steps|sequence)\b")
}

fn condition() -> &'static regex::Regex {
    rx!(
        r"(?i)条件|适用|仅限|前提|除非|针对|对于|如果|主体|账户|企业|付款方|收款方|商户|角色|规则|当.*时|\b(?:if|when|unless|only|applies|applicable|for|scope|subject|role|actor|approver|payer|payee|account|company|enterprise|merchant|MerchantId)\b"
    )
}

fn is_indented(line: &str) -> bool {
    source_indent(line) >= 4
}

fn is_html_table(line: &str) -> bool {
    rx!(r"(?i)^\s*<table\b").is_match(line)
}

fn blank(line: &str) -> bool {
    line.trim().is_empty()
}

pub(crate) fn is_source_line_end(ch: char) -> bool {
    matches!(
        ch,
        '\r' | '\n'
            | '\u{b}'
            | '\u{c}'
            | '\u{1c}'
            | '\u{1d}'
            | '\u{1e}'
            | '\u{85}'
            | '\u{2028}'
            | '\u{2029}'
    )
}

pub fn source_lines(text: &str, keep_end: bool) -> impl Iterator<Item = &str> {
    let mut rest = text;
    std::iter::from_fn(move || {
        if rest.is_empty() {
            return None;
        }
        let (position, ch) = match rest.char_indices().find(|&(_, ch)| is_source_line_end(ch)) {
            Some(found) => found,
            None => return Some(std::mem::take(&mut rest)),
        };
        let mut end = position + ch.len_utf8();
        if ch == '\r' && rest.as_bytes().get(end) == Some(&b'\n') {
            end += 1;
        }
        let line = &rest[..if keep_end { end } else { position }];
        rest = &rest[end..];
        Some(line)
    })
}

fn tab_indent(prefix: &str) -> usize {
    prefix.chars().fold(0, |column, ch| {
        if ch == '\t' {
            (column / 4 + 1) * 4
        } else {
            column + 1
        }
    })
}

fn source_indent(line: &str) -> usize {
    let prefix = line.trim_start_matches([' ', '\t']);
    tab_indent(&line[..line.len() - prefix.len()])
}

fn ordered_key(lines: &[&str], original: &[&str]) -> String {
    let base = source_indent(original[0]) as isize;
    let mut items = Vec::<(isize, String)>::new();
    for (&line, &raw) in lines.iter().zip(original) {
        if ordered().is_match(line) || bullet().is_match(line) {
            let level = source_indent(raw) as isize - base;
            items.push((level, line.trim().to_owned()));
        } else if let Some((_, item)) = items.last_mut() {
            item.push(' ');
            item.push_str(line.trim());
        }
    }
    for (_, item) in &mut items {
        *item = prose_key(item);
    }
    serde_json::to_string(&items).expect("ordered items are serializable")
}

struct Extractor {
    units: Vec<Unit>,
    headings: Vec<(usize, String)>,
    bullet_parents: Vec<(usize, String)>,
    leadin: String,
    list_scope: String,
    list_indent: Option<usize>,
    remaining_context_bytes: usize,
}

impl Extractor {
    fn append(
        &mut self,
        kind: &'static str,
        start: usize,
        end: usize,
        text: String,
        key: String,
    ) -> Result<(), &'static str> {
        if key.is_empty() {
            return Ok(());
        }
        let include_list_scope = !self.list_scope.is_empty()
            && self.list_scope != self.leadin
            && matches!(kind, "bullet" | "ordered" | "table" | "code");
        let parents = if kind == "bullet" {
            self.bullet_parents.as_slice()
        } else {
            &[]
        };
        let context_parts = self
            .headings
            .iter()
            .map(|(_, key)| key)
            .chain((!self.leadin.is_empty()).then_some(&self.leadin))
            .chain(include_list_scope.then_some(&self.list_scope))
            .chain(parents.iter().map(|(_, key)| key));
        // Context is repeated in the semantic evidence. Bound that expansion
        // before allocating, including when the original text is small.
        self.remaining_context_bytes = context_parts
            .clone()
            .try_fold(self.remaining_context_bytes, |remaining, key| {
                remaining.checked_sub(key.len() + size_of::<String>())
            })
            .ok_or("BODY_CONTEXT_BUDGET_EXCEEDED")?;
        let context = context_parts.cloned().collect();
        self.units.push(Unit {
            kind,
            key,
            context,
            text,
            start_line: start + 1,
            end_line: end,
            exporter_title: false,
        });
        Ok(())
    }

    fn block(
        &mut self,
        kind: &'static str,
        start: usize,
        end: usize,
        lines: &[&str],
        original: &[&str],
    ) -> Result<(), &'static str> {
        let text = lines[start..end].join("\n");
        let key = match kind {
            "code" => text.trim_end_matches('\n').to_owned(),
            "table" => table_key(&text),
            "ordered" => ordered_key(&lines[start..end], &original[start..end]),
            _ => unreachable!("block kind"),
        };
        self.append(kind, start, end, text, key)?;
        self.list_indent = (kind == "ordered").then(|| {
            let first = original[start];
            ordered().find(first).map_or_else(
                || source_indent(first) + 2,
                |matched| tab_indent(matched.as_str()),
            )
        });
        self.list_scope = self
            .units
            .last()
            .map_or_else(String::new, |unit| unit.key.clone());
        Ok(())
    }
}

pub fn extract_units(
    text: &str,
    body_scope: &Value,
    explicit_legacy_repair: bool,
) -> Result<Vec<Unit>, &'static str> {
    let (body, ignored) = body_view::body_view(text, body_scope, explicit_legacy_repair)?;
    let lines: Vec<_> = source_lines(&body, false).collect();
    let original: Vec<_> = source_lines(text, false).collect();
    let mut output = Extractor {
        units: Vec::new(),
        headings: Vec::new(),
        bullet_parents: Vec::new(),
        leadin: String::new(),
        list_scope: String::new(),
        list_indent: None,
        remaining_context_bytes: crate::input::MANIFEST_LIMIT as usize,
    };
    let mut index = 0;
    while let Some(&line) = lines.get(index) {
        let start = index;
        index += 1;
        if blank(line) {
            continue;
        }
        if let Some(m) = heading().captures(line) {
            let level = m[1].len();
            while output.headings.last().is_some_and(|(n, _)| *n >= level) {
                output.headings.pop();
            }
            output.leadin.clear();
            output.list_scope.clear();
            output.list_indent = None;
            let text = heading_text(&m[2]);
            let key = prose_key(text);
            output.append("heading", start, index, text.to_owned(), key.clone())?;
            output.headings.push((level, key));
            output.bullet_parents.clear();
            continue;
        }
        if let Some(m) = fence().captures(line) {
            while let Some(next) = lines.get(index) {
                index += 1;
                if fence_end(next, &m[1]) {
                    break;
                }
            }
            output.block("code", start, index, &lines, &original)?;
            continue;
        }
        if is_indented(original[start]) {
            let indent = source_indent(original[start]);
            let in_container =
                |content_indent| indent >= content_indent && indent < content_indent + 4;
            let nested_item = (ordered().is_match(line) || bullet().is_match(line))
                && (output.list_indent.is_some_and(in_container)
                    || output
                        .bullet_parents
                        .iter()
                        .any(|(parent, _)| in_container(parent + 2)));
            if !nested_item {
                while original.get(index).is_some_and(|next| is_indented(next)) {
                    index += 1;
                }
                output.block("code", start, index, &lines, &original)?;
                output.bullet_parents.clear();
                continue;
            }
        }
        if table_start(&lines, start) {
            index += 1;
            while lines
                .get(index)
                .is_some_and(|s| s.contains('|') && !blank(s))
            {
                index += 1;
            }
            output.block("table", start, index, &lines, &original)?;
            continue;
        }
        if is_html_table(line) {
            while index < lines.len() && !rx!(r"(?i)</table\s*>").is_match(lines[index - 1]) {
                index += 1;
            }
            output.block("table", start, index, &lines, &original)?;
            continue;
        }
        if ordered().is_match(line) {
            index = list_end(&lines, &original, index, ordered());
            output.block("ordered", start, index, &lines, &original)?;
            continue;
        }
        if let Some(m) = bullet().captures(line) {
            if output
                .headings
                .iter()
                .any(|(_, key)| workflow().is_match(key))
                || workflow().is_match(&output.list_scope)
                || workflow().is_match(&output.leadin)
            {
                index = list_end(&lines, &original, index, bullet());
                output.block("ordered", start, index, &lines, &original)?;
                continue;
            }
            let indent = source_indent(original[start]);
            while output
                .bullet_parents
                .last()
                .is_some_and(|(n, _)| *n >= indent)
            {
                output.bullet_parents.pop();
            }
            let mut value = m[2].to_owned();
            while let Some(next) = lines.get(index).filter(|next| {
                !blank(next)
                    && source_indent(original[index]) >= indent + 2
                    && !bullet().is_match(next)
                    && !ordered().is_match(next)
            }) {
                index += 1;
                value.push('\n');
                value.push_str(next.trim());
            }
            let key = prose_key(&value);
            output.append("bullet", start, index, value, key.clone())?;
            output.list_indent = None;
            output.bullet_parents.push((indent, key));
            continue;
        }
        while let Some(&next) = lines.get(index) {
            let excluded = ignored.contains(&index);
            if (!excluded && (blank(next) || block_start(next, original[index])))
                || table_start(&lines, index)
            {
                break;
            }
            index += 1;
        }
        let value = lines[start..index].join("\n");
        let is_scope = value.trim_end().ends_with([':', '：'])
            || condition().is_match(&value)
            || workflow().is_match(&value);
        let key = prose_key(&value);
        let scope = if is_scope { key.clone() } else { String::new() };
        output.append("paragraph", start, index, value, key)?;
        output.list_scope = scope;
        output.list_indent = None;
        if is_scope {
            output.leadin.clone_from(&output.list_scope);
        }
        output.bullet_parents.clear();
    }
    Ok(output.units)
}

fn table_start(lines: &[&str], index: usize) -> bool {
    lines[index].contains('|')
        && lines
            .get(index + 1)
            .is_some_and(|next| table_separator().is_match(next))
}

fn block_start(line: &str, original: &str) -> bool {
    heading().is_match(line)
        || fence().is_match(line)
        || bullet().is_match(line)
        || ordered().is_match(line)
        || is_indented(original)
        || is_html_table(line)
}

fn list_end(lines: &[&str], original: &[&str], mut index: usize, marker: &regex::Regex) -> usize {
    while lines.get(index).is_some_and(|next| {
        !blank(next) && (marker.is_match(next) || source_indent(original[index]) > 0)
    }) {
        index += 1;
    }
    index
}

pub const KINDS: [&str; 5] = ["reuse", "add", "revise", "old_only_review", "unresolved"];

#[derive(Clone, Copy)]
pub struct Match {
    pub kind: &'static str,
    pub old: Option<usize>,
    pub new: Option<usize>,
    pub basis: &'static str,
    pub similarity: Option<f64>,
}

pub struct Alignment {
    pub items: Vec<Match>,
    pub complete: bool,
}

fn grams(text: &str) -> HashSet<Vec<char>> {
    let chars: Vec<_> = text.chars().collect();
    if chars.len() < 3 {
        HashSet::from([chars])
    } else {
        chars.windows(3).map(<[char]>::to_vec).collect()
    }
}

#[derive(Clone, Copy, Default)]
struct Candidates {
    best: Option<(f64, usize)>,
    runner_up: Option<f64>,
}

impl Candidates {
    fn consider(&mut self, score: f64, index: usize) {
        if self.best.is_none_or(|(best, _)| score > best) {
            self.runner_up = self.best.map(|(best, _)| best);
            self.best = Some((score, index));
        } else if self.runner_up.is_none_or(|second| score > second) {
            self.runner_up = Some(score);
        }
    }

    fn ambiguous(&self) -> bool {
        self.best
            .zip(self.runner_up)
            .is_some_and(|((best, _), second)| best - second < 0.03)
    }
}

pub fn align_units(old: &[&Unit], new: &[&Unit], max_work: usize) -> Alignment {
    let mut exact = HashMap::<_, Vec<usize>>::new();
    for (index, unit) in old.iter().enumerate() {
        exact.entry(unit.exact_key()).or_default().push(index);
    }
    let mut used = HashSet::new();
    let mut covered = HashSet::new();
    let mut items: Vec<_> = new
        .iter()
        .enumerate()
        .map(|(index, unit)| {
            exact.get(&unit.exact_key()).map(|matches| {
                if covered.insert(matches[0]) {
                    used.extend(matches.iter().copied());
                }
                Match {
                    kind: "reuse",
                    old: Some(matches[0]),
                    new: Some(index),
                    basis: "NORMALIZED_BODY_EQUIVALENT",
                    similarity: Some(1.0),
                }
            })
        })
        .collect();
    let mut groups = HashMap::<_, Vec<usize>>::new();
    let old_lengths: Vec<_> = old.iter().map(|unit| unit.key.chars().count()).collect();
    for (index, unit) in old.iter().enumerate().filter(|(i, _)| !used.contains(i)) {
        groups
            .entry((unit.kind, unit.context.as_slice()))
            .or_default()
            .push(index);
    }
    let mut cache = HashMap::new();
    let mut remaining = max_work;
    let mut exhausted = false;
    let mut incomplete_groups = HashSet::new();
    let mut by_old = vec![Candidates::default(); old.len()];
    let mut by_new = vec![Candidates::default(); new.len()];
    for (index, item) in items.iter().enumerate() {
        if item.is_some() {
            continue;
        }
        let unit = new[index];
        let group_key = (unit.kind, unit.context.as_slice());
        let Some(group) = groups.get(&group_key) else {
            continue;
        };
        if exhausted {
            incomplete_groups.insert(group_key);
            continue;
        }
        let after_size = unit.key.chars().count();
        let mut after = None;
        for &previous in group {
            // Reserve the scan and the expensive grams construction before
            // allocating either candidate. Every traversal consumes work.
            let cost = old_lengths[previous]
                .saturating_add(after_size)
                .saturating_add(1);
            let Some(work) = remaining.checked_sub(cost) else {
                exhausted = true;
                incomplete_groups.insert(group_key);
                break;
            };
            remaining = work;
            let before = cache
                .entry(previous)
                .or_insert_with(|| grams(&old[previous].key));
            let after = after.get_or_insert_with(|| grams(&unit.key));
            let similarity = (2 * before.intersection(after).count()) as f64
                / (before.len() + after.len()) as f64;
            if similarity >= 0.6 {
                by_old[previous].consider(similarity, index);
                by_new[index].consider(similarity, previous);
            }
        }
    }
    // A revision is uniquely strongest only if both sides chose each
    // other. Do not consume an old occurrence until competitors are known.
    let mut revisions = HashMap::new();
    for (index, candidates) in by_new.iter().enumerate() {
        if incomplete_groups.contains(&(new[index].kind, new[index].context.as_slice())) {
            continue;
        }
        if let Some((score, previous)) = candidates.best
            && !candidates.ambiguous()
            && !by_old[previous].ambiguous()
            && by_old[previous]
                .best
                .is_some_and(|(_, preferred)| preferred == index)
        {
            used.insert(previous);
            revisions.insert(index, (previous, score));
        }
    }

    for (index, item) in items.iter_mut().enumerate() {
        if item.is_some() {
            continue;
        }
        let candidates = &by_new[index];
        let group_key = (new[index].kind, new[index].context.as_slice());
        let (kind, previous, basis, similarity) = if incomplete_groups.contains(&group_key) {
            ("unresolved", None, "BODY_MATCH_BUDGET_EXCEEDED", None)
        } else if let Some(&(previous, score)) = revisions.get(&index) {
            (
                "revise",
                Some(previous),
                "SIMILAR_BODY_REVISION_CANDIDATE",
                Some(score),
            )
        } else if let Some((score, previous)) = candidates.best {
            if candidates.ambiguous() || by_old[previous].ambiguous() || !used.contains(&previous) {
                ("unresolved", None, "AMBIGUOUS_BODY_ALIGNMENT", Some(score))
            } else {
                ("add", None, "NO_MATCHED_BASELINE_BODY_UNIT", None)
            }
        } else {
            ("add", None, "NO_MATCHED_BASELINE_BODY_UNIT", None)
        };
        *item = Some(Match {
            kind,
            old: previous,
            new: Some(index),
            basis,
            similarity,
        });
    }
    let complete = !exhausted;
    let mut items: Vec<_> = items
        .into_iter()
        .map(|item| item.expect("all new units aligned"))
        .collect();
    items.extend(
        (0..old.len())
            .filter(|index| !used.contains(index))
            .map(|index| Match {
                kind: "old_only_review",
                old: Some(index),
                new: None,
                basis: "OLD_BODY_NOT_OBSERVED",
                similarity: None,
            }),
    );
    Alignment { items, complete }
}

#[cfg(test)]
mod tests {
    use super::{align_units, extract_units};
    use serde_json::Value;

    #[test]
    fn unicode_source_separators_preserve_headings_and_unit_locations() {
        let units =
            extract_units("# Scope\u{2028}- limit\u{85}- refund", &Value::Null, false).unwrap();
        let actual: Vec<_> = units
            .iter()
            .map(|unit| (unit.kind, unit.key.as_str(), unit.start_line, unit.end_line))
            .collect();
        assert_eq!(
            actual,
            [
                ("heading", "Scope", 1, 1),
                ("bullet", "limit", 2, 2),
                ("bullet", "refund", 3, 3)
            ]
        );
    }

    #[test]
    fn image_mask_padding_does_not_make_authored_prose_code() {
        for text in [
            "![diagram](old.png) Keep this policy.",
            "![diagram](longer.png) Keep this policy.",
        ] {
            let units = extract_units(text, &Value::Null, false).unwrap();
            assert_eq!(units.len(), 1);
            assert_eq!(units[0].kind, "paragraph");
            assert_eq!(units[0].key, "Keep this policy.");
        }
    }

    fn authored(text: &str) -> Vec<super::Unit> {
        extract_units(text, &Value::Null, false).unwrap()
    }

    #[test]
    fn closing_hashes_do_not_erase_a_heading_hash() {
        let language = authored("# C#\n\nUse this language.");
        let letter = authored("# C\n\nUse this language.");
        let closed = authored("# C# ###\n\nUse this language.");
        assert_ne!(language[0].exact_key(), letter[0].exact_key());
        assert_ne!(language[1].exact_key(), letter[1].exact_key());
        assert_eq!(language[0].exact_key(), closed[0].exact_key());
    }

    #[test]
    fn indented_root_structure_is_code_but_a_nested_bullet_is_a_list_item() {
        for text in ["    - allow", "    1. allow", "    <table>allow</table>"] {
            assert_eq!(authored(text)[0].kind, "code");
        }
        let nested = authored("- parent\n    - child");
        assert_eq!(nested.len(), 2);
        assert_eq!(nested[1].kind, "bullet");
        assert_eq!(nested[1].context, ["parent"]);
    }

    #[test]
    fn removed_image_padding_does_not_join_a_list_item_to_a_paragraph() {
        for marker in ["-", "1."] {
            let plain = authored(&format!("{marker} limit\nKeep this policy."));
            let illustrated = authored(&format!(
                "{marker} limit\n![diagram](old.png) Keep this policy."
            ));
            assert_eq!(plain.len(), 2);
            assert_eq!(illustrated.len(), 2);
            for (plain_unit, illustrated_unit) in plain.iter().zip(&illustrated) {
                assert_eq!(plain_unit.exact_key(), illustrated_unit.exact_key());
            }
        }
    }

    #[test]
    fn ordered_keys_preserve_item_boundaries_and_nesting() {
        let two = authored("1. Require approval\n2. Release funds");
        let one = authored("1. Require approval 2. Release funds");
        let nested = authored("1. Require approval\n    2. Release funds");
        let wrapped = authored("1. Require\n   approval\n2. Release funds");
        assert_ne!(two[0].exact_key(), one[0].exact_key());
        assert_ne!(two[0].exact_key(), nested[0].exact_key());
        assert_eq!(two[0].exact_key(), wrapped[0].exact_key());
    }

    #[test]
    fn loose_workflow_lists_remain_order_sensitive() {
        let prior = authored("Procedure:\n\n- Approve\n\n- Validate\n\n- Release");
        let incoming = authored("Procedure:\n\n- Approve\n\n- Release\n\n- Validate");
        assert!(prior[1..].iter().all(|unit| unit.kind == "ordered"));
        let alignment = align_units(
            &prior.iter().collect::<Vec<_>>(),
            &incoming.iter().collect::<Vec<_>>(),
            10_000,
        );
        assert!(alignment.items.iter().any(|item| item.kind != "reuse"));
    }

    #[test]
    fn revision_competition_is_resolved_on_both_sides() {
        let old = authored("abcdefghijk");
        for incoming in ["abcdefghijx\n\nabcdefghijy", "abcdefghijy\n\nabcdefghijx"] {
            let new = authored(incoming);
            let alignment = align_units(&[&old[0]], &new.iter().collect::<Vec<_>>(), 10_000);
            assert_eq!(
                alignment.items[..2]
                    .iter()
                    .map(|item| item.kind)
                    .collect::<Vec<_>>(),
                ["unresolved", "unresolved"]
            );
        }
        let new = authored("abcdefghixx\n\nabcdefghijx");
        let alignment = align_units(&[&old[0]], &[&new[0], &new[1]], 10_000);
        assert_eq!(alignment.items[0].kind, "add");
        assert_eq!(alignment.items[1].kind, "revise");
    }

    #[test]
    fn alignment_budget_cannot_publish_a_partial_group_as_unique() {
        let old = authored("abcdefghijk");
        let new = authored("abcdefghijx\n\nabcdefghijy");
        let alignment = align_units(&[&old[0]], &[&new[0], &new[1]], 24);
        assert!(!alignment.complete);
        assert_eq!(
            alignment.items[..2]
                .iter()
                .map(|item| item.basis)
                .collect::<Vec<_>>(),
            ["BODY_MATCH_BUDGET_EXCEEDED", "BODY_MATCH_BUDGET_EXCEEDED"]
        );
    }

    #[test]
    fn repeated_scope_is_bounded_before_evidence_expansion() {
        let text = format!(
            "{}:\n\n{}",
            "scope ".repeat(170_000),
            "- limit\n".repeat(24)
        );
        assert_eq!(
            extract_units(&text, &Value::Null, false).unwrap_err(),
            "BODY_CONTEXT_BUDGET_EXCEEDED"
        );
    }
}
