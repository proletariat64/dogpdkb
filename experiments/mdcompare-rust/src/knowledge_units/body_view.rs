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
    !spans
        .get(spans.partition_point(|span| span.end <= position))
        .is_some_and(|span| span.contains(&position))
}

fn overlaps(area: &Span, spans: &[Span]) -> bool {
    spans
        .get(spans.partition_point(|span| span.end <= area.start))
        .is_some_and(|span| span.start < area.end)
}

fn merge_spans(mut spans: Vec<Span>) -> Vec<Span> {
    spans.sort_unstable_by_key(|span| span.start);
    let mut merged: Vec<Span> = Vec::new();
    for span in spans {
        if let Some(last) = merged.last_mut()
            && span.start < last.end
        {
            last.end = last.end.max(span.end);
        } else {
            merged.push(span);
        }
    }
    merged
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
    let mut prose = None;
    for line in source_lines(text, true) {
        let line_value = line.trim_end_matches(is_source_line_end);
        let delimiter = fence().captures(line_value).and_then(|m| m.get(1));
        let is_code = open.is_some() || delimiter.is_some() || is_indented(line_value);
        let boundary = is_code || line_value.trim().is_empty();
        if boundary {
            if let Some(start) = prose.take() {
                spans.extend(
                    inline_code(&text[start..offset], true)
                        .into_iter()
                        .map(|span| start + span.start..start + span.end),
                );
            }
        } else if prose.is_none() {
            prose = Some(offset);
        }
        if let Some((start, delimiter)) = open {
            if fence_end(line_value, delimiter) {
                spans.push(start..offset + line.len());
                open = None;
            }
        } else if let Some(delimiter) = delimiter {
            open = Some((offset, delimiter.as_str()));
        } else if is_indented(line) {
            spans.push(offset..offset + line.len());
        }
        offset += line.len();
    }
    if let Some((start, _)) = open {
        spans.push(start..text.len());
    }
    if let Some(start) = prose {
        spans.extend(
            inline_code(&text[start..], true)
                .into_iter()
                .map(|span| start + span.start..start + span.end),
        );
    }
    merge_spans(spans)
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
    let markers_by_line = |pattern: &'static Regex| {
        let mut markers = HashMap::new();
        for entry in pattern.find_iter(text) {
            markers
                .entry(line_number(line_starts, entry.start()))
                .and_modify(|previous| *previous = None)
                .or_insert(Some(entry));
        }
        markers
    };
    let begins = markers_by_line(ocr_begin());
    let ends = markers_by_line(ocr_end());
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
        match (begins.get(&start), ends.get(&end)) {
            (Some(Some(a)), Some(Some(b))) if a.end() <= b.start() => regions.push(Region {
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
        .filter(|(m, _)| outside(m.start(), code) && outside(m.start(), spans))
        .collect();
    markers.sort_unstable_by_key(|(m, _)| m.start());
    let mut open = None;
    let mut generated = Vec::new();
    for (m, begin) in markers {
        if begin {
            if open.replace(m.start()).is_some() {
                return Err(BOUNDARY);
            }
        } else {
            let area = open.take().ok_or(BOUNDARY)?..m.end();
            if overlaps(&area, spans) {
                return Err(BOUNDARY);
            }
            generated.push(area);
        }
    }
    if open.is_some() {
        return Err(UNCLOSED);
    }
    spans.extend(generated);
    *spans = merge_spans(std::mem::take(spans));
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
    *spans = merge_spans(std::mem::take(spans));
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
    let mut html_spans = Vec::new();
    let mut elements = Vec::<(String, usize)>::new();
    for m in html_tags().captures_iter(text) {
        let full = m.get(0).expect("tag match");
        html_spans.push(full.range());
        if !outside(full.start(), code) {
            continue;
        }
        let tag = m["tag"].to_ascii_lowercase();
        if matches!(tag.as_str(), "picture" | "svg") {
            if m.name("closing").is_some() {
                if elements.last().is_some_and(|(name, _)| *name == tag) {
                    let (_, start) = elements.pop().expect("matching opening");
                    spans.push(start..full.end());
                } else {
                    // A mismatched or absent closer does not prove image scope.
                    elements.clear();
                }
            } else if full.as_str().ends_with("/>") {
                spans.push(full.range());
            } else {
                elements.push((tag, full.start()));
            }
        } else if m.name("closing").is_none() && matches!(tag.as_str(), "img" | "source") {
            spans.push(full.range());
        }
    }
    let mut labels = HashSet::new();
    for (start, _) in text.match_indices("![") {
        if escaped(text, start) || !outside(start, code) || !outside(start, &html_spans) {
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
    (merge_spans(spans), labels)
}

fn escaped(text: &str, start: usize) -> bool {
    text.as_bytes()[..start]
        .iter()
        .rev()
        .take_while(|&&byte| byte == b'\\')
        .count()
        % 2
        != 0
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
    !overlaps(&area, code)
        && images
            .get(images.partition_point(|span| span.start < content.start))
            .is_some_and(|span| span.end <= content.end)
}

fn blank_image_links(text: String, images: &[Span], code: &[Span]) -> String {
    let spans: Vec<_> = rx!(r"\[(\s*)\]\(")
        .captures_iter(&text)
        .filter_map(|m| {
            let prefix = m.get(0)?;
            let content = m.get(1)?.range();
            let opening = prefix.end() - 1;
            let end = closing(&text, opening, b'(', b')')?;
            if !valid_link_target(&text[opening + 1..end], true) || escaped(&text, prefix.start()) {
                return None;
            }
            let area = prefix.start()..end + 1;
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
    rx!(r"(?m)^ {0,3}\[([^\]\r\n\x0B\x0C\x1C-\x1E\u{85}\u{2028}\u{2029}]+)\]:[ \t]*")
        .captures_iter(text)
        .filter_map(|entry| {
            let marker = entry.get(0)?;
            let label = fold_label(&entry[1]);
            if label.is_empty() || !outside(marker.start(), code) {
                return None;
            }
            let target_start = marker.end();
            let target_end = target_start + definition_target_end(&text[target_start..])?;
            Some(Definition {
                label,
                target: &text[target_start..target_end],
                span: marker.start()..target_end,
            })
        })
        .collect()
}

fn definition_target_end(text: &str) -> Option<usize> {
    let mut lines = source_lines(text, true);
    let first = lines.next()?;
    let mut line = first;
    let mut preceding = 0;
    if first.trim().is_empty() {
        line = lines.next()?;
        if !line.starts_with([' ', '\t']) {
            return None;
        }
        preceding = first.len();
    }
    let value = line.trim_end_matches(is_source_line_end);
    if !valid_link_target(value, false) {
        return None;
    }
    let end = preceding + value.len();
    // A title can follow on its own continuation line. Additional indented
    // body lines are not part of the definition.
    if target_end(value.trim()) == Some(value.trim().len())
        && let Some(title) = lines.next()
        && title.starts_with([' ', '\t'])
        && valid_title(title.trim())
    {
        return Some(preceding + line.len() + title.trim_end_matches(is_source_line_end).len());
    }
    Some(end)
}

fn references<'a>(
    text: &'a str,
    code: &'a [Span],
) -> impl Iterator<Item = (regex::Captures<'a>, Span, String)> {
    // Reference binding changes the label spelling; it must not change the
    // number of source lines represented in the projected document.
    rx!(r"\[([^\]\r\n\x0B\x0C\x1C-\x1E\u{85}\u{2028}\u{2029}]+)\](?:\[([^\]\r\n\x0B\x0C\x1C-\x1E\u{85}\u{2028}\u{2029}]*)\])?")
        .captures_iter(text)
        .filter_map(move |m| {
            let full = m.get(0)?;
            let image = full.start().checked_sub(1).is_some_and(|before| {
                text.as_bytes()[before] == b'!' && !escaped(text, before)
            });
            if !outside(full.start(), code)
                || escaped(text, full.start())
                || image
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

fn bind_references(
    masked: &str,
    original: &str,
    code: &[Span],
    limit: usize,
) -> Result<String, &'static str> {
    const BUDGET: &str = "BODY_REFERENCE_BUDGET_EXCEEDED";
    let mut definitions = HashMap::new();
    for entry in reference_definitions(masked, code) {
        definitions.entry(entry.label).or_insert_with(|| {
            entry
                .target
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ")
        });
    }
    let mut output = String::with_capacity(original.len().min(limit));
    let append = |output: &mut String, range: Span| {
        for (pos, ch) in original[range.clone()].char_indices() {
            let value = if masked.as_bytes()[range.start + pos] == b' ' && ch != ' ' {
                ' '
            } else {
                ch
            };
            if value.len_utf8() > limit - output.len() {
                return Err(BUDGET);
            }
            output.push(value);
        }
        Ok(())
    };
    let mut cursor = 0;
    for (capture, span, label) in references(masked, code) {
        let Some(target) = definitions.get(&label) else {
            continue;
        };
        append(&mut output, cursor..span.start)?;
        let label_span = capture.get(1).expect("matched reference label").range();
        if label_span
            .len()
            .saturating_add(target.len())
            .saturating_add(4)
            > limit - output.len()
        {
            return Err(BUDGET);
        }
        output.push('[');
        append(&mut output, label_span)?;
        output.push_str("](");
        output.push_str(target);
        output.push(')');
        cursor = span.end;
    }
    append(&mut output, cursor..original.len())?;
    Ok(output)
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
    let mut spans = merge_spans(
        declared_regions(&original, body_scope, &positions)?
            .into_iter()
            .map(|region| region.opening..region.end)
            .collect(),
    );
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
    // Bind in original byte coordinates, then restore the character counts of
    // masked text while constructing the bounded projected output in one pass.
    let projected = bind_references(
        &masked,
        &original,
        &code,
        original.len().max(crate::input::MANIFEST_LIMIT as usize),
    )?;
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
    use super::{bind_references, body_view, code_ranges};
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

    #[test]
    fn masked_image_does_not_turn_ordinary_reference_into_literal_code() {
        let body = "![图](chart.png) Read [policy][p].\n\n[p]: /guide";
        let (view, _) = body_view(body, &Value::Null, false).unwrap();
        assert!(view.contains("Read [policy](/guide)."));
    }

    #[test]
    fn image_only_link_masks_balanced_destination_completely() {
        let body = "[![chart](chart.png)](reports/2026(final).pdf)\n\nPolicy.";
        let (view, _) = body_view(body, &Value::Null, false).unwrap();
        assert!(!view.contains("reports"));
        assert!(!view.contains(".pdf)"));
        assert!(view.contains("Policy."));
    }

    #[test]
    fn escaped_reference_bracket_remains_literal() {
        let body = "![chart][fig]\n\n\\[fig]\n\n[fig]: chart.png";
        let (view, _) = body_view(body, &Value::Null, false).unwrap();
        assert!(view.contains("\\[fig]"));
        assert!(!view.contains("chart.png"));

        let ordinary = "\\![fig]\n\n[fig]: chart.png";
        let (view, _) = body_view(ordinary, &Value::Null, false).unwrap();
        assert!(view.contains("\\![fig](chart.png)"));
    }

    #[test]
    fn reference_definitions_use_only_valid_continuations() {
        for definition in [
            "[fig]: chart.png",
            "[fig]:\n    chart.png\n    \"Annual chart\"",
        ] {
            let body = format!("![chart][fig]\n\n{definition}\n    Example code.");
            let (view, _) = body_view(&body, &Value::Null, false).unwrap();
            assert!(!view.contains("chart.png"));
            assert!(view.contains("    Example code."));
        }
    }

    #[test]
    fn reference_binding_preserves_unicode_source_line_boundaries() {
        let body = "[Read][policy\u{2028}plan]\n\n[policy plan]: /guide\n\nTail.";
        let (view, _) = body_view(body, &Value::Null, false).unwrap();
        assert_eq!(view, body);
    }

    #[test]
    fn unclosed_or_nested_image_elements_are_scoped_by_confirmed_closers() {
        let body = "<svg><text>Outer diagram</text><svg><text>Nested diagram</text></svg><text>Outer caption</text></svg>\n\nPolicy.";
        let (view, _) = body_view(body, &Value::Null, false).unwrap();
        assert!(!view.contains("Outer caption"));
        assert!(view.contains("Policy."));

        let body = "<picture>\n\nPolicy.\n\n~~~html\n</picture>\n~~~\n";
        let (view, _) = body_view(body, &Value::Null, false).unwrap();
        assert!(view.contains("Policy."));
        assert!(view.contains("</picture>"));
    }

    #[test]
    fn block_code_delimiters_do_not_pair_with_prose_backticks() {
        let body = "~~~text\n`\n~~~\n\n<!-- mdocr:ocr-begin -->\nGenerated text.\n<!-- mdocr:ocr-end -->\n\nPolicy ` noted.";
        let (view, _) = body_view(body, &Value::Null, false).unwrap();
        assert!(!view.contains("Generated text."));
        assert!(view.contains("Policy ` noted."));
        let prose = "Example `![literal](diagram.png)\ncontinued` text.";
        let (view, _) = body_view(prose, &Value::Null, false).unwrap();
        assert_eq!(view, prose);
    }

    #[test]
    fn reference_output_limit_is_checked_before_expansion() {
        let text = "[Help][guide]\n\n[guide]: /team/long/path\n";
        let code = code_ranges(text);
        assert_eq!(
            bind_references(text, text, &code, text.len()).unwrap_err(),
            "BODY_REFERENCE_BUDGET_EXCEEDED"
        );
        assert!(
            bind_references(text, text, &code, 100)
                .unwrap()
                .contains("[Help](/team/long/path)")
        );
    }

    #[test]
    fn declared_region_owns_both_of_its_markers() {
        let body = "    <!-- mdocr:ocr-begin -->\n    generated\n<!-- mdocr:ocr-end -->\n\nPolicy.";
        let declaration = json!({
            "version":"generated-regions-v1",
            "regions":[{"kind":"mdocr_ocr","start_line":1,"end_line":3}],
        });
        let (view, _) = body_view(body, &declaration, false).unwrap();
        assert!(!view.contains("generated"));
        assert!(view.contains("Policy."));
    }
}
