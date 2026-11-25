use crate::input::{Document, Reader};
use percent_encoding::percent_decode_str;
use regex::Regex;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;
use unicode_normalization::UnicodeNormalization;

mod syntax;
use syntax::Use;
pub(crate) use syntax::uses;

const MAX_REFERENCES: usize = 10_000;
const BUDGET: &str = "RESOURCE_BUDGET_EXCEEDED";
const SYNTAX: &str = "RESOURCE_SYNTAX_UNVERIFIED";

pub(crate) fn entries(doc: &Document) -> impl Iterator<Item = &Value> {
    doc.raw["resources"].as_array().into_iter().flatten()
}

fn catalog(doc: Option<&Document>) -> (BTreeMap<&str, &Value>, Vec<&'static str>) {
    let mut result = BTreeMap::new();
    let mut reasons = Vec::new();
    if let Some(doc) = doc {
        if doc.raw["resources"]
            .as_array()
            .is_some_and(|entries| entries.len() > MAX_REFERENCES)
        {
            return (result, vec![BUDGET]);
        }
        for entry in entries(doc) {
            match entry["resource_id"].as_str() {
                Some(id)
                    if !id.is_empty()
                        && !result.contains_key(id)
                        && matches!(
                            entry["kind"].as_str(),
                            Some("image" | "attachment" | "media")
                        )
                        && entry["url"].as_str().is_some_and(|url| !url.is_empty()) =>
                {
                    result.insert(id, entry);
                }
                _ => reasons.push("RESOURCE_DECLARATION_INVALID"),
            }
        }
    }
    (result, reasons)
}

pub(crate) struct UrlParts<'a> {
    pub(crate) scheme: &'a str,
    pub(crate) host: &'a str,
    pub(crate) path: &'a str,
    query: &'a str,
}

// Keep the literal authority and path. In particular, an S3 object key
// comparison must preserve dot segments, percent encoding and port syntax.
pub(crate) fn split_url(url: &str) -> Option<UrlParts<'_>> {
    static SCHEME: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"^[A-Za-z][A-Za-z0-9+.-]*:").unwrap());
    let url = url.trim_start_matches(|c| c <= ' ');
    let (scheme, tail) = SCHEME
        .find(url)
        .map_or(("", url), |m| (&url[..m.end() - 1], &url[m.end()..]));
    let (host, tail) = if let Some(authority) = tail.strip_prefix("//") {
        let delimiter = authority.find(['/', '?', '#']).unwrap_or(authority.len());
        let host = &authority[..delimiter];
        if !host.is_ascii()
            && host
                .chars()
                .filter(|ch| !matches!(ch, '@' | ':' | '#' | '?'))
                .nfkc()
                .any(|ch| matches!(ch, '/' | '?' | '#' | '@' | ':'))
        {
            return None;
        }
        let brackets = (host.contains('['), host.contains(']'));
        if brackets != (false, false) {
            let (_, literal) = host.rsplit_once('[')?;
            let (literal, _) = literal.split_once(']')?;
            let valid_ipv6 = literal.parse::<std::net::Ipv6Addr>().is_ok()
                || literal.split_once('%').is_some_and(|(ip, zone)| {
                    !zone.is_empty() && ip.parse::<std::net::Ipv6Addr>().is_ok()
                });
            let valid_future = literal.strip_prefix('v').is_some_and(|value| {
                value.split_once('.').is_some_and(|(prefix, suffix)| {
                    !prefix.is_empty()
                        && prefix.bytes().all(|ch| ch.is_ascii_hexdigit())
                        && !suffix.is_empty()
                })
            });
            if !valid_ipv6 && !valid_future {
                return None;
            }
        }
        (host, &authority[delimiter..])
    } else {
        ("", tail)
    };
    let tail = tail.split_once('#').map_or(tail, |(before, _)| before);
    let (path, query) = tail.split_once('?').unwrap_or((tail, ""));
    Some(UrlParts {
        scheme,
        host,
        path,
        query,
    })
}

pub(crate) fn decoded(value: &str) -> String {
    percent_decode_str(value).decode_utf8_lossy().into_owned()
}

fn download(url: &str) -> bool {
    const SUFFIXES: &[&str] = &[
        ".pdf", ".csv", ".tsv", ".xls", ".xlsx", ".doc", ".docx", ".ppt", ".pptx", ".zip", ".gz",
        ".tar", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".mp3", ".mp4", ".wav", ".webm",
    ];
    let cleaned = url.replace(['\r', '\n', '\t'], "");
    split_url(&cleaned).is_none_or(|url| {
        let path = decoded(url.path).to_lowercase();
        SUFFIXES.iter().any(|suffix| path.ends_with(suffix))
    })
}

fn immutable_version(entry: &Value) -> Option<(String, String, String)> {
    static S3: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"^([a-z0-9][a-z0-9.-]*)\.s3(?:\.[a-z0-9-]+)?\.amazonaws\.com$").unwrap()
    });
    let proof = entry.get("immutable")?;
    if proof["contract"] != "s3-version-id-v1" || proof["captured"].as_bool() != Some(true) {
        return None;
    }
    let url = entry["url"].as_str()?.replace(['\r', '\n', '\t'], "");
    let parts = split_url(&url)?;
    if !parts.scheme.eq_ignore_ascii_case("https") {
        return None;
    }
    let bucket = S3.captures(parts.host)?.get(1)?.as_str();
    let key = decoded(parts.path.strip_prefix('/')?);
    if key.is_empty() {
        return None;
    }
    let mut query = parts.query.split('&').filter(|s| !s.is_empty());
    let (name, version) = query.next()?.split_once('=')?;
    let version = decoded(&version.replace('+', " "));
    if decoded(&name.replace('+', " ")) != "versionId"
        || query.next().is_some()
        || version.is_empty()
        || version.eq_ignore_ascii_case("null")
        || proof["bucket"] != bucket
        || proof["key"] != key
        || proof["version_id"] != version
    {
        return None;
    }
    Some((bucket.to_owned(), key, version))
}

fn key(entry: &Value) -> (&str, &str) {
    (
        entry["url"].as_str().unwrap(),
        entry["kind"].as_str().unwrap(),
    )
}

fn positions<'a>(
    uses: &'a [Use],
    entries: &BTreeMap<&str, &Value>,
    reasons: &mut Vec<&'static str>,
) -> BTreeMap<(&'a str, &'a str), Vec<usize>> {
    let mut lookup = BTreeMap::new();
    for entry in entries.values() {
        *lookup.entry(key(entry)).or_insert(0) += 1;
    }
    let mut positions: BTreeMap<_, Vec<_>> = BTreeMap::new();
    for (index, usage) in uses.iter().enumerate() {
        let key = (usage.url.as_str(), usage.kind);
        if lookup.get(&key) == Some(&1) {
            positions.entry(key).or_default().push(index);
        } else {
            reasons.push("RESOURCE_REFERENCE_UNMAPPED");
        }
    }
    positions
}

fn evidence(
    doc: &Document,
    entry: &Value,
    reader: &mut Reader,
) -> Result<Option<Vec<u8>>, &'static str> {
    entry["path"]
        .as_str()
        .filter(|path| !path.is_empty())
        .map(|path| reader.read(&doc.resolve(path)))
        .transpose()
}

fn compare_entry(
    old: Option<&Document>,
    new: &Document,
    previous: Option<&&Value>,
    current: &Value,
    reader: &mut Reader,
) -> Result<(&'static str, &'static str), &'static str> {
    let before = old
        .zip(previous)
        .map(|(doc, entry)| evidence(doc, entry, reader))
        .transpose()?
        .flatten();
    let after = evidence(new, current, reader)?;
    match (previous, before, after) {
        (None, _, Some(_)) => Ok(("NEW", "local-bytes")),
        (None, _, None) if immutable_version(current).is_some() => Ok(("NEW", "s3-version-id-v1")),
        (_, Some(left), Some(right)) => {
            let status = if left == right {
                "UNCHANGED"
            } else {
                "CHANGED"
            };
            Ok((status, "local-bytes"))
        }
        (Some(previous), None, None) => match immutable_version(previous) {
            Some(proof) if immutable_version(current).as_ref() == Some(&proof) => {
                Ok(("UNCHANGED", "s3-version-id-v1"))
            }
            _ => Err("RESOURCE_BYTES_UNAVAILABLE"),
        },
        _ => Err("RESOURCE_BYTES_UNAVAILABLE"),
    }
}

pub(crate) fn compare(
    old: Option<&Document>,
    new: &Document,
    old_body: Option<&[u8]>,
    new_body: Option<&[u8]>,
    reader: &mut Reader,
) -> Value {
    let (old_entries, mut reasons) = catalog(old);
    let (new_entries, new_invalid) = catalog(Some(new));
    let old_urls: BTreeSet<_> = old_entries.values().map(|entry| key(entry).0).collect();
    let new_urls: BTreeSet<_> = new_entries.values().map(|entry| key(entry).0).collect();
    let (before, old_reasons) = uses(old_body.unwrap_or_default(), &old_urls);
    let (after, new_reasons) = uses(new_body.unwrap_or_default(), &new_urls);
    reasons.extend(new_invalid);
    reasons.extend(old_reasons);
    reasons.extend(new_reasons);
    let old_positions = positions(&before, &old_entries, &mut reasons);
    let new_positions = positions(&after, &new_entries, &mut reasons);
    let mut items = Vec::new();
    let mut changed = false;
    let mut processing = Vec::new();
    let ids: BTreeSet<_> = old_entries
        .keys()
        .chain(new_entries.keys())
        .copied()
        .collect();
    for id in ids {
        let previous = old_entries.get(id);
        let current = new_entries.get(id);
        let entry = current.or(previous).unwrap();
        let before = previous.and_then(|entry| old_positions.get(&key(entry)));
        let after = current.and_then(|entry| new_positions.get(&key(entry)));
        let mut item = json!({
            "resource_id": id,
            "kind": entry["kind"],
            "status": "UNKNOWN",
            "reason_codes": [],
            "basis": null,
            "occurrences_before": before.map(Vec::as_slice).unwrap_or_default(),
            "occurrences_after": after.map(Vec::as_slice).unwrap_or_default(),
        });
        if let Some(current) = current {
            match compare_entry(old, new, previous, current, reader) {
                Ok((status, basis)) => {
                    item["status"] = json!(status);
                    item["basis"] = json!(basis);
                    if matches!(status, "NEW" | "CHANGED") {
                        changed = true;
                        processing.push(id);
                    }
                }
                Err(reason) => {
                    item["reason_codes"] = json!([reason]);
                    reasons.push(reason);
                }
            }
        } else {
            item["status"] = json!("CHANGED");
            item["operation"] = json!("removed");
            changed = true;
        }
        items.push(item);
    }
    let status = if !reasons.is_empty() {
        "UNKNOWN"
    } else if items.is_empty() {
        "NOT_APPLICABLE"
    } else if old.is_none() {
        "NEW"
    } else if changed {
        "CHANGED"
    } else {
        "UNCHANGED"
    };
    let reasons: BTreeSet<_> = reasons.into_iter().collect();
    json!({
        "status": status,
        "reason_codes": reasons,
        "items": items,
        "processing_required": processing,
        "references_changed": before != after,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ambiguous_occurrence_indexes_are_not_expanded_across_declarations() {
        let declarations: Vec<_> = (0..MAX_REFERENCES)
            .map(|_| json!({"url":"chart.png","kind":"image"}))
            .collect();
        let ids: Vec<_> = (0..MAX_REFERENCES).map(|id| id.to_string()).collect();
        let entries = ids.iter().map(String::as_str).zip(&declarations).collect();
        let body = "![chart](chart.png)\n".repeat(MAX_REFERENCES);
        let (references, mut reasons) = uses(body.as_bytes(), &BTreeSet::from(["chart.png"]));
        assert_eq!(references.len(), MAX_REFERENCES);
        assert!(reasons.is_empty());

        let positions = positions(&references, &entries, &mut reasons);

        assert!(positions.is_empty());
        assert_eq!(reasons.len(), MAX_REFERENCES);
        assert!(
            reasons
                .iter()
                .all(|reason| *reason == "RESOURCE_REFERENCE_UNMAPPED")
        );
    }
}
