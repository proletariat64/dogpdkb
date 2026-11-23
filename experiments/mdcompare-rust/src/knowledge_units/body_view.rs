use std::collections::{HashMap, HashSet};
use std::ops::Range;

use regex::Regex;
use serde_json::Value;
use unicode_casefold::UnicodeCaseFold;

use super::formatting::{attributes, cosmetic_attrs, html_tags};
use super::{fence, fence_end, is_indented, source_lines};

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

fn line_number(newlines: &[usize], pos: usize) -> usize {
    newlines.partition_point(|&newline| newline < pos) + 1
}

fn declared_ranges(
    text: &str,
    declaration: &Value,
    newlines: &[usize],
    line_count: usize,
) -> Result<Vec<Span>, &'static str> {
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
    let mut spans = Vec::new();
    for region in declaration["regions"].as_array().ok_or(INVALID)? {
        if region["kind"] != "mdocr_ocr" {
            return Err(INVALID);
        }
        let start = region["start_line"].as_u64().ok_or(LOCATION)? as usize;
        let end = region["end_line"].as_u64().ok_or(LOCATION)? as usize;
        if start < 1 || end < start || end > line_count {
            return Err(LOCATION);
        }
        let start_matches: Vec<_> = begins
            .iter()
            .filter(|m| line_number(newlines, m.start()) == start)
            .collect();
        let end_matches: Vec<_> = ends
            .iter()
            .filter(|m| line_number(newlines, m.start()) == end)
            .collect();
        match (start_matches.as_slice(), end_matches.as_slice()) {
            ([a], [b]) if a.end() <= b.start() => spans.push(a.start()..b.end()),
            _ => return Err(LOCATION),
        }
    }
    spans.sort_by_key(|span| span.start);
    if spans.windows(2).any(|pair| pair[0].end > pair[1].start) {
        return Err(LOCATION);
    }
    Ok(spans)
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
    let legacy_comments: Vec<_> = rx!(r"(?is)<!--\s*这是一张图片[，,]\s*ocr\s*内容为[：:].*?-->")
        .find_iter(text)
        .filter(|m| outside(m.start(), code))
        .collect();
    let complete_starts: HashSet<_> = legacy_comments.iter().map(|m| m.start()).collect();
    spans.extend(legacy_comments.iter().map(|m| m.range()));
    if rx!(r"(?i)<!--\s*这是一张图片[，,]\s*ocr\s*内容为[：:]")
        .find_iter(text)
        .any(|m| outside(m.start(), code) && !complete_starts.contains(&m.start()))
    {
        return Err(UNCLOSED);
    }
    let legacy_blocks: Vec<_> = rx!(r"(?m)^\s*>\s*\*\*图片转写[：:]\*\*[^\n]*")
        .find_iter(text)
        .collect();
    for (index, begin) in legacy_blocks.iter().enumerate() {
        if !outside(begin.start(), code) {
            continue;
        }
        let limit = legacy_blocks
            .get(index + 1)
            .map_or(text.len(), |m| m.start());
        let end = rx!(r"(?m)^\s*>\s*(?:\*\*)?转写说明[：:](?:\*\*)?[^\n]*")
            .find_at(text, begin.end())
            .filter(|m| m.end() <= limit)
            .ok_or(UNCLOSED)?;
        spans.push(begin.start()..end.end());
    }
    Ok(())
}

fn image_ranges(text: &str, code: &[Span]) -> (Vec<Span>, HashSet<String>) {
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
            "picture" | "svg" => {
                let end = captures
                    .iter()
                    .filter(|other| {
                        other.name("closing").is_some()
                            && other["tag"].eq_ignore_ascii_case(&m["tag"])
                    })
                    .filter_map(|other| other.get(0))
                    .find(|other| other.start() >= full.end())
                    .map_or_else(
                        || {
                            if full.as_str().trim_end().ends_with("/>") {
                                full.end()
                            } else {
                                text.len()
                            }
                        },
                        |other| other.end(),
                    );
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
        let mut end = close + 1;
        match text.as_bytes().get(end) {
            Some(&open @ (b'(' | b'[')) => {
                if let Some(stop) = closing(text, end, open, if open == b'[' { b']' } else { b')' })
                {
                    if open == b'[' {
                        let id = text[end + 1..stop].trim();
                        labels.insert(fold_label(if id.is_empty() { label } else { id }));
                    }
                    end = stop + 1;
                }
            }
            _ => {
                labels.insert(fold_label(label));
            }
        }
        spans.push(start..end);
    }
    (spans, labels)
}

fn mask(text: &str, spans: &[Span]) -> String {
    let mut bytes = text.as_bytes().to_vec();
    for span in spans {
        for byte in &mut bytes[span.clone()] {
            if *byte != b'\n' {
                *byte = b' ';
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

fn reference_definitions() -> &'static Regex {
    rx!(r"(?m)^ {0,3}\[([^\]\n]+)\]:([^\n]*(?:\n[ \t]+[^\n]*)*)")
}

fn fold_label(label: &str) -> String {
    label.trim().case_fold().collect()
}

fn bind_references(mut text: String, image_labels: &HashSet<String>) -> String {
    let reference = rx!(r"\[([^\]\n]+)\](?:\[([^\]\n]*)\])?");
    let label = |m: &regex::Captures<'_>| {
        fold_label(
            m.get(2)
                .filter(|label| !label.is_empty())
                .or_else(|| m.get(1))
                .expect("reference label")
                .as_str(),
        )
    };
    let ordinary: HashSet<_> = reference
        .captures_iter(&text)
        .filter_map(|m| {
            let full = m.get(0)?;
            (text.as_bytes().get(full.start().wrapping_sub(1)) != Some(&b'!')
                && (m.get(2).is_some()
                    || !matches!(text.as_bytes().get(full.end()), Some(b'[' | b':' | b'('))))
            .then(|| label(&m))
        })
        .collect();
    let excluded: Vec<_> = reference_definitions()
        .captures_iter(&text)
        .filter_map(|m| {
            let full = m.get(0)?;
            let label = fold_label(&m[1]);
            (image_labels.contains(&label) && !ordinary.contains(&label)).then(|| full.range())
        })
        .collect();
    text = mask(&text, &excluded);

    let mut definitions = HashMap::new();
    for m in reference_definitions().captures_iter(&text) {
        definitions
            .entry(fold_label(&m[1]))
            .or_insert_with(|| m[2].split_whitespace().collect::<Vec<_>>().join(" "));
    }
    let code = code_ranges(&text);
    let replacements: Vec<_> = reference
        .captures_iter(&text)
        .filter_map(|m| {
            let full = m.get(0)?;
            if !outside(full.start(), &code)
                || text.as_bytes().get(full.start().wrapping_sub(1)) == Some(&b'!')
                || matches!(text.as_bytes().get(full.end()), Some(b':' | b'('))
            {
                return None;
            }
            definitions
                .get(&label(&m))
                .map(|target| (full.range(), format!("[{}]({target})", &m[1])))
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
) -> Result<(String, HashSet<usize>), &'static str> {
    let original = text.replace("\r\n", "\n").replace('\r', "\n");
    let lines: Vec<_> = source_lines(&original, false).collect();
    let newlines: Vec<_> = original.match_indices('\n').map(|(pos, _)| pos).collect();
    let code = code_ranges(&original);
    let mut spans = declared_ranges(&original, body_scope, &newlines, lines.len())?;
    generated_ranges(&original, &code, &mut spans)?;
    let (images, image_labels) = image_ranges(&original, &code);
    spans.extend(images.iter().cloned());
    let masked = mask(&original, &spans);
    let masked = blank_image_links(masked, &images, &code);
    let masked = blank_image_wrappers(masked, &images, &code);
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
    let projected = bind_references(projected, &image_labels);
    let mut excluded_lines = HashSet::new();
    for span in spans {
        let start = newlines.partition_point(|&offset| offset <= span.start);
        let end = newlines.partition_point(|&offset| offset <= span.end);
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
