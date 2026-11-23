use serde_json::{Value, json};

pub struct Options {
    pub file_bytes: u64,
    pub normalize: bool,
    pub evidence: bool,
    pub work: usize,
    pub chars: usize,
    pub char_region: usize,
}

#[derive(Clone, Copy, PartialEq)]
enum Op {
    Insert,
    Delete,
}
type Edit = (Op, usize, usize);

fn step(work: &mut usize) -> Option<()> {
    *work = work.checked_sub(1)?;
    Some(())
}

fn previous(frontier: &[isize], distance: isize, diagonal: isize) -> (bool, isize, isize) {
    if distance == 0 {
        return (true, 0, 0);
    }
    let get = |k: isize| {
        let index = k + distance - 1;
        if index < 0 || index >= 2 * distance {
            -1
        } else {
            frontier[(index / 2) as usize]
        }
    };
    let insert =
        diagonal == -distance || (diagonal != distance && get(diagonal - 1) < get(diagonal + 1));
    let k = if insert { diagonal + 1 } else { diagonal - 1 };
    (insert, k, get(k))
}

fn myers<T: PartialEq>(old: &[T], new: &[T], work: &mut usize) -> Option<Vec<Edit>> {
    let mut trace: Vec<Vec<isize>> = Vec::new();
    let (old_len, new_len) = (old.len() as isize, new.len() as isize);
    for distance in 0..=old_len + new_len {
        let frontier = trace.last().map(Vec::as_slice).unwrap_or_default();
        let mut next = Vec::new();
        for k in (-distance..=distance).step_by(2) {
            step(work)?;
            let (insert, _, x) = previous(frontier, distance, k);
            let mut x = x + isize::from(!insert);
            let mut y = x - k;
            while x < old_len && y < new_len && old[x as usize] == new[y as usize] {
                step(work)?;
                x += 1;
                y += 1;
            }
            next.push(x);
            if x >= old_len && y >= new_len {
                let mut edits = Vec::new();
                let (mut x, mut y) = (old_len, new_len);
                for (d, frontier) in trace.iter().enumerate().rev() {
                    let (insert, k, px) = previous(frontier, (d + 1) as isize, x - y);
                    let py = px - k;
                    edits.push(if insert {
                        (Op::Insert, px as usize, py as usize)
                    } else {
                        (Op::Delete, px as usize, py as usize)
                    });
                    (x, y) = (px, py);
                }
                edits.reverse();
                return Some(edits);
            }
        }
        trace.push(next);
    }
    unreachable!("Myers path must reach target")
}

fn lines(s: &str) -> Vec<&str> {
    let mut result = Vec::new();
    let mut start = 0;
    let mut chars = s.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        if matches!(
            c,
            '\n' | '\r'
                | '\x0b'
                | '\x0c'
                | '\x1c'
                | '\x1d'
                | '\x1e'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        ) {
            let mut end = i + c.len_utf8();
            if c == '\r' && chars.peek().is_some_and(|(_, ch)| *ch == '\n') {
                chars.next();
                end += 1;
            }
            result.push(&s[start..end]);
            start = end;
        }
    }
    if start < s.len() {
        result.push(&s[start..]);
    }
    result
}

fn evidence(
    old: &[&str],
    new: &[&str],
    edits: &[Edit],
    options: &Options,
    work: &mut usize,
) -> Option<(Vec<Value>, bool)> {
    let mut regions: Vec<(usize, usize, usize, usize)> = Vec::new();
    for &(op, x, y) in edits {
        let end = (
            x + usize::from(op == Op::Delete),
            y + usize::from(op == Op::Insert),
        );
        if let Some(last) = regions.last_mut().filter(|v| (v.2, v.3) == (x, y)) {
            (last.2, last.3) = end;
        } else {
            regions.push((x, y, end.0, end.1));
        }
    }
    let mut remaining = options.chars;
    let mut differences = Vec::new();
    let mut truncated = false;
    for (x, y, end_x, end_y) in regions {
        if remaining == 0 {
            truncated = true;
            break;
        }
        let removed: Vec<char> = old[x..end_x].iter().flat_map(|l| l.chars()).collect();
        let added: Vec<char> = new[y..end_y].iter().flat_map(|l| l.chars()).collect();
        let nr = remaining.min(removed.len());
        remaining -= nr;
        let na = remaining.min(added.len());
        remaining -= na;
        let clipped = nr + na < removed.len() + added.len();
        truncated |= clipped;
        let mut omitted = clipped || nr + na > options.char_region;
        let mut characters = Vec::new();
        if !omitted {
            for (op, i, j) in myers(&removed, &added, work)? {
                if remaining == 0 {
                    truncated = true;
                    omitted = true;
                    break;
                }
                let (operation, c) = if op == Op::Delete {
                    ("delete", removed[i])
                } else {
                    ("insert", added[j])
                };
                characters.push(json!({"operation":operation, "old_offset":i, "new_offset":j, "text":c.to_string()}));
                remaining -= 1;
            }
        }
        differences.push(json!({
            "old_start_line":x+1, "new_start_line":y+1,
            "removed":removed[..nr].iter().collect::<String>(), "added":added[..na].iter().collect::<String>(),
            "characters":characters, "characters_omitted":omitted
        }));
    }
    Some((differences, truncated))
}

pub fn result(status: &str, reasons: &[&str], equal: Option<bool>) -> Value {
    json!({"status":status, "reason_codes":reasons, "differences":[], "byte_equal":equal,
           "comparison_complete":status != "UNKNOWN", "evidence_truncated":false})
}

pub fn compare(old: Option<&[u8]>, new: &[u8], options: &Options) -> Value {
    let Some(old) = old else {
        return result("NEW", &["NEW_DOCUMENT"], None);
    };
    if old == new {
        return result("UNCHANGED", &["TEXT_BYTES_EQUAL"], Some(true));
    }
    let diff = "TEXT_BYTES_DIFFER";
    let normalize = |b: &[u8]| {
        let mut normalized = Vec::with_capacity(b.len());
        let mut it = b.iter().copied().peekable();
        while let Some(x) = it.next() {
            if x == b'\r' {
                if it.peek() == Some(&b'\n') {
                    it.next();
                }
                normalized.push(b'\n');
            } else {
                normalized.push(x);
            }
        }
        normalized
    };
    let (normalized_old, normalized_new);
    let (old, new) = if options.normalize {
        normalized_old = normalize(old);
        normalized_new = normalize(new);
        if normalized_old == normalized_new {
            return result("UNCHANGED", &[diff, "TEXT_NORMALIZED_EQUAL"], Some(false));
        }
        (normalized_old.as_slice(), normalized_new.as_slice())
    } else {
        (old, new)
    };
    let (Ok(old), Ok(new)) = (std::str::from_utf8(old), std::str::from_utf8(new)) else {
        return result("UNKNOWN", &[diff, "TEXT_ENCODING_UNSUPPORTED"], Some(false));
    };
    let mut changed = result("CHANGED", &[diff], Some(false));
    if options.evidence {
        let (old, new) = (lines(old), lines(new));
        let mut budget = options.work;
        let explanation = myers(&old, &new, &mut budget)
            .and_then(|edits| evidence(&old, &new, &edits, options, &mut budget));
        match explanation {
            Some((diffs, truncated)) => {
                changed["differences"] = json!(diffs);
                changed["evidence_truncated"] = json!(truncated);
            }
            None => changed["evidence_truncated"] = json!(true),
        }
    }
    changed
}
