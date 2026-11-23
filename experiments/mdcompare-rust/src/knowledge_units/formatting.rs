use std::ops::Range;

use regex::Regex;
use unicode_normalization::UnicodeNormalization;

use super::body_view::{closing, inline_code};
use super::table_separator;

pub(super) fn html_tags() -> &'static Regex {
    rx!(
        r#"<(?P<closing>/)?(?P<tag>[A-Za-z][A-Za-z0-9:-]*)\b(?P<attrs>(?:[^>"']|"[^"]*"|'[^']*')*)>"#
    )
}

fn is_cosmetic(tag: &str) -> bool {
    matches!(
        tag,
        "font" | "span" | "u" | "div" | "p" | "center" | "b" | "strong" | "em" | "i" | "br"
    )
}

pub(super) fn attributes(text: &str) -> Option<Vec<(String, Option<&str>)>> {
    let pattern =
        rx!(r#"\s+([A-Za-z_:][A-Za-z0-9_:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?"#);
    let mut cursor = 0;
    let mut attrs = Vec::new();
    for entry in pattern.captures_iter(text) {
        let full = entry.get(0)?;
        if !text[cursor..full.start()].trim().is_empty() {
            return None;
        }
        attrs.push((
            entry[1].to_ascii_lowercase(),
            (2..=4).find_map(|i| entry.get(i).map(|value| value.as_str())),
        ));
        cursor = full.end();
    }
    text[cursor..].trim().is_empty().then_some(attrs)
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

fn cosmetic_markup(text: &str) -> String {
    html_tags()
        .replace_all(text, |match_: &regex::Captures<'_>| {
            let tag = match_["tag"].to_ascii_lowercase();
            if is_cosmetic(&tag) {
                let closing = match_.name("closing").is_some();
                if tag == "br" && !closing {
                    return " ".to_owned();
                }
                if tag != "br"
                    && (closing
                        || attributes(&match_["attrs"]).is_some_and(|attrs| cosmetic_attrs(&attrs)))
                {
                    return String::new();
                }
            }
            match_[0].to_owned()
        })
        .into_owned()
}

fn protected_ranges(text: &str, include_cosmetic: bool) -> Vec<Range<usize>> {
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
    ranges.extend(html_tags().captures_iter(text).filter_map(|m| {
        (include_cosmetic || !is_cosmetic(&m["tag"].to_ascii_lowercase()))
            .then(|| m.get(0).expect("tag match").range())
    }));
    for (start, _) in text.match_indices("](") {
        if let Some(end) = closing(text, start + 1, b'(', b')') {
            ranges.push(start + 2..end);
        }
    }
    ranges.sort_unstable_by_key(|span| (span.start, span.end));
    ranges
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

fn normalize(text: &str) -> String {
    let unit = rx!(
        r"(?P<number>(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)\s*(?P<magnitude>万亿|亿|万)?\s*(?P<unit>人民币元|美元|港元|欧元|日元|万元|元|笔|个|次|人|天|日|年|秒|分钟|小时|[%％‰])"
    );
    let value = html_escape::decode_html_entities(text)
        .replace('\u{a0}', " ")
        .nfc()
        .collect::<String>();
    let value =
        rx!(r"\*\*([^*]+)\*\*|__([^_]+)__").replace_all(&value, |m: &regex::Captures<'_>| {
            m.get(1)
                .or_else(|| m.get(2))
                .expect("emphasis content")
                .as_str()
                .to_owned()
        });
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
    let text = transform_unprotected(text, protected_ranges(text, false), cosmetic_markup);
    transform_unprotected(&text, protected_ranges(&text, true), normalize)
        .trim()
        .to_owned()
}

#[derive(Default)]
struct HtmlTable {
    rows: Vec<Vec<String>>,
    row: Option<Vec<String>>,
    cell: Option<String>,
    links: Vec<String>,
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
                match tag {
                    "font" | "span" | "u" if cosmetic_attrs(&attrs) => {}
                    "br" | "p" | "div" if cosmetic_attrs(&attrs) => cell.push(' '),
                    "b" | "strong" => cell.push_str("**"),
                    "em" | "i" => cell.push('*'),
                    "code" if !self.literal_code => {
                        self.literal_code = true;
                        cell.push('`');
                    }
                    "a" => {
                        let href = attrs.iter().rev().find(|(name, _)| name == "href")?.1?;
                        self.links
                            .push(html_escape::decode_html_entities(href).into_owned());
                        cell.push('[');
                    }
                    _ => return None,
                }
            }
        }
        Some(())
    }

    fn end(&mut self, tag: &str) -> Option<()> {
        match tag {
            "td" | "th" => {
                if self.literal_code {
                    return None;
                }
                let value = self.cell.take()?;
                self.row.as_mut()?.push(prose_key(&value));
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
                match tag {
                    "font" | "span" | "u" => {}
                    "p" | "div" => cell.push(' '),
                    "b" | "strong" => cell.push_str("**"),
                    "em" | "i" => cell.push('*'),
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
                if decoded.contains('`') {
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
                let raw = &m["attrs"];
                let self_close = raw.ends_with('/')
                    && (raw.trim() == "/"
                        || !raw.contains('=')
                        || raw[..raw.len() - 1].ends_with([' ', '\t', '"', '\'']));
                if self_close && tag != "br" {
                    return None;
                }
                let attr_text = if self_close {
                    &raw[..raw.len() - 1]
                } else {
                    raw
                };
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
    let mut rows = if rx!(r"(?i)<table\b").is_match(text) {
        let Some(rows) = HtmlTable::parse(text) else {
            return format!("unsupported-html-table:{text}");
        };
        rows
    } else {
        text.split_terminator('\n')
            .filter(|line| !table_separator().is_match(line))
            .map(markdown_cells)
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
    use super::table_key;

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
