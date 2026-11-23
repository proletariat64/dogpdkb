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
    rx!(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
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
    line.starts_with("    ") || line.starts_with('\t')
}

fn is_html_table(line: &str) -> bool {
    rx!(r"(?i)^\s*<table\b").is_match(line)
}

fn blank(line: &str) -> bool {
    line.trim().is_empty()
}

fn is_source_line_end(ch: char) -> bool {
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

struct Extractor {
    units: Vec<Unit>,
    headings: Vec<(usize, String)>,
    bullet_parents: Vec<(usize, String)>,
    leadin: String,
    list_scope: String,
}

impl Extractor {
    fn append(&mut self, kind: &'static str, start: usize, end: usize, text: String) {
        let key = match kind {
            "code" => text.trim_end_matches('\n').to_owned(),
            "table" => table_key(&text),
            _ => prose_key(&text),
        };
        if key.is_empty() {
            return;
        }
        let mut context: Vec<_> = self.headings.iter().map(|(_, key)| key.clone()).collect();
        if !self.leadin.is_empty() {
            context.push(self.leadin.clone());
        }
        if !self.list_scope.is_empty()
            && self.list_scope != self.leadin
            && matches!(kind, "bullet" | "ordered" | "table" | "code")
        {
            context.push(self.list_scope.clone());
        }
        if kind == "bullet" {
            context.extend(self.bullet_parents.iter().map(|(_, key)| key.clone()));
        }
        self.units.push(Unit {
            kind,
            key,
            context,
            text,
            start_line: start + 1,
            end_line: end,
            exporter_title: false,
        });
    }

    fn block(&mut self, kind: &'static str, start: usize, end: usize, lines: &[&str]) {
        self.append(kind, start, end, lines[start..end].join("\n"));
        self.list_scope = self
            .units
            .last()
            .map_or_else(String::new, |unit| unit.key.clone());
    }
}

pub fn extract_units(text: &str, body_scope: &Value) -> Result<Vec<Unit>, &'static str> {
    let (body, ignored) = body_view::body_view(text, body_scope)?;
    let lines: Vec<_> = source_lines(&body, false).collect();
    let mut output = Extractor {
        units: Vec::new(),
        headings: Vec::new(),
        bullet_parents: Vec::new(),
        leadin: String::new(),
        list_scope: String::new(),
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
            output.append("heading", start, index, m[2].to_owned());
            output.headings.push((level, prose_key(&m[2])));
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
            output.block("code", start, index, &lines);
            continue;
        }
        if table_start(&lines, start) {
            index += 1;
            while lines
                .get(index)
                .is_some_and(|s| s.contains('|') && !blank(s))
            {
                index += 1;
            }
            output.block("table", start, index, &lines);
            continue;
        }
        if is_html_table(line) {
            while index < lines.len() && !rx!(r"(?i)</table\s*>").is_match(lines[index - 1]) {
                index += 1;
            }
            output.block("table", start, index, &lines);
            continue;
        }
        if ordered().is_match(line) {
            index = list_end(&lines, index, ordered());
            output.block("ordered", start, index, &lines);
            continue;
        }
        if let Some(m) = bullet().captures(line) {
            let heading_scope = output
                .headings
                .iter()
                .map(|(_, key)| key.as_str())
                .collect::<Vec<_>>()
                .join(" ");
            if workflow().is_match(&format!("{} {}", heading_scope, output.list_scope)) {
                index = list_end(&lines, index, bullet());
                output.block("ordered", start, index, &lines);
                continue;
            }
            let indent = tab_indent(&m[1]);
            while output
                .bullet_parents
                .last()
                .is_some_and(|(n, _)| *n >= indent)
            {
                output.bullet_parents.pop();
            }
            let mut value = m[2].to_owned();
            let required_indent = " ".repeat(indent + 2);
            while let Some(next) = lines.get(index).filter(|next| {
                !blank(next)
                    && next.starts_with(&required_indent)
                    && !bullet().is_match(next)
                    && !ordered().is_match(next)
            }) {
                index += 1;
                value.push('\n');
                value.push_str(next.trim());
            }
            let key = prose_key(&value);
            output.append("bullet", start, index, value);
            output.bullet_parents.push((indent, key));
            continue;
        }
        if is_indented(line) {
            while lines.get(index).is_some_and(|next| is_indented(next)) {
                index += 1;
            }
            output.block("code", start, index, &lines);
            continue;
        }
        while let Some(&next) = lines.get(index) {
            let excluded = ignored.contains(&index);
            if (!excluded && (blank(next) || block_start(next))) || table_start(&lines, index) {
                break;
            }
            index += 1;
        }
        let value = lines[start..index].join("\n");
        let is_scope = value.trim_end().ends_with([':', '：'])
            || condition().is_match(&value)
            || workflow().is_match(&value);
        let scope = if is_scope {
            prose_key(&value)
        } else {
            String::new()
        };
        output.append("paragraph", start, index, value);
        output.list_scope = scope;
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

fn block_start(line: &str) -> bool {
    heading().is_match(line)
        || fence().is_match(line)
        || bullet().is_match(line)
        || ordered().is_match(line)
        || is_indented(line)
        || is_html_table(line)
}

fn list_end(lines: &[&str], mut index: usize, marker: &regex::Regex) -> usize {
    while lines.get(index).is_some_and(|next| {
        !blank(next) && (marker.is_match(next) || next.starts_with([' ', '\t']))
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

pub fn align_units(old: &[&Unit], new: &[&Unit], max_work: usize) -> Alignment {
    let mut exact = HashMap::<_, Vec<usize>>::new();
    for (index, unit) in old.iter().enumerate() {
        exact.entry(unit.exact_key()).or_default().push(index);
    }
    let mut used = HashSet::new();
    let mut items: Vec<_> = new
        .iter()
        .enumerate()
        .map(|(index, unit)| {
            exact.get(&unit.exact_key()).map(|matches| {
                used.extend(matches.iter().copied());
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
    for (index, unit) in old.iter().enumerate().filter(|(i, _)| !used.contains(i)) {
        groups
            .entry((unit.kind, unit.context.as_slice()))
            .or_default()
            .push(index);
    }
    let mut cache = HashMap::new();
    let mut remaining = max_work;
    let mut complete = true;
    for (index, item) in items
        .iter_mut()
        .enumerate()
        .filter(|(_, item)| item.is_none())
    {
        let unit = new[index];
        let mut scored = Vec::<(f64, usize)>::new();
        let after = grams(&unit.key);
        let after_size = unit.key.chars().count();
        let mut exhausted = false;
        for &previous in groups
            .get(&(unit.kind, unit.context.as_slice()))
            .into_iter()
            .flatten()
        {
            if used.contains(&previous) {
                continue;
            }
            let cost = old[previous].key.chars().count().saturating_add(after_size);
            let Some(work) = remaining.checked_sub(cost) else {
                exhausted = true;
                break;
            };
            remaining = work;
            let before = cache
                .entry(previous)
                .or_insert_with(|| grams(&old[previous].key));
            let similarity = (2 * before.intersection(&after).count()) as f64
                / (before.len() + after.len()) as f64;
            if similarity >= 0.6 {
                scored.push((similarity, previous));
            }
        }
        scored.sort_unstable_by(|a, b| b.0.total_cmp(&a.0).then_with(|| b.1.cmp(&a.1)));
        let (kind, previous, basis, similarity) = if exhausted {
            complete = false;
            ("unresolved", None, "BODY_MATCH_BUDGET_EXCEEDED", None)
        } else {
            match scored.as_slice() {
                [(best, _), (second, _), ..] if best - second < 0.03 => {
                    ("unresolved", None, "AMBIGUOUS_BODY_ALIGNMENT", Some(*best))
                }
                [(similarity, previous), ..] => {
                    used.insert(*previous);
                    (
                        "revise",
                        Some(*previous),
                        "SIMILAR_BODY_REVISION_CANDIDATE",
                        Some(*similarity),
                    )
                }
                [] => ("add", None, "NO_MATCHED_BASELINE_BODY_UNIT", None),
            }
        };
        *item = Some(Match {
            kind,
            old: previous,
            new: Some(index),
            basis,
            similarity,
        });
    }
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
    use super::extract_units;
    use serde_json::Value;

    #[test]
    fn unicode_source_separators_preserve_headings_and_unit_locations() {
        let units = extract_units("# Scope\u{2028}- limit\u{85}- refund", &Value::Null).unwrap();
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
}
