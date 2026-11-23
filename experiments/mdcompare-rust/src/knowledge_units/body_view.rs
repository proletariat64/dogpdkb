use std::collections::{HashMap, HashSet};
use std::ops::Range;

use regex::Regex;
use serde_json::Value;
use unicode_casefold::UnicodeCaseFold;

use super::formatting::{attributes, cosmetic_attrs, html_tags};
use super::{fence, fence_end, is_indented, is_source_line_end, source_lines};

type Span = Range<usize>;

fn ocr_begin() -> &'static Regex {
    rx!(r"(?is)<!--\s*mdocr:ocr-begin\b.*?-->")
}

fn ocr_end() -> &'static Regex {
    rx!(r"(?i)<!--\s*mdocr:ocr-end\s*-->")
}

pub(super) fn closing(text: &str, start: usize, open: u8, close: u8) -> Option<usize> {
    let bytes = text.as_bytes();
    let mut depth = 1;
    let mut pos = start + 1;
    while let Some(&byte) = bytes.get(pos) {
        if byte == b'\\' {
            pos += 2;
            continue;
        }
        if byte == open {
            depth += 1;
        } else if byte == close {
            depth -= 1;
            if depth == 0 {
                return Some(pos);
            }
        }
        pos += 1;
    }
    None
}

pub(super) fn outside(position: usize, spans: &[Span]) -> bool {
    !spans.iter().any(|span| span.contains(&position))
}

pub(super) fn inline_code(text: &str, multiline: bool) -> Vec<Span> {
    let mut runs: Vec<_> = rx!(r"`+").find_iter(text).map(|m| (m, None)).collect();
    let mut following = HashMap::new();
    for (index, (run, next)) in runs.iter_mut().enumerate().rev() {
        *next = following.insert(run.len(), index);
    }
    let mut pairs = Vec::new();
    let mut index = 0;
    while let Some((start, next)) = runs.get(index) {
        index += 1;
        if let Some(next) = *next {
            let (end, _) = runs[next];
            if multiline || !text[start.end()..end.start()].contains('\n') {
                pairs.push(start.start()..end.end());
                index = next + 1;
            }
        }
    }
    pairs
}

fn code_ranges(text: &str) -> Vec<Span> {
    let mut spans = Vec::new();
    let mut offset = 0;
    let mut open = None;
    for line in source_lines(text, true) {
        let line_value = line.trim_end_matches('\n');
        if let Some((start, delimiter)) = open {
            if fence_end(line_value, delimiter) {
                spans.push(start..offset + line.len());
                open = None;
            }
        } else if let Some(delimiter) = fence().captures(line_value).and_then(|m| m.get(1)) {
            open = Some((offset, delimiter.as_str()));
        } else if is_indented(line) {
            spans.push(offset..offset + line.len());
        }
        offset += line.len();
    }
    if let Some((start, _)) = open {
        spans.push(start..text.len());
    }
    spans.extend(inline_code(text, true));
    spans
}

fn line_number(line_starts: &[usize], pos: usize) -> usize {
    line_starts.partition_point(|&start| start <= pos)
}

struct Region {
    opening: usize,
    closing_start: usize,
    end: usize,
}

fn line_starts(text: &str) -> Vec<usize> {
    source_lines(text, true)
        .scan(0, |start, line| {
            let position = *start;
            *start += line.len();
            Some(position)
        })
        .collect()
}

fn declared_regions(
    text: &str,
    declaration: &Value,
    line_starts: &[usize],
) -> Result<Vec<Region>, &'static str> {
    const INVALID: &str = "BODY_SCOPE_DECLARATION_INVALID";
    const LOCATION: &str = "BODY_SCOPE_LOCATION_MISMATCH";
    let unset = match declaration {
        Value::Null => true,
        Value::Object(fields) => fields.is_empty(),
        Value::Array(items) => items.is_empty(),
        Value::String(text) => text.is_empty(),
        Value::Bool(value) => !value,
        Value::Number(value) => value.as_f64() == Some(0.0),
    };
    if unset {
        return Ok(Vec::new());
    }
    if declaration["version"] != "generated-regions-v1" {
        return Err(INVALID);
    }
    let begins: Vec<_> = ocr_begin().find_iter(text).collect();
    let ends: Vec<_> = ocr_end().find_iter(text).collect();
    let mut regions = Vec::new();
    for region in declaration["regions"].as_array().ok_or(INVALID)? {
        if region["kind"] != "mdocr_ocr" {
            return Err(INVALID);
        }
        let start = region["start_line"].as_u64().ok_or(LOCATION)? as usize;
        let end = region["end_line"].as_u64().ok_or(LOCATION)? as usize;
        if start < 1 || end < start || end > line_starts.len() {
            return Err(LOCATION);
        }
        let start_matches: Vec<_> = begins
            .iter()
            .filter(|m| line_number(line_starts, m.start()) == start)
            .collect();
        let end_matches: Vec<_> = ends
            .iter()
            .filter(|m| line_number(line_starts, m.start()) == end)
            .collect();
        match (start_matches.as_slice(), end_matches.as_slice()) {
            ([a], [b]) if a.end() <= b.start() => regions.push(Region {
                opening: a.start(),
                closing_start: b.start(),
                end: b.end(),
            }),
            _ => return Err(LOCATION),
        }
    }
    let mut ordered: Vec<_> = regions.iter().collect();
    ordered.sort_by_key(|region| region.opening);
    if ordered.windows(2).any(|pair| pair[0].end > pair[1].opening) {
        return Err(LOCATION);
    }
    Ok(regions)
}

pub(crate) fn declared_marker_positions(
    text: &str,
    declaration: &Value,
) -> Result<Vec<(usize, usize)>, &'static str> {
    declared_regions(text, declaration, &line_starts(text)).map(|regions| {
        regions
            .into_iter()
            .map(|region| (region.opening, region.closing_start))
            .collect()
    })
}

fn generated_ranges(text: &str, code: &[Span], spans: &mut Vec<Span>) -> Result<(), &'static str> {
    const BOUNDARY: &str = "BODY_SCOPE_BOUNDARY_INVALID";
    const UNCLOSED: &str = "BODY_SCOPE_BOUNDARY_UNCLOSED";
    let mut markers: Vec<_> = ocr_begin()
        .find_iter(text)
        .map(|m| (m, true))
        .chain(ocr_end().find_iter(text).map(|m| (m, false)))
        .filter(|(m, _)| outside(m.start(), code))
        .collect();
    markers.sort_unstable_by_key(|(m, _)| m.start());
    let mut open = None;
    for (m, begin) in markers {
        if begin {
            if open.replace(m.start()).is_some() {
                return Err(BOUNDARY);
            }
        } else {
            spans.push(open.take().ok_or(BOUNDARY)?..m.end());
        }
    }
    if open.is_some() {
        return Err(UNCLOSED);
    }
    Ok(())
}

fn legacy_generated_ranges(
    text: &str,
    code: &[Span],
    spans: &mut Vec<Span>,
) -> Result<(), &'static str> {
    const UNCLOSED: &str = "BODY_SCOPE_BOUNDARY_UNCLOSED";
    let mut legacy = Vec::new();
    for (begin, end) in [
        (
            rx!(r"(?i)<!--\s*这是一张图片[，,]\s*ocr\s*内容为[：:]"),
            rx!(r"-->"),
        ),
        (
            rx!(r"(?m)^[ \t]*>[ \t]*\*\*图片转写[：:]\*\*[^\n]*"),
            rx!(r"(?m)^[ \t]*>[ \t]*(?:\*\*)?转写说明[：:](?:\*\*)?[^\n]*"),
        ),
    ] {
        let openings: Vec<_> = begin
            .find_iter(text)
            .filter(|m| outside(m.start(), code) && outside(m.start(), spans))
            .collect();
        let mut closes = end.find_iter(text).peekable();
        for (index, opening) in openings.iter().enumerate() {
            while closes.peek().is_some_and(|m| m.start() < opening.end()) {
                closes.next();
            }
            let limit = openings.get(index + 1).map_or(text.len(), |m| m.start());
            let closing = closes.next().filter(|m| m.end() <= limit).ok_or(UNCLOSED)?;
            legacy.push(opening.start()..closing.end());
        }
    }
    spans.extend(legacy);
    Ok(())
}

fn image_ranges(
    text: &str,
    code: &[Span],
    definitions: &HashSet<&str>,
) -> (Vec<Span>, HashSet<String>) {
    let mut spans: Vec<_> = rx!(r"\[PaddleOCR image region: untranscribed\]")
        .find_iter(text)
        .filter(|m| outside(m.start(), code))
        .map(|m| m.range())
        .collect();
    let tags = html_tags();
    let captures: Vec<_> = tags.captures_iter(text).collect();
    let mut html_spans = Vec::new();
    for m in &captures {
        let full = m.get(0).expect("tag match");
        html_spans.push(full.range());
        if m.name("closing").is_some() || !outside(full.start(), code) {
            continue;
        }
        match m["tag"].to_ascii_lowercase().as_str() {
            "picture" | "svg" if full.as_str().ends_with("/>") => spans.push(full.range()),
            "picture" | "svg" => {
                let end = captures
                    .iter()
                    .filter(|other| {
                        other.name("closing").is_some()
                            && other["tag"].eq_ignore_ascii_case(&m["tag"])
                    })
                    .filter_map(|other| other.get(0))
                    .find(|other| other.start() >= full.end())
                    .map_or(text.len(), |other| other.end());
                spans.push(full.start()..end);
            }
            "img" | "source" => spans.push(full.range()),
            _ => {}
        }
    }
    let mut labels = HashSet::new();
    for (start, _) in text.match_indices("![") {
        let escaped = text.as_bytes()[..start]
            .iter()
            .rev()
            .take_while(|&&b| b == b'\\')
            .count()
            % 2
            != 0;
        if escaped || !outside(start, code) || !outside(start, &html_spans) {
            continue;
        }
        let Some(close) = closing(text, start + 1, b'[', b']') else {
            continue;
        };
        let label = &text[start + 2..close];
        let suffix = close + 1;
        let end = match text.as_bytes().get(suffix) {
            Some(b'(') => {
                let Some(stop) = closing(text, suffix, b'(', b')') else {
                    continue;
                };
                if !valid_link_target(&text[suffix + 1..stop], true) {
                    continue;
                }
                stop + 1
            }
            _ => {
                let (id, end) = if text.as_bytes().get(suffix) == Some(&b'[') {
                    let Some(stop) = closing(text, suffix, b'[', b']') else {
                        continue;
                    };
                    let id = &text[suffix + 1..stop];
                    (if id.is_empty() { label } else { id }, stop + 1)
                } else {
                    (label, suffix)
                };
                let id = fold_label(id);
                if !definitions.contains(id.as_str()) {
                    continue;
                }
                labels.insert(id);
                end
            }
        };
        spans.push(start..end);
    }
    (spans, labels)
}

fn mask(text: &str, spans: &[Span]) -> String {
    let mut bytes = text.as_bytes().to_vec();
    for span in spans {
        for (position, ch) in text[span.clone()].char_indices() {
            if !is_source_line_end(ch) {
                let start = span.start + position;
                bytes[start..start + ch.len_utf8()].fill(b' ');
            }
        }
    }
    String::from_utf8(bytes).expect("mask preserves character boundaries")
}

fn caused_by_image(area: Span, content: Span, images: &[Span], code: &[Span]) -> bool {
    !code
        .iter()
        .any(|span| span.start < area.end && area.start < span.end)
        && images
            .iter()
            .any(|span| content.start <= span.start && span.end <= content.end)
}

fn blank_image_links(text: String, images: &[Span], code: &[Span]) -> String {
    let spans: Vec<_> = rx!(r"\[(\s*)\]\([^\n)]*\)")
        .captures_iter(&text)
        .filter_map(|m| {
            let area = m.get(0)?.range();
            let content = m.get(1)?.range();
            caused_by_image(area.clone(), content, images, code).then_some(area)
        })
        .collect();
    mask(&text, &spans)
}

fn blank_image_wrappers(mut text: String, images: &[Span], code: &[Span]) -> String {
    loop {
        let tags: Vec<_> = html_tags().captures_iter(&text).collect();
        let spans: Vec<_> = tags
            .windows(2)
            .filter_map(|pair| {
                let (open, close) = (&pair[0], &pair[1]);
                let tag = open["tag"].to_ascii_lowercase();
                if !matches!(
                    tag.as_str(),
                    "div" | "span" | "p" | "a" | "figure" | "center"
                ) || open.name("closing").is_some()
                    || close.name("closing").is_none()
                    || !close["tag"].eq_ignore_ascii_case(&tag)
                    || !close["attrs"].trim().is_empty()
                {
                    return None;
                }
                let open_match = open.get(0)?;
                let close_match = close.get(0)?;
                let content = open_match.end()..close_match.start();
                let area = open_match.start()..close_match.end();
                if !text[content.clone()].trim().is_empty()
                    || !caused_by_image(area.clone(), content, images, code)
                {
                    return None;
                }
                let filtered: Vec<_> = attributes(&open["attrs"])?
                    .into_iter()
                    .filter(|(name, value)| {
                        !(name == "href" && tag == "a" && value.is_some()
                            || name == "align"
                                && value.is_some_and(|value| {
                                    matches!(
                                        value.to_ascii_lowercase().as_str(),
                                        "left" | "right" | "center" | "justify"
                                    )
                                }))
                    })
                    .collect();
                cosmetic_attrs(&filtered).then_some(area)
            })
            .collect();
        if spans.is_empty() {
            break;
        }
        text = mask(&text, &spans);
    }
    text
}

fn target_end(text: &str) -> Option<usize> {
    if let Some(angle) = text.strip_prefix('<') {
        let mut chars = angle.char_indices().peekable();
        while let Some((offset, ch)) = chars.next() {
            match ch {
                '\\' if chars
                    .peek()
                    .is_some_and(|(_, next)| next.is_ascii_punctuation()) =>
                {
                    chars.next();
                }
                '>' => return Some(offset + 2),
                '<' | '\n' => return None,
                _ => {}
            }
        }
        return None;
    }
    let mut depth = 0usize;
    let mut chars = text.char_indices().peekable();
    while let Some((position, ch)) = chars.next() {
        match ch {
            '\\' if chars
                .peek()
                .is_some_and(|(_, next)| next.is_ascii_punctuation()) =>
            {
                chars.next();
            }
            '(' => depth += 1,
            ')' => depth = depth.checked_sub(1)?,
            ch if ch.is_whitespace() => return (depth == 0).then_some(position),
            ch if ch.is_ascii_control() || ch == '<' => return None,
            _ => {}
        }
    }
    (depth == 0).then_some(text.len())
}

fn valid_title(title: &str) -> bool {
    let Some(first) = title.chars().next() else {
        return false;
    };
    let closing = match first {
        '"' | '\'' => first,
        '(' => ')',
        _ => return false,
    };
    let Some(content) = title[1..].strip_suffix(closing) else {
        return false;
    };
    let mut chars = content.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch == '\\' && chars.peek().is_some_and(char::is_ascii_punctuation) {
            chars.next();
        } else if ch == closing || first == '(' && ch == '(' {
            return false;
        }
    }
    true
}

fn valid_link_target(text: &str, allow_empty: bool) -> bool {
    let text = text.trim();
    if text.is_empty() {
        return allow_empty;
    }
    let Some(end) = target_end(text) else {
        return false;
    };
    let suffix = &text[end..];
    suffix.is_empty() || (suffix.starts_with(char::is_whitespace) && valid_title(suffix.trim()))
}

fn fold_label(label: &str) -> String {
    label
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .case_fold()
        .collect()
}

struct Definition<'a> {
    label: String,
    target: &'a str,
    span: Span,
}

fn reference_definitions<'a>(text: &'a str, code: &[Span]) -> Vec<Definition<'a>> {
    rx!(r"(?m)^ {0,3}\[([^\]\n]+)\]:([^\n]*(?:\n[ \t]+[^\n]*)*)")
        .captures_iter(text)
        .filter_map(|entry| {
            let span = entry.get(0)?.range();
            let target = entry.get(2)?.as_str();
            let label = fold_label(&entry[1]);
            (valid_link_target(target, false)
                && !label.is_empty()
                && !code
                    .iter()
                    .any(|code| code.start < span.end && span.start < code.end))
            .then_some(Definition {
                label,
                target,
                span,
            })
        })
        .collect()
}

fn references<'a>(
    text: &'a str,
    code: &'a [Span],
) -> impl Iterator<Item = (regex::Captures<'a>, Span, String)> {
    rx!(r"\[([^\]\n]+)\](?:\[([^\]\n]*)\])?")
        .captures_iter(text)
        .filter_map(move |m| {
            let full = m.get(0)?;
            if !outside(full.start(), code)
                || text.as_bytes().get(full.start().wrapping_sub(1)) == Some(&b'!')
                || matches!(text.as_bytes().get(full.end()), Some(b'[' | b':' | b'('))
            {
                return None;
            }
            let label = fold_label(
                m.get(2)
                    .filter(|label| !label.is_empty())
                    .or_else(|| m.get(1))?
                    .as_str(),
            );
            Some((m, full.range(), label))
        })
}

fn blank_image_definitions(
    text: String,
    image_labels: &HashSet<String>,
    definitions: Vec<Definition<'_>>,
    code: &[Span],
) -> (String, Vec<Span>) {
    let ordinary: HashSet<_> = references(&text, code).map(|(_, _, label)| label).collect();
    let removed: Vec<_> = definitions
        .into_iter()
        .filter(|entry| image_labels.contains(&entry.label) && !ordinary.contains(&entry.label))
        .map(|entry| entry.span)
        .collect();
    (mask(&text, &removed), removed)
}

fn bind_references(mut text: String) -> String {
    let code = code_ranges(&text);
    let mut definitions = HashMap::new();
    for entry in reference_definitions(&text, &code) {
        definitions.entry(entry.label).or_insert_with(|| {
            entry
                .target
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ")
        });
    }
    let replacements: Vec<_> = references(&text, &code)
        .filter_map(|(m, span, label)| {
            definitions
                .get(&label)
                .map(|target| (span, format!("[{}]({target})", &m[1])))
        })
        .collect();
    for (span, value) in replacements.into_iter().rev() {
        text.replace_range(span, &value);
    }
    text
}

pub(super) fn body_view(
    text: &str,
    body_scope: &Value,
    explicit_legacy_repair: bool,
) -> Result<(String, HashSet<usize>), &'static str> {
    let original = text.replace("\r\n", "\n").replace('\r', "\n");
    let lines: Vec<_> = source_lines(&original, true).collect();
    let positions = line_starts(&original);
    let code = code_ranges(&original);
    let mut spans: Vec<_> = declared_regions(&original, body_scope, &positions)?
        .into_iter()
        .map(|region| region.opening..region.end)
        .collect();
    generated_ranges(&original, &code, &mut spans)?;
    if explicit_legacy_repair {
        legacy_generated_ranges(&original, &code, &mut spans)?;
    }
    let generated = mask(&original, &spans);
    let definitions = reference_definitions(&generated, &code);
    let labels = definitions
        .iter()
        .map(|entry| entry.label.as_str())
        .collect();
    let (images, image_labels) = image_ranges(&generated, &code, &labels);
    spans.extend(images.iter().cloned());
    let masked = mask(&generated, &images);
    let masked = blank_image_links(masked, &images, &code);
    let masked = blank_image_wrappers(masked, &images, &code);
    let (masked, removed) = blank_image_definitions(masked, &image_labels, definitions, &code);
    spans.extend(removed);
    // During syntax analysis preserve byte offsets. Restore the character count
    // for the exposed source text once span offsets are no longer needed.
    let mut projected = String::with_capacity(masked.len());
    for (pos, ch) in original.char_indices() {
        if masked.as_bytes()[pos] == b' ' && ch != ' ' {
            projected.push(' ');
        } else {
            projected.push(ch);
        }
    }
    let projected = bind_references(projected);
    let mut excluded_lines = HashSet::new();
    for span in spans {
        let start = line_number(&positions, span.start) - 1;
        let end = line_number(&positions, span.end.saturating_sub(1)) - 1;
        excluded_lines.extend(start..=end);
    }
    let ignored = source_lines(&projected, false)
        .enumerate()
        .filter_map(|(index, line)| {
            (line.trim().is_empty()
                && (excluded_lines.contains(&index) || !lines[index].trim().is_empty()))
            .then_some(index)
        })
        .collect();
    Ok((projected, ignored))
}

#[cfg(test)]
mod tests {
    use super::body_view;
    use serde_json::{Value, json};

    #[test]
    fn image_reference_definitions_do_not_hide_literal_code() {
        let body = "![Chart][fig]\n\n[fig]: chart.png\n\n~~~text\n[fig]: value-10\n~~~\n";
        let (view, _) = body_view(body, &Value::Null, false).unwrap();
        assert!(view.contains("[fig]: value-10"));
        assert!(!view.contains("chart.png"));

        let only_code = "![Refunds allowed][fig]\n\n~~~text\n[fig]: sample.png\n~~~\n";
        let (view, _) = body_view(only_code, &Value::Null, false).unwrap();
        assert!(view.contains("![Refunds allowed][fig]"));
    }

    #[test]
    fn unresolved_and_malformed_images_remain_authored_text() {
        let body = "![Refunds are allowed]\n\n![Capped refunds][missing]\n\n![Invalid](some bad target)\n\n![Unclosed](target\n";
        let (view, _) = body_view(body, &Value::Null, false).unwrap();
        assert_eq!(view, body);
    }

    #[test]
    fn self_closing_svg_does_not_claim_a_following_svg() {
        let body = "<svg/>\n\nKeep this policy.\n\n<svg><text>diagram</text></svg>";
        let (view, _) = body_view(body, &Value::Null, false).unwrap();
        assert!(view.contains("Keep this policy."));
        assert!(!view.contains("diagram"));
    }

    #[test]
    fn generated_scope_preserves_unicode_line_boundaries() {
        let text = "Intro.\u{2028}<!-- mdocr:ocr-begin -->\u{85}hidden\u{2028}<!-- mdocr:ocr-end -->\u{2028}Policy.";
        let declared = json!({
            "version":"generated-regions-v1",
            "regions":[{"kind":"mdocr_ocr","start_line":2,"end_line":4}],
        });
        for scope in [Value::Null, declared] {
            let (view, _) = body_view(text, &scope, false).unwrap();
            assert!(view.ends_with("\u{2028}Policy."));
            assert_eq!(view.matches('\u{2028}').count(), 3);
            assert_eq!(view.matches('\u{85}').count(), 1);
            assert!(!view.contains("hidden"));
        }
    }
}
