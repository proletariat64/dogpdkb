use super::{BUDGET, MAX_REFERENCES, SYNTAX, download};
use regex::Regex;
use std::borrow::Cow;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;
use unicode_casefold::UnicodeCaseFold;

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct Use {
    pub(super) offset: usize,
    pub(crate) kind: &'static str,
    pub(crate) url: String,
    alt: String,
    title: Option<String>,
}

impl Use {
    fn new(offset: usize, kind: &'static str, url: &str, alt: &str, title: Option<&str>) -> Self {
        Self {
            offset,
            kind,
            url: url.into(),
            alt: alt.into(),
            title: title.map(str::to_owned),
        }
    }
}

fn label(value: &str) -> String {
    value
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .case_fold()
        .collect()
}

fn escaped(text: &str, offset: usize) -> bool {
    text.as_bytes()[..offset]
        .iter()
        .rev()
        .take_while(|&&b| b == b'\\')
        .count()
        % 2
        != 0
}

fn mask_spans(line: &str, output: &mut String) {
    static TICKS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\x60+").unwrap());
    static ANGLE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<[^>\r\n]*(?:>|$)").unwrap());
    let runs: Vec<_> = TICKS.find_iter(line).collect();
    let mut following = BTreeMap::new();
    let mut closers = vec![None; runs.len()];
    for (index, run) in runs.iter().enumerate().rev() {
        closers[index] = following.insert(run.len(), index);
    }
    let mut angles = ANGLE.find_iter(line).peekable();
    let mut index = 0;
    let mut previous = 0;
    while index < runs.len() {
        let start = runs[index].start();
        while angles.peek().is_some_and(|angle| angle.end() <= start) {
            angles.next();
        }
        if let Some(end) = closers[index]
            && !escaped(line, start)
            && angles.peek().is_none_or(|angle| angle.start() > start)
        {
            let end_offset = runs[end].end();
            output.push_str(&line[previous..start]);
            output.extend(std::iter::repeat_n(
                ' ',
                line[start..end_offset].chars().count(),
            ));
            previous = end_offset;
            index = end + 1;
        } else {
            index += 1;
        }
    }
    output.push_str(&line[previous..]);
}

fn mask_code(text: &str) -> String {
    static FENCE_OPEN: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"^(\x60{3,}|~{3,})([^\r\n]*)$").unwrap());
    static FENCE_CLOSE: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"^ {0,3}(\x60+|~+)[ \t]*$").unwrap());
    static HTML_START: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^ {0,3}<").unwrap());
    let mut output = String::with_capacity(text.len());
    let mut fence: Option<&str> = None;
    let mut prior_html = false;
    for (index, line) in text.split('\n').enumerate() {
        if index != 0 {
            output.push('\n');
        }
        if let Some(open) = fence {
            if let Some(marker) = FENCE_CLOSE.captures(line).and_then(|cap| cap.get(1))
                && marker.len() >= open.len()
                && marker.as_str().as_bytes()[0] == open.as_bytes()[0]
            {
                fence = None;
            }
            output.extend(std::iter::repeat_n(' ', line.chars().count()));
        } else if let Some(cap) = FENCE_OPEN.captures(line)
            && !prior_html
            && (cap[1].starts_with('~') || !cap[2].contains('\x60'))
        {
            fence = cap.get(1).map(|marker| marker.as_str());
            output.extend(std::iter::repeat_n(' ', line.chars().count()));
        } else {
            if !prior_html && !HTML_START.is_match(line) {
                mask_spans(line, &mut output);
            } else {
                output.push_str(line);
            }
            prior_html |= line.contains('<');
        }
    }
    output
}

const RESOURCE_TAGS: &[&str] = &[
    "img", "video", "audio", "source", "iframe", "object", "embed", "script", "link", "image",
    "use", "input", "style",
];
const MEDIA_TAGS: &[&str] = &[
    "img", "video", "audio", "source", "iframe", "embed", "script",
];
const LINK_TAGS: &[&str] = &["a", "link", "image", "use"];
const SVG_TAGS: &[&str] = &["image", "use"];

fn named_entity(name: &str) -> Option<&'static str> {
    let entities = &html_escape::NAMED_ENTITIES;
    let index = entities
        .binary_search_by_key(&name.as_bytes(), |(name, _)| *name)
        .ok()?;
    Some(entities[index].1)
}

fn numeric_entity(digits: &str) -> Option<char> {
    const C1: [char; 32] = [
        '€', '\u{81}', '‚', 'ƒ', '„', '…', '†', '‡', 'ˆ', '‰', 'Š', '‹', 'Œ', '\u{8d}', 'Ž',
        '\u{8f}', '\u{90}', '‘', '’', '“', '”', '•', '–', '—', '˜', '™', 'š', '›', 'œ', '\u{9d}',
        'ž', 'Ÿ',
    ];
    let digits = digits.trim_end_matches(';');
    let (radix, digits) = digits
        .strip_prefix(['x', 'X'])
        .map_or((10, digits), |hex| (16, hex));
    let nonzero = digits.trim_start_matches('0');
    let value = if nonzero.is_empty() {
        0
    } else {
        u32::from_str_radix(nonzero, radix).unwrap_or(u32::MAX)
    };
    let replacement = '\u{fffd}';
    match value {
        0 => Some(replacement),
        0x80..=0x9f => Some(C1[(value - 0x80) as usize]),
        1..=8 | 11 | 14..=31 | 127 | 0xfdd0..=0xfdef => None,
        0..=0x10ffff if value & 0xffff >= 0xfffe => None,
        _ => Some(char::from_u32(value).unwrap_or(replacement)),
    }
}

fn html_unescape(value: &str) -> Cow<'_, str> {
    static REF: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)").unwrap()
    });
    // HTML5 retains these legacy references without a semicolon.
    static LEGACY: LazyLock<BTreeSet<&str>> = LazyLock::new(|| {
        concat!(
            "Aacute aacute Acirc acirc acute AElig aelig Agrave agrave AMP amp Aring aring Atilde atilde ",
            "Auml auml brvbar Ccedil ccedil cedil cent COPY copy curren deg divide Eacute eacute ",
            "Ecirc ecirc Egrave egrave ETH eth Euml euml frac12 frac14 frac34 GT gt Iacute iacute ",
            "Icirc icirc iexcl Igrave igrave iquest Iuml iuml laquo LT lt macr micro middot nbsp not ",
            "Ntilde ntilde Oacute oacute Ocirc ocirc Ograve ograve ordf ordm Oslash oslash Otilde otilde ",
            "Ouml ouml para plusmn pound QUOT quot raquo REG reg sect shy sup1 sup2 sup3 szlig THORN thorn ",
            "times Uacute uacute Ucirc ucirc Ugrave ugrave uml Uuml uuml Yacute yacute yen yuml",
        )
        .split_whitespace()
        .collect()
    });
    REF.replace_all(value, |cap: &regex::Captures<'_>| {
        let reference = &cap[1];
        if let Some(digits) = reference.strip_prefix('#') {
            return numeric_entity(digits).map_or_else(String::new, |ch| ch.to_string());
        }
        if let Some(value) = reference.strip_suffix(';').and_then(named_entity) {
            return value.to_owned();
        }
        let ends = std::iter::once(reference.len())
            .chain(reference.char_indices().rev().map(|(pos, _)| pos));
        for end in ends.take_while(|end| *end >= 2) {
            let (prefix, suffix) = reference.split_at(end);
            if LEGACY.contains(prefix)
                && let Some(value) = named_entity(prefix)
            {
                return format!("{value}{suffix}");
            }
        }
        cap[0].to_owned()
    })
}

fn capture_attrs(
    start: usize,
    tag: &str,
    attrs: &str,
    uses: &mut Vec<Use>,
    reasons: &mut Vec<&'static str>,
) {
    static ATTR: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?s)([^\s/=>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?"#).unwrap()
    });
    let mut pairs = BTreeMap::new();
    let relevant = RESOURCE_TAGS.contains(&tag);
    for cap in ATTR.captures_iter(attrs) {
        let name = cap[1].to_ascii_lowercase();
        let value = cap
            .get(2)
            .or_else(|| cap.get(3))
            .or_else(|| cap.get(4))
            .map_or("", |value| value.as_str());
        if pairs.insert(name, html_unescape(value)).is_some() && relevant {
            reasons.push(SYNTAX);
        }
    }
    let unverified = pairs.keys().any(|name| match name.as_str() {
        "src" | "srcset" | "poster" => !MEDIA_TAGS.contains(&tag),
        "data" => tag != "object",
        "href" => !LINK_TAGS.contains(&tag),
        "xlink:href" => !SVG_TAGS.contains(&tag),
        "style" | "srcdoc" | "background" | "manifest" | "archive" | "codebase" | "classid" => true,
        _ => name.starts_with("on") || name.ends_with(":src") || name.ends_with(":href"),
    });
    if unverified || tag.contains('-') {
        reasons.push(SYNTAX);
    }
    let get = |name: &str| pairs.get(name).map(|value| value.as_ref());
    let nonempty = |name: &str| get(name).filter(|value| !value.is_empty());
    let original_len = uses.len();
    let mut push = |kind, target| {
        if uses.len() <= MAX_REFERENCES {
            uses.push(Use::new(
                start,
                kind,
                target,
                get("alt").unwrap_or_default(),
                get("title"),
            ));
        }
    };
    if MEDIA_TAGS.contains(&tag) {
        if let Some(src) = nonempty("src") {
            push(if tag == "img" { "image" } else { "media" }, src);
        }
        if let Some(srcset) = nonempty("srcset") {
            if srcset.contains("data:") {
                reasons.push(SYNTAX);
            } else {
                for src in srcset
                    .split(',')
                    .filter_map(|part| part.split_whitespace().next())
                {
                    push("image", src);
                }
            }
        }
        if let Some(poster) = nonempty("poster") {
            push("image", poster);
        }
    } else if tag == "object" {
        if let Some(target) = nonempty("data") {
            push("media", target);
        }
    } else if SVG_TAGS.contains(&tag) {
        if let Some(target) = nonempty("href").or_else(|| nonempty("xlink:href"))
            && !target.starts_with('#')
        {
            push("image", target);
        }
    } else if let Some(target) = nonempty("href")
        && (tag == "link" || tag == "a" && (pairs.contains_key("download") || download(target)))
    {
        push("attachment", target);
    }
    if relevant && uses.len() == original_len {
        reasons.push(SYNTAX);
    }
}

fn html_uses(text: &str, uses: &mut Vec<Use>, reasons: &mut Vec<&'static str>) -> Option<usize> {
    static TAG: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?s)^<([A-Za-z][^\s/<>]*)([^<>"']*(?:"[^"]*"[^<>"']*|'[^']*'[^<>"']*)*)>"#)
            .unwrap()
    });
    static SCRIPT_END: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(?i)</\s*script\s*>").unwrap());
    static STYLE_END: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(?i)</\s*style\s*>").unwrap());
    static OPEN_QUOTE: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r#"=\s*(?:(")[^"]*$|(')[^']*$)"#).unwrap());
    let mut cursor = 0;
    let mut tags = 0;
    while let Some(offset) = text[cursor..].find('<') {
        let start = cursor + offset;
        let tail = &text[start..];
        if let Some(comment) = tail.strip_prefix("<!--") {
            cursor = comment
                .find("-->")
                .map_or(text.len(), |end| start + 4 + end + 3);
            continue;
        }
        let Some(cap) = TAG.captures(tail) else {
            // A malformed tag can still carry unsupported attributes. Do not
            // claim absence of dependencies based on a failed lexical match.
            if tail.as_bytes().get(1).is_some_and(u8::is_ascii_alphabetic) {
                let raw = &tail[1..];
                let boundary = raw.find(['<', '>']).unwrap_or(raw.len());
                if boundary < raw.len() && raw[..boundary].contains('=') {
                    reasons.push(SYNTAX);
                    let quote = OPEN_QUOTE
                        .captures(&raw[..boundary])
                        .and_then(|cap| cap.get(1).or_else(|| cap.get(2)));
                    if quote.is_some_and(|quote| !raw[boundary..].contains(quote.as_str())) {
                        return None;
                    }
                }
            }
            cursor = start + 1;
            continue;
        };
        let tag = cap[1].to_ascii_lowercase();
        cursor = start + cap.get(0).unwrap().end();
        if RESOURCE_TAGS.contains(&tag.as_str()) {
            tags += 1;
        }
        capture_attrs(start, &tag, &cap[2], uses, reasons);
        let end = match tag.as_str() {
            "script" => Some(&*SCRIPT_END),
            "style" => Some(&*STYLE_END),
            _ => None,
        };
        if let Some(end) = end
            && !cap[2].ends_with('/')
        {
            cursor = end
                .find_at(text, cursor)
                .map_or(text.len(), |marker| marker.end());
        }
    }
    Some(tags)
}

pub(crate) fn uses(data: &[u8], declared: &BTreeSet<&str>) -> (Vec<Use>, Vec<&'static str>) {
    static RESOURCE_TAG: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)<(img|video|audio|source|iframe|object|embed|script|link|image|use|input|style)\b").unwrap()
    });
    static INLINE: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(
            r#"(!?)\[([^\]\r\n]*)\]\((?:<([^>\r\n]+)>|([^\s)]+))(?:\s+(?:"(.*?)"|'(.*?)'))?\)"#,
        )
        .unwrap()
    });
    static DEFINITION: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?m)^ {0,3}\[([^\]\r\n]+)\]:[ \t]*(?:<([^>\r\n]+)>|(\S+))(?:[ \t]+(?:"(.*?)"|'(.*?)'))?[ \t]*$"#).unwrap()
    });
    static REFERENCE: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(!?)\[([^\]\r\n]*)\](?:\[([^\]\r\n]*)\])?").unwrap());
    static AUTOLINK: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"<(https?://[^>\s]+)>").unwrap());
    static CSS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)url\s*\(").unwrap());
    let Ok(original) = std::str::from_utf8(data) else {
        return (Vec::new(), vec![SYNTAX]);
    };
    let original = original.replace("\r\n", "\n").replace('\r', "\n");
    let mut work = 0usize;
    for line in original.split('\n') {
        let length = line.chars().count();
        let candidates = line
            .bytes()
            .filter(|b| matches!(b, b'[' | b'<' | b'\x60' | b'~'))
            .count();
        work = work.saturating_add(length.saturating_mul(candidates));
        if work > 20_000_000 {
            return (Vec::new(), vec![BUDGET]);
        }
    }
    let text = mask_code(&original);
    let tags = RESOURCE_TAG.find_iter(&text).count();
    if text.matches("![").count() + tags > MAX_REFERENCES {
        return (Vec::new(), vec![BUDGET]);
    }
    let mut definitions = BTreeMap::new();
    let mut usages = Vec::new();
    let mut reasons = Vec::new();
    let mut unclaimed = text.as_bytes().to_owned();
    for cap in DEFINITION.captures_iter(&text) {
        let target = cap.get(2).or_else(|| cap.get(3)).unwrap().as_str();
        let title = cap.get(4).or_else(|| cap.get(5)).map(|m| m.as_str());
        if definitions
            .insert(label(&cap[1]), (target, title))
            .is_some()
        {
            reasons.push(SYNTAX);
        }
        let tag = cap.get(0).unwrap();
        unclaimed[tag.start()..tag.end()].fill(b' ');
    }
    for cap in INLINE.captures_iter(&text) {
        let tag = cap.get(0).unwrap();
        let is_escaped = escaped(&text, tag.start());
        let marked_image = !cap[1].is_empty();
        if is_escaped && !marked_image {
            continue;
        }
        let kind = if marked_image && !is_escaped {
            "image"
        } else {
            "attachment"
        };
        let angle = cap.get(3);
        let target = angle.or_else(|| cap.get(4)).unwrap().as_str();
        if angle.is_none() && target.contains('(') {
            reasons.push(SYNTAX);
        }
        if kind == "image" || download(target) || declared.contains(target) {
            let title = cap
                .get(5)
                .or_else(|| cap.get(6))
                .map(|value| value.as_str());
            usages.push(Use::new(tag.start(), kind, target, &cap[2], title));
            if usages.len() > MAX_REFERENCES {
                return (Vec::new(), vec![BUDGET]);
            }
        }
        unclaimed[tag.start()..tag.end()].fill(b' ');
    }
    let unclaimed = String::from_utf8(unclaimed).unwrap();
    for cap in AUTOLINK.captures_iter(&unclaimed) {
        if download(&cap[1]) {
            usages.push(Use::new(
                cap.get(0).unwrap().start(),
                "attachment",
                &cap[1],
                "",
                None,
            ));
            if usages.len() > MAX_REFERENCES {
                return (Vec::new(), vec![BUDGET]);
            }
        }
    }
    for cap in REFERENCE.captures_iter(&unclaimed) {
        let start = cap.get(0).unwrap().start();
        let is_escaped = escaped(&text, start);
        let marked_image = !cap[1].is_empty();
        if is_escaped && !marked_image {
            continue;
        }
        let is_image = marked_image && !is_escaped;
        let reference = cap
            .get(3)
            .filter(|m| !m.is_empty())
            .map_or(&cap[2], |m| m.as_str());
        if let Some((target, title)) = definitions.get(&label(reference)) {
            if marked_image || download(target) || declared.contains(target) {
                let kind = if is_image { "image" } else { "attachment" };
                usages.push(Use::new(start, kind, target, &cap[2], *title));
                if usages.len() > MAX_REFERENCES {
                    return (Vec::new(), vec![BUDGET]);
                }
            }
        } else if is_image {
            reasons.push(SYNTAX);
        }
    }
    let image_markers = text
        .match_indices("![")
        .filter(|(offset, _)| !escaped(&text, *offset));
    let known_images = usages.iter().filter(|usage| usage.kind == "image");
    if image_markers.count() != known_images.count() {
        reasons.push(SYNTAX);
    }
    let mut html_reasons = Vec::new();
    if html_uses(&text, &mut usages, &mut html_reasons).is_some_and(|matched| matched != tags) {
        reasons.push(SYNTAX);
    }
    if CSS.is_match(&text) {
        reasons.push(SYNTAX);
    }
    reasons.extend(html_reasons);
    if usages.len() > MAX_REFERENCES {
        return (Vec::new(), vec![BUDGET]);
    }
    usages.sort_by_key(|usage| usage.offset);
    let mut byte_offset = 0;
    let mut char_offset = 0;
    for usage in &mut usages {
        char_offset += text[byte_offset..usage.offset].chars().count();
        byte_offset = usage.offset;
        usage.offset = char_offset;
    }
    (usages, reasons)
}
