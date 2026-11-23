use std::fmt::Arguments;
use std::io::{self, Write};
use std::time::{SystemTime, UNIX_EPOCH};

pub(crate) fn emit(event: &str, detail: Arguments<'_>) {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let mut stderr = io::stderr().lock();
    let _ = writeln!(stderr, "timestamp_ms={millis} event={event} {detail}");
    let _ = stderr.flush();
}
