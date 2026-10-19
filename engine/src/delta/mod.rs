//! Fabric-safe Delta profile checks (phase 3). Table I/O uses Python until deltalake is wired in CI.

pub const MAX_READER: i32 = 1;
pub const MAX_WRITER: i32 = 2;

pub fn problems_from_protocol(
    min_reader: i32,
    min_writer: i32,
    reader_features: &[String],
    writer_features: &[String],
    config: &[(String, String)],
) -> Vec<String> {
    let mut found = Vec::new();
    if min_reader > MAX_READER {
        found.push(format!("reader version {min_reader} > {MAX_READER}"));
    }
    if min_writer > MAX_WRITER {
        found.push(format!("writer version {min_writer} > {MAX_WRITER}"));
    }
    if !reader_features.is_empty() {
        found.push(format!("reader features {reader_features:?}"));
    }
    if !writer_features.is_empty() {
        found.push(format!("writer features {writer_features:?}"));
    }
    for (key, bad_values) in FORBIDDEN_CONFIG {
        for (k, v) in config {
            if k == key && bad_values.iter().any(|b| v.eq_ignore_ascii_case(b)) {
                found.push(format!("{key}={v}"));
            }
        }
    }
    found
}

const FORBIDDEN_CONFIG: &[(&str, &[&str])] = &[
    ("delta.enableDeletionVectors", &["true"]),
    ("delta.columnMapping.mode", &["name", "id"]),
    ("delta.checkpointPolicy", &["v2"]),
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_when_inside_profile() {
        assert!(problems_from_protocol(1, 2, &[], &[], &[]).is_empty());
    }

    #[test]
    fn flags_high_reader() {
        let p = problems_from_protocol(2, 2, &[], &[], &[]);
        assert!(p.iter().any(|s| s.contains("reader version")));
    }
}
