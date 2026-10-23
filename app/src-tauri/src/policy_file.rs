// Which copy of the organization's policy file applies, by the sidecar's rule
// (app_settings._policy_file): policy.json when it holds a JSON object; when it exists but can't
// be read as one (a trailing comma after an edit, say), the last copy the sidecar could read,
// policy-last-good.json next to the user's settings, as Group Policy keeps its last applied
// settings. A missing policy.json is no policy, whatever copy is left (the sidecar deletes it).
// Only std and serde_json, so the rule is tested without a window.

use std::path::Path;

use serde_json::Value;

fn object(text: &str) -> bool {
    serde_json::from_str::<Value>(text.trim_start_matches('\u{feff}')).is_ok_and(|v| v.is_object())
}

/// The text of the policy that applies: `policy`'s, else `saved`'s by the rule above; empty for
/// no policy, or for an unreadable one with no usable copy.
pub fn text(policy: &Path, saved: &Path) -> String {
    match std::fs::read_to_string(policy) {
        Ok(text) if object(&text) => return text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return String::new(),
        _ => {}
    }
    std::fs::read_to_string(saved).ok().filter(|copy| object(copy)).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::text;

    #[test]
    fn unreadable_policy_falls_back_to_the_last_good_copy() {
        let dir = std::env::temp_dir().join(format!("policy-file-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let (policy, saved) = (dir.join("policy.json"), dir.join("policy-last-good.json"));

        // No policy file: no policy, even with a copy left over.
        std::fs::write(&saved, r#"{"channel": "stable"}"#).unwrap();
        assert_eq!(text(&policy, &saved), "");

        // A readable policy file wins over the copy.
        std::fs::write(&policy, "\u{feff}{\"channel\": \"preview\"}").unwrap();
        assert_eq!(text(&policy, &saved), "\u{feff}{\"channel\": \"preview\"}");

        // One that can't be parsed, or isn't an object, gives way to the copy.
        for broken in [r#"{"channel": "preview",}"#, "[]", "", "\u{feff}"] {
            std::fs::write(&policy, broken).unwrap();
            assert_eq!(text(&policy, &saved), r#"{"channel": "stable"}"#, "{broken:?}");
        }
        std::fs::write(&policy, [0xff, 0xfe, b'{', b'}']).unwrap(); // not UTF-8
        assert_eq!(text(&policy, &saved), r#"{"channel": "stable"}"#);

        // With no usable copy, nothing applies.
        std::fs::write(&policy, "{,").unwrap();
        std::fs::write(&saved, "not json").unwrap();
        assert_eq!(text(&policy, &saved), "");
        std::fs::remove_file(&saved).unwrap();
        assert_eq!(text(&policy, &saved), "");

        let _ = std::fs::remove_dir_all(&dir);
    }
}
