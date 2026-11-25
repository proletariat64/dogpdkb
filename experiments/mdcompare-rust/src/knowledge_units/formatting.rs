use std::ops::Range;

use unicode_normalization::UnicodeNormalization;

use super::table_separator;
use crate::markdown_syntax::{attributes, escaped, html_tags, inline_code, overlaps};

fn is_cosmetic(tag: &str) -> bool {
    matches!(
        tag,
        "font" | "span" | "u" | "div" | "p" | "center" | "b" | "strong" | "em" | "i" | "br"
    )
}

pub(super) fn cosmetic_attrs(attrs: &[(String, Option<&str>)]) -> bool {
    attrs.iter().all(|(name, value)| {
        matches!(name.as_str(), "class" | "id" | "color" | "face" | "size")
            || (name == "style"
                && value.unwrap_or_default().split(';').all(|declaration| {
                    if declaration.trim().is_empty() {
                        return true;
                    }
                    declaration.split_once(':').is_some_and(|(prop, value)| {
                        matches!(
                            prop.trim().to_ascii_lowercase().as_str(),
                            "font-family"
                                | "font-size"
                                | "font-weight"
                                | "font-style"
                                | "color"
                                | "background-color"
                                | "line-height"
                                | "letter-spacing"
                                | "text-align"
                                | "vertical-align"
                                | "width"
                                | "height"
                                | "table-layout"
                                | "border-collapse"
                                | "border-spacing"
                                | "border"
                                | "border-width"
                                | "border-style"
                                | "border-color"
                                | "padding"
                                | "margin"
                        ) || prop.trim().eq_ignore_ascii_case("text-decoration")
                            && matches!(
                                value.trim().to_ascii_lowercase().as_str(),
                                "none" | "underline"
                            )
                    })
                }))
    })
}

fn self_closing_attrs(raw: &str) -> (&str, bool) {
    let ending = raw.strip_suffix('/').filter(|before| {
        raw.trim() == "/" || !raw.contains('=') || before.ends_with([' ', '\t', '"', '\''])
    });
    ending.map_or((raw, false), |before| (before, true))
}

fn merged_ranges(mut ranges: Vec<Range<usize>>) -> Vec<Range<usize>> {
    ranges.retain(|span| !span.is_empty());
    ranges.sort_unstable_by_key(|span| (span.start, span.end));
    let mut merged: Vec<Range<usize>> = Vec::new();
    for span in ranges {
        if let Some(last) = merged.last_mut()
            && span.start <= last.end
        {
            last.end = last.end.max(span.end);
        } else {
            merged.push(span);
        }
    }
    merged
}

fn cosmetic_markup(text: &str) -> String {
    let protected = protected_ranges(text, false);
    let mut pairs = Vec::new();
    let mut removable = Vec::new();
    for m in html_tags().captures_iter(text) {
        let range = m.get(0).expect("tag match").range();
        if overlaps(&range, &protected) {
            continue;
        }
        let tag = m["tag"].to_ascii_lowercase();
        if !is_cosmetic(&tag) {
            continue;
        }
        let raw = &m["attrs"];
        if m.name("closing").is_some() {
            if pairs.last().is_some_and(|(name, _, _)| name == &tag) {
                let (_, open, safe) = pairs.pop().expect("matching opener");
                if safe && raw.trim().is_empty() {
                    let boundary = matches!(tag.as_str(), "p" | "div" | "center");
                    removable.extend([(open, boundary), (range, boundary)]);
                }
            } else {
                pairs.clear();
            }
        } else {
            let (raw, self_closing) = self_closing_attrs(raw);
            let safe = attributes(raw).is_some_and(|attrs| cosmetic_attrs(&attrs));
            if tag == "br" {
                if safe {
                    removable.push((range, true));
                }
            } else if !self_closing {
                pairs.push((tag, range, safe));
            }
        }
    }
    removable.sort_unstable_by_key(|(range, _)| range.start);
    let mut result = String::with_capacity(text.len());
    let mut cursor = 0;
    for (range, boundary) in removable {
        result.push_str(&text[cursor..range.start]);
        if boundary {
            result.push(' ');
        }
        cursor = range.end;
    }
    result.push_str(&text[cursor..]);
    result
}

fn link_ranges(text: &str, ranges: &mut Vec<Range<usize>>) {
    let bytes = text.as_bytes();
    let mut stack = Vec::new();
    let mut pos = 0;
    while let Some(byte) = bytes.get(pos) {
        match byte {
            b'\\' => {
                pos += 2;
                continue;
            }
            b'(' => stack.push(pos),
            b')' => {
                if let Some(start) = stack.pop()
                    && bytes.get(start.wrapping_sub(1)) == Some(&b']')
                {
                    ranges.push(start + 1..pos);
                }
            }
            _ => {}
        }
        pos += 1;
    }
}

fn literal_ranges(text: &str, protected: &[Range<usize>], ranges: &mut Vec<Range<usize>>) {
    let mut stack = Vec::new();
    let mut start = None;
    for m in html_tags().captures_iter(text) {
        let range = m.get(0).expect("tag match").range();
        let tag = m["tag"].to_ascii_lowercase();
        if !matches!(tag.as_str(), "code" | "pre") {
            continue;
        }
        if start.is_none() && overlaps(&(range.start..range.start + 1), protected) {
            continue;
        }
        if m.name("closing").is_some() {
            if stack.last() == Some(&tag) && m["attrs"].trim().is_empty() {
                stack.pop();
                if stack.is_empty()
                    && let Some(start) = start.take()
                {
                    ranges.push(start..range.end);
                }
            }
        } else {
            // Non-void HTML elements do not gain a trusted closing boundary
            // from a slash. An uncertain opening is protected to the end.
            start.get_or_insert(range.start);
            stack.push(tag);
        }
    }
    if let Some(start) = start {
        ranges.push(start..text.len());
    }
}

fn protected_ranges(text: &str, include_tags: bool) -> Vec<Range<usize>> {
    let mut ranges = inline_code(text, false);
    let urls = rx!(r"(?:[A-Za-z][A-Za-z0-9+.-]*://|www\.)[^\s<>]+");
    ranges.extend(urls.find_iter(text).map(|entry| entry.range()));
    for (pos, ch) in text
        .char_indices()
        .filter(|(_, ch)| matches!(ch, '/' | '.'))
    {
        if pos != 0
            && !text[..pos]
                .chars()
                .next_back()
                .is_some_and(char::is_whitespace)
        {
            continue;
        }
        let rest = &text[pos..];
        let prefix = if ch == '/' && !rest.starts_with("/>") {
            1
        } else if rest.starts_with("./") {
            2
        } else if rest.starts_with("../") {
            3
        } else {
            continue;
        };
        let body = &rest[prefix..];
        let end = body.find(char::is_whitespace).unwrap_or(body.len());
        if end != 0 {
            ranges.push(pos..pos + prefix + end);
        }
    }
    link_ranges(text, &mut ranges);
    let mut ranges = merged_ranges(ranges);
    let mut literal = Vec::new();
    literal_ranges(text, &ranges, &mut literal);
    ranges.extend(literal);
    if include_tags {
        ranges.extend(html_tags().find_iter(text).map(|m| m.range()));
    }
    merged_ranges(ranges)
}

fn transform_unprotected(
    text: &str,
    ranges: Vec<Range<usize>>,
    transform: impl Fn(&str) -> String,
) -> String {
    let mut result = String::with_capacity(text.len());
    let mut cursor = 0;
    for span in ranges {
        if span.end <= cursor {
            continue;
        }
        if cursor < span.start {
            result.push_str(&transform(&text[cursor..span.start]));
        }
        result.push_str(&text[cursor.max(span.start)..span.end]);
        cursor = span.end;
    }
    result.push_str(&transform(&text[cursor..]));
    result
}

fn quantity(m: &regex::Captures<'_>) -> String {
    let number = m["number"].replace(',', "");
    // Decimal magnitudes here are powers of ten. Shift the radix point
    // directly so long quantities retain all digits without rounding.
    let (whole, fraction) = number.split_once('.').unwrap_or((&number, ""));
    let magnitude = match m.name("magnitude").map(|value| value.as_str()) {
        Some("万") => 4,
        Some("亿") => 8,
        Some("万亿") => 12,
        _ => 0,
    };
    let mut fraction = fraction.chars();
    let integer = whole
        .chars()
        .chain((0..magnitude).map(|_| fraction.next().unwrap_or('0')))
        .collect::<String>();
    let integer = integer.trim_start_matches('0');
    let integer = if integer.is_empty() { "0" } else { integer };
    let decimals = fraction.as_str().trim_end_matches('0');
    let unit = if &m["unit"] == "％" { "%" } else { &m["unit"] };
    if decimals.is_empty() {
        format!("{integer}{unit}")
    } else {
        format!("{integer}.{decimals}{unit}")
    }
}

fn flanking(text: &str, start: usize, end: usize) -> (bool, bool) {
    let previous = text[..start].chars().next_back();
    let next = text[end..].chars().next();
    let whitespace = |ch: Option<char>| {
        ch.is_none_or(|ch| {
            matches!(ch, ' ' | '\t' | '\n' | '\r' | '\u{000c}')
                || (!ch.is_ascii() && rx!(r"^\p{Zs}$").is_match(ch.encode_utf8(&mut [0; 4])))
        })
    };
    let punctuation = |ch: Option<char>| ch.is_some_and(is_punctuation);
    let left =
        !whitespace(next) && (!punctuation(next) || whitespace(previous) || punctuation(previous));
    let right =
        !whitespace(previous) && (!punctuation(previous) || whitespace(next) || punctuation(next));
    (left, right)
}

fn is_punctuation(ch: char) -> bool {
    ch.is_ascii_punctuation()
        || (!ch.is_ascii() && rx!(r"^\p{P}$").is_match(ch.encode_utf8(&mut [0; 4])))
}

fn strip_strong_emphasis(text: &str, protected: &[Range<usize>]) -> String {
    let mut result = String::with_capacity(text.len());
    let mut cursor = 0;
    for m in rx!(r"\*\*([^*]+)\*\*|__([^_]+)__").captures_iter(text) {
        let full = m.get(0).expect("emphasis match").range();
        let inner = m.get(1).or_else(|| m.get(2)).expect("emphasis content");
        let marker = text.as_bytes()[full.start];
        let opening_end = inner.start();
        let closing_start = inner.end();
        let (opens_left, opens_right) = flanking(text, full.start, opening_end);
        let (closes_left, closes_right) = flanking(text, closing_start, full.end);
        let previous = text[..full.start].chars().next_back();
        let following = text[full.end..].chars().next();
        if overlaps(&full, protected)
            || escaped(text, full.start)
            || escaped(text, closing_start)
            || previous.is_some_and(|ch| ch == marker as char)
            || following.is_some_and(|ch| ch == marker as char)
            || !opens_left
            || !closes_right
            || (marker == b'_'
                && (opens_right && !previous.is_some_and(is_punctuation)
                    || closes_left && !following.is_some_and(is_punctuation)))
        {
            continue;
        }
        result.push_str(&text[cursor..full.start]);
        result.push_str(inner.as_str());
        cursor = full.end;
    }
    result.push_str(&text[cursor..]);
    result
}

fn normalize(text: &str) -> String {
    let unit = rx!(
        r"(?P<number>(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)\s*(?P<magnitude>万亿|亿|万)?\s*(?P<unit>人民币元|美元|港元|欧元|日元|万元|元|笔|个|次|人|天|日|年|秒|分钟|小时|[%％‰])"
    );
    let value = html_escape::decode_html_entities(text)
        .replace('\u{a0}', " ")
        .nfc()
        .collect::<String>();
    let value = rx!(r"[ \t\n]+").replace_all(&value, " ");
    unit.replace_all(&value, |m: &regex::Captures<'_>| {
        let full = m.get(0).expect("quantity match");
        let before = value.as_bytes().get(full.start().wrapping_sub(1)).copied();
        let after = &value.as_bytes()[full.end()..];
        if before.is_some_and(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'.'))
            || after
                .first()
                .is_some_and(|c| c.is_ascii_alphanumeric() || *c == b'_')
            || matches!(after, [b'.', digit, ..] if digit.is_ascii_digit())
        {
            full.as_str().to_owned()
        } else {
            quantity(m)
        }
    })
    .into_owned()
}

pub fn prose_key(text: &str) -> String {
    // Recognize delimiters in the original Markdown, before markup removal
    // or entity decoding can manufacture new emphasis syntax.
    let text = strip_strong_emphasis(text, &protected_ranges(text, true));
    let text = cosmetic_markup(&text);
    normalized_prose(&text)
}

fn normalized_prose(text: &str) -> String {
    transform_unprotected(text, protected_ranges(text, true), normalize)
        .trim()
        .to_owned()
}

#[derive(Default)]
struct HtmlTable {
    rows: Vec<Vec<String>>,
    row: Option<Vec<String>>,
    cell: Option<String>,
    links: Vec<String>,
    inline: Vec<String>,
    depth: i64,
    tables: usize,
    literal_code: bool,
}

fn layout(name: &str) -> bool {
    matches!(
        name,
        "width"
            | "height"
            | "align"
            | "valign"
            | "border"
            | "cellpadding"
            | "cellspacing"
            | "frame"
            | "rules"
            | "rowspan"
            | "colspan"
    )
}

impl HtmlTable {
    fn start(&mut self, tag: &str, attrs: Vec<(String, Option<&str>)>) -> Option<()> {
        if matches!(
            tag,
            "table" | "tr" | "td" | "th" | "thead" | "tbody" | "tfoot"
        ) {
            let css: Vec<_> = attrs
                .iter()
                .filter(|(name, _)| !layout(name))
                .cloned()
                .collect();
            if !cosmetic_attrs(&css) {
                return None;
            }
        }
        match tag {
            "table" => {
                self.depth += 1;
                self.tables += 1;
                if self.depth != 1 || self.tables != 1 {
                    return None;
                }
            }
            "thead" | "tbody" | "tfoot" if self.depth != 0 => {}
            "tr" if self.depth != 0 => {
                if self.row.replace(Vec::new()).is_some() {
                    return None;
                }
            }
            "td" | "th" if self.depth != 0 => {
                if self.row.is_none()
                    || self.cell.replace(String::new()).is_some()
                    || attrs.iter().any(|(name, value)| {
                        matches!(name.as_str(), "rowspan" | "colspan") && *value != Some("1")
                    })
                {
                    return None;
                }
            }
            _ => {
                let cell = self.cell.as_mut()?;
                if self.literal_code {
                    return None;
                }
                match tag {
                    "font" | "span" | "u" | "b" | "strong" | "em" | "i"
                        if cosmetic_attrs(&attrs) => {}
                    "br" | "p" | "div" | "center" if cosmetic_attrs(&attrs) => cell.push(' '),
                    "code" if cosmetic_attrs(&attrs) => {
                        self.literal_code = true;
                        cell.push('`');
                    }
                    "a" => {
                        let mut hrefs = attrs.iter().filter(|(name, _)| name == "href");
                        let href = hrefs.next()?.1?;
                        let cosmetic: Vec<_> = attrs
                            .iter()
                            .filter(|(name, _)| name != "href")
                            .cloned()
                            .collect();
                        if hrefs.next().is_some() || !cosmetic_attrs(&cosmetic) {
                            return None;
                        }
                        self.links
                            .push(html_escape::decode_html_entities(href).into_owned());
                        cell.push('[');
                    }
                    _ => return None,
                }
                if tag != "br" {
                    self.inline.push(tag.into());
                }
            }
        }
        Some(())
    }

    fn end(&mut self, tag: &str) -> Option<()> {
        match tag {
            "td" | "th" => {
                if self.literal_code || !self.inline.is_empty() {
                    return None;
                }
                let value = self.cell.take()?;
                self.row.as_mut()?.push(normalized_prose(&value));
            }
            "tr" => {
                if self.cell.is_some() {
                    return None;
                }
                self.rows.push(self.row.take()?);
            }
            "table" => self.depth -= 1,
            "thead" | "tbody" | "tfoot" => {}
            _ => {
                let cell = self.cell.as_mut()?;
                if self.inline.pop().as_deref() != Some(tag) {
                    return None;
                }
                match tag {
                    "font" | "span" | "u" | "b" | "strong" | "em" | "i" => {}
                    "p" | "div" | "center" => cell.push(' '),
                    "code" if self.literal_code => {
                        self.literal_code = false;
                        cell.push('`');
                    }
                    "a" => {
                        cell.push_str("](");
                        cell.push_str(&self.links.pop()?);
                        cell.push(')');
                    }
                    _ => return None,
                }
            }
        }
        Some(())
    }

    fn data(&mut self, text: &str) -> Option<()> {
        if let Some(cell) = self.cell.as_mut() {
            if self.literal_code {
                let decoded = html_escape::decode_html_entities(text);
                if decoded.contains(['`', '\r', '\n']) {
                    return None;
                }
                cell.push_str(&decoded);
            } else {
                // Normalize entities after markup recognition so visible tags
                // encoded as cell text cannot be stripped as presentation.
                cell.push_str(text);
            }
        } else if !text.trim().is_empty() {
            return None;
        }
        Some(())
    }

    fn parse(text: &str) -> Option<Vec<Vec<String>>> {
        let mut parser = Self::default();
        let mut cursor = 0;
        let tags = rx!(
            r#"(?s)<!--.*?-->|<(?P<closing>/)?(?P<tag>[A-Za-z][A-Za-z0-9:-]*)\b(?P<attrs>(?:[^>"']|"[^"]*"|'[^']*')*)>"#
        );
        for m in tags.captures_iter(text) {
            let full = m.get(0)?;
            parser.data(&text[cursor..full.start()])?;
            cursor = full.end();
            let Some(tag) = m.name("tag") else {
                continue;
            };
            let tag = tag.as_str().to_ascii_lowercase();
            if m.name("closing").is_some() {
                parser.end(&tag)?;
            } else {
                let (attr_text, self_close) = self_closing_attrs(&m["attrs"]);
                if self_close && tag != "br" {
                    return None;
                }
                parser.start(&tag, attributes(attr_text)?)?;
            }
        }
        parser.data(&text[cursor..])?;
        if parser.depth == 0
            && parser.row.is_none()
            && parser.cell.is_none()
            && parser.links.is_empty()
            && !parser.rows.is_empty()
        {
            Some(parser.rows)
        } else {
            None
        }
    }
}

fn markdown_cells(line: &str) -> Vec<String> {
    let line = line.trim();
    let line = line.strip_prefix('|').unwrap_or(line);
    let line = if line.ends_with("\\|") {
        line
    } else {
        line.strip_suffix('|').unwrap_or(line)
    };
    let mut cells = Vec::new();
    let mut start = 0;
    for (pos, _) in line.match_indices('|') {
        if line.as_bytes().get(pos.wrapping_sub(1)) == Some(&b'\\') {
            continue;
        }
        cells.push(prose_key(&line[start..pos]));
        start = pos + 1;
    }
    cells.push(prose_key(&line[start..]));
    cells
}

pub fn table_key(text: &str) -> String {
    let mut rows = if rx!(r"(?i)^\s*<table\b").is_match(text) {
        let Some(rows) = HtmlTable::parse(text) else {
            return format!("unsupported-html-table:{text}");
        };
        rows
    } else {
        text.split_terminator('\n')
            .enumerate()
            .filter(|(index, line)| *index != 1 || !table_separator().is_match(line))
            .map(|(_, line)| markdown_cells(line))
            .collect()
    };
    let mut position = 0;
    rows.retain(|row| {
        position += 1;
        position == 1 || row.iter().any(|cell| !cell.is_empty())
    });
    format!(
        "table:{}",
        serde_json::to_string(&rows).expect("JSON strings")
    )
}

#[cfg(test)]
mod tests {
    use super::{link_ranges, prose_key, table_key};

    #[test]
    fn block_markup_keeps_boundaries_between_adjacent_values() {
        assert_eq!(prose_key("<p>10</p><p>20</p>"), "10 20");
        assert_eq!(prose_key("<div>10</div><center>20</center>"), "10 20");
    }

    #[test]
    fn non_cosmetic_tags_and_break_attributes_remain_in_the_key() {
        assert_ne!(
            prose_key("<span hidden>10</span>20"),
            prose_key("<span hidden>1020</span>")
        );
        assert_ne!(
            prose_key("10<br title=\"Required\">20"),
            prose_key("10<br>20")
        );
        assert_eq!(prose_key("10<br class=\"layout\"/>20"), "10 20");
        assert_eq!(prose_key("<span>10 <b>20</b></span>"), "10 20");
    }

    #[test]
    fn strong_emphasis_requires_real_unescaped_markdown_delimiters() {
        assert_ne!(
            prose_key("account__id__suffix"),
            prose_key("accountidsuffix")
        );
        assert_ne!(prose_key("&#42;&#42;Limit&#42;&#42;"), prose_key("Limit"));
        assert_ne!(prose_key(r"\**Limit**"), prose_key("Limit"));
        assert_ne!(prose_key("A**，Limit**"), prose_key("A，Limit"));
        assert_eq!(prose_key("**Limit** __Amount__"), "Limit Amount");
    }

    #[test]
    fn literal_html_code_and_preformatted_regions_preserve_payload() {
        assert_ne!(
            prose_key("Use <code>foo  bar</code>"),
            prose_key("Use <code>foo bar</code>")
        );
        assert_ne!(
            prose_key("Use <pre><span>10万元</span></pre>"),
            prose_key("Use <pre>100000元</pre>")
        );
        assert_eq!(prose_key("Use <code>foo  bar"), "Use <code>foo  bar");
        assert_eq!(
            prose_key("<span>Use <code>foo  bar</code></span>"),
            "Use <code>foo  bar</code>"
        );
    }

    #[test]
    fn link_target_ranges_match_nested_and_escaped_parentheses() {
        let text = r"[one](path(nested)) and [two](file\).txt) [unfinished](folder";
        let mut ranges = Vec::new();
        link_ranges(text, &mut ranges);
        assert_eq!(
            ranges
                .iter()
                .map(|range| &text[range.clone()])
                .collect::<Vec<_>>(),
            ["path(nested)", r"file\).txt"]
        );
    }

    #[test]
    fn markdown_table_with_code_example_is_classified_as_markdown() {
        assert_eq!(
            table_key("|A|B|\n|---|---|\n|`<table>`|Limit|"),
            table_key("| A | B |\n|---|---|\n| `<table>` | Limit |")
        );
    }

    #[test]
    fn markdown_table_retains_separator_looking_data_rows() {
        let header = "|A|B|\n|---|---|\n";
        assert_eq!(
            table_key(&format!("{header}|---|---|")),
            r#"table:[["A","B"],["---","---"]]"#
        );
        assert_ne!(table_key(header), table_key(&format!("{header}|---|---|")));
    }

    #[test]
    fn html_cells_keep_literal_text_and_unsafe_attributes() {
        let table = |value| table_key(&format!("<table><tr><td>{value}</td></tr></table>"));
        let plain = table("Required");
        assert_eq!(table("<strong>Required</strong>"), plain);
        assert_ne!(table("**Required**"), plain);
        assert_ne!(table("<strong hidden>Required</strong>"), plain);
        assert_ne!(
            table("<code hidden>Required</code>"),
            table("<code>Required</code>")
        );
        assert_ne!(
            table("<a href=\"doc\" title=\"Required\">Report</a>"),
            table("<a href=\"doc\">Report</a>")
        );
        assert_eq!(
            table("<a href=\"doc\">Report</a>"),
            r#"table:[["[Report](doc)"]]"#
        );
    }

    #[test]
    fn markdown_table_empty_edge_cells_keep_column_bindings() {
        let header = "|A|B|C|\n|---|---|---|\n";
        assert_eq!(
            table_key(&format!("{header}||USD|100|")),
            r#"table:[["A","B","C"],["","USD","100"]]"#
        );
        assert_eq!(
            table_key(&format!("{header}|USD|100||")),
            r#"table:[["A","B","C"],["USD","100",""]]"#
        );
    }

    #[test]
    fn escaped_html_cell_text_is_not_cosmetic_markup() {
        let escaped = "<table><tr><td>&lt;span&gt;Limit&lt;/span&gt;</td></tr></table>";
        let markup = "<table><tr><td><span>Limit</span></td></tr></table>";
        let plain = "<table><tr><td>Limit</td></tr></table>";
        assert_ne!(table_key(escaped), table_key(plain));
        assert_eq!(table_key(markup), table_key(plain));
    }
}
