use std::collections::HashMap;
use std::ops::Range;
use std::sync::LazyLock;

use regex::Regex;
use unicode_casefold::UnicodeCaseFold;

pub(crate) fn escaped(text: &str, offset: usize) -> bool {
    text.as_bytes()[..offset]
        .iter()
        .rev()
        .take_while(|&&byte| byte == b'\\')
        .count()
        % 2
        != 0
}

pub(crate) fn fold_label(label: &str) -> String {
    label
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .case_fold()
        .collect()
}

/// Pair equal-length backtick runs in source order. The caller decides whether
/// a pair is recognized in its context. All ranges are original UTF-8 bytes.
pub(crate) fn backtick_spans(
    text: &str,
    mut accepts: impl FnMut(&Range<usize>, &Range<usize>) -> bool,
) -> Vec<Range<usize>> {
    static TICKS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\x60+").unwrap());
    let mut runs: Vec<_> = TICKS.find_iter(text).map(|m| (m.range(), None)).collect();
    let mut following = HashMap::new();
    for (index, (run, next)) in runs.iter_mut().enumerate().rev() {
        *next = following.insert(run.len(), index);
    }
    let mut pairs = Vec::new();
    let mut index = 0;
    while let Some((start, next)) = runs.get(index) {
        index += 1;
        if let Some(next) = *next {
            let (end, _) = &runs[next];
            if accepts(start, end) {
                pairs.push(start.start..end.end);
                index = next + 1;
            }
        }
    }
    pairs
}

pub(crate) fn inline_code(text: &str, multiline: bool) -> Vec<Range<usize>> {
    backtick_spans(text, |start, end| {
        multiline || !text[start.end..end.start].contains('\n')
    })
}

/// The supplied ranges must be sorted and mutually non-overlapping.
pub(crate) fn overlaps(area: &Range<usize>, spans: &[Range<usize>]) -> bool {
    spans
        .get(spans.partition_point(|span| span.end <= area.start))
        .is_some_and(|span| span.start < area.end)
}

pub(crate) fn html_tags() -> &'static Regex {
    static TAGS: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(
            r#"<(?P<closing>/)?(?P<tag>[A-Za-z][A-Za-z0-9:-]*)\b(?P<attrs>(?:[^>"']|"[^"]*"|'[^']*')*)>"#,
        )
        .unwrap()
    });
    &TAGS
}

pub(crate) fn attributes(text: &str) -> Option<Vec<(String, Option<&str>)>> {
    static ATTRIBUTES: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(
            r#"\s+([A-Za-z_:][A-Za-z0-9_:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?"#,
        )
        .unwrap()
    });
    let mut cursor = 0;
    let mut attrs = Vec::new();
    for entry in ATTRIBUTES.captures_iter(text) {
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
