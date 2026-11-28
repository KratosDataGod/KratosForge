//! Group Policy for the shell: the values the ADMX templates in deploy/policy write under
//! HKLM\Software\Policies\KratosBI\KratosForge, laid over the policy file's for the settings the
//! shell reads (the engine reads the rest, registry_policy.py). Only HKLM is read, never HKCU, which
//! the person can change. A value there wins over the same key in policy.json, as in the engine. A
//! value of the wrong type is skipped, so the file's value applies (the engine reports it). Lists are
//! a subkey of numbered values (1, 2, 3, ...), as the templates write them, read up to the first
//! missing number, as the engine reads them; a subkey with no items sets nothing.

use serde_json::{Map, Value};

/// Under HKEY_LOCAL_MACHINE (policy_schema.REGISTRY_KEY).
pub const KEY: &str = r"Software\Policies\KratosBI\KratosForge";

/// The settings the shell reads, and how the registry holds each.
pub const SHELL_KEYS: [(&str, Kind); 5] = [
    ("auto_update", Kind::Bool),
    ("channel", Kind::Text),
    ("register_link_scheme", Kind::Bool),
    ("engine_download_url", Kind::Text),
    ("fabric_view_sign_in_hosts", Kind::List),
];

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Kind {
    Bool,
    Text,
    List,
}

/// One value as the registry holds it.
#[derive(Clone, PartialEq, Eq, Debug)]
pub enum Raw {
    Dword(u32),
    Text(String),
    List(Vec<String>),
}

/// The JSON value for `raw` when it has the kind `kind` needs; None when it doesn't.
fn typed(kind: Kind, raw: &Raw) -> Option<Value> {
    match (kind, raw) {
        (Kind::Bool, Raw::Dword(0)) => Some(Value::Bool(false)),
        (Kind::Bool, Raw::Dword(1)) => Some(Value::Bool(true)),
        (Kind::Text, Raw::Text(s)) => Some(Value::String(s.clone())),
        // A subkey left with no items (Group Policy set to Not Configured) sets nothing.
        (Kind::List, Raw::List(items)) if !items.is_empty() => {
            Some(Value::Array(items.iter().cloned().map(Value::String).collect()))
        }
        _ => None,
    }
}

/// `text`, the policy file's JSON (or "" for none), with Group Policy's values (`found`) laid over
/// it. Unchanged when Group Policy sets none of the shell's settings.
pub fn overlay(text: &str, found: &[(String, Raw)]) -> String {
    let set: Vec<(&str, Value)> = SHELL_KEYS
        .iter()
        .filter_map(|(key, kind)| {
            found.iter().find(|(name, _)| name == key).and_then(|(_, raw)| typed(*kind, raw)).map(|v| (*key, v))
        })
        .collect();
    if set.is_empty() {
        return text.to_owned();
    }
    let mut object = match serde_json::from_str::<Value>(text.trim_start_matches('\u{feff}')) {
        Ok(Value::Object(map)) => map,
        _ => Map::new(),
    };
    for (key, value) in set {
        object.insert(key.to_owned(), value);
    }
    Value::Object(object).to_string()
}

/// Whether `found` sets `key` with a value of the right kind.
pub fn sets(found: &[(String, Raw)], key: &str) -> bool {
    SHELL_KEYS.iter().any(|(k, kind)| {
        *k == key && found.iter().any(|(name, raw)| name == key && typed(*kind, raw).is_some())
    })
}

/// The Fabric view's extra sign-in hosts, and why any were ignored ("" for none). Hosts widen what the
/// view trusts, so only a source the person using the PC couldn't change may set them (as the engine's
/// app_settings.LOOSENING): Group Policy's (`registry`, when it sets them) when `registry_ok`, else the
/// policy file's when `file_ok`. An ignored source leaves the other in force. When Group Policy can't be
/// read (`unreadable`), none: IT may have set hosts there to narrow the file's, so neither is used.
pub fn trusted_hosts(unreadable: Option<String>, file: Vec<String>, file_ok: Result<(), String>,
                     registry: Option<Vec<String>>, registry_ok: Result<(), String>) -> (Vec<String>, String) {
    if let Some(why) = unreadable {
        return (Vec::new(), format!("none are used, because Group Policy couldn't be read ({why})"));
    }
    let mut ignored = Vec::new();
    if let Some(hosts) = registry {
        match registry_ok {
            Ok(()) => return (hosts, String::new()),
            Err(why) => ignored.push(format!("Group Policy's are ignored: {why}")),
        }
    }
    if !file.is_empty() {
        match file_ok {
            Ok(()) => return (file, ignored.join("; ")),
            Err(why) => ignored.push(format!("the policy file's are ignored: {why}")),
        }
    }
    (Vec::new(), ignored.join("; "))
}

#[cfg(windows)]
pub use win::{admin_only, read_all};

/// Group Policy's values for the shell's settings that could be read; empty with no key. A value that
/// can't be read is left out, so the file's applies, as before read_checked.
pub fn read() -> Vec<(String, Raw)> {
    read_all().0
}

/// Group Policy's values for the shell's settings, or why the key or one of its values is there but
/// couldn't be read. The engine's source (engine_download_url, engine_folder) uses this: when Group
/// Policy can't be read, what IT set there is unknown, so only the built-in source is used, never the
/// policy file's, which Group Policy may have been set to override.
pub fn read_checked() -> Result<Vec<(String, Raw)>, String> {
    match read_all() {
        (found, None) => Ok(found),
        (_, Some(why)) => Err(why),
    }
}

/// Off Windows there is no Group Policy.
#[cfg(not(windows))]
pub fn read_all() -> (Vec<(String, Raw)>, Option<String>) {
    (Vec::new(), None)
}

/// Why a registry call's `status` means Group Policy couldn't be read, or None when it read or the key
/// or value just isn't there (not configured), or a value has the wrong type (skipped, as the engine
/// skips it). Access denied, a damaged hive or any other failure is a reason: the key may hold values
/// IT set, so it is not taken as "no policy".
pub fn unreadable(status: u32, what: &str) -> Option<String> {
    const SUCCESS: u32 = 0;
    const FILE_NOT_FOUND: u32 = 2;
    const PATH_NOT_FOUND: u32 = 3;
    const UNSUPPORTED_TYPE: u32 = 1630;
    match status {
        SUCCESS | FILE_NOT_FOUND | PATH_NOT_FOUND | UNSUPPORTED_TYPE => None,
        _ => Some(format!("Windows error {status} reading {what}")),
    }
}

#[cfg(not(windows))]
pub fn admin_only(_subkeys: &[&str]) -> Result<(), String> {
    Err("it is only trusted on Windows".into())
}

#[cfg(windows)]
mod win {
    use super::{unreadable, Kind, Raw, KEY, SHELL_KEYS};
    use windows::core::{HSTRING, PCWSTR};
    use windows::Win32::Foundation::ERROR_SUCCESS;
    use windows::Win32::System::Registry::{
        RegCloseKey, RegGetValueW, RegOpenKeyExW, HKEY, HKEY_LOCAL_MACHINE, KEY_READ, KEY_WOW64_64KEY,
        REG_SAM_FLAGS, RRF_RT_REG_DWORD, RRF_RT_REG_SZ,
    };

    const MAX_ITEMS: u32 = 1000;

    /// The key opened with `access` in the 64-bit view, which Group Policy writes: Ok(None) when it isn't
    /// there, Err when it is but can't be opened (access denied, a damaged hive).
    fn open(path: &str, access: REG_SAM_FLAGS) -> Result<Option<HKEY>, String> {
        let mut key = HKEY::default();
        let status = unsafe { RegOpenKeyExW(HKEY_LOCAL_MACHINE, &HSTRING::from(path), None, access | KEY_WOW64_64KEY, &mut key) };
        if status == ERROR_SUCCESS {
            return Ok(Some(key));
        }
        unreadable(status.0, &format!(r"HKLM\{path}")).map_or(Ok(None), Err)
    }

    fn dword(key: HKEY, name: &str) -> Result<Option<u32>, String> {
        let mut data = 0u32;
        let mut size = std::mem::size_of::<u32>() as u32;
        let status = unsafe {
            RegGetValueW(key, PCWSTR::null(), &HSTRING::from(name), RRF_RT_REG_DWORD, None,
                         Some(&mut data as *mut u32 as *mut _), Some(&mut size))
        };
        if status == ERROR_SUCCESS {
            return Ok(Some(data));
        }
        unreadable(status.0, name).map_or(Ok(None), Err)
    }

    fn string(key: HKEY, name: &str) -> Result<Option<String>, String> {
        let wide = HSTRING::from(name);
        let mut size = 0u32;
        let status = unsafe { RegGetValueW(key, PCWSTR::null(), &wide, RRF_RT_REG_SZ, None, None, Some(&mut size)) };
        if status != ERROR_SUCCESS {
            return unreadable(status.0, name).map_or(Ok(None), Err);
        }
        if size > 64 * 1024 {
            return Ok(None); // too long to be a setting: skipped, as a value of the wrong type is
        }
        let mut buf = vec![0u16; (size as usize).div_ceil(2) + 1];
        let mut size = (buf.len() * 2) as u32;
        let status = unsafe {
            RegGetValueW(key, PCWSTR::null(), &wide, RRF_RT_REG_SZ, None, Some(buf.as_mut_ptr() as *mut _), Some(&mut size))
        };
        if status != ERROR_SUCCESS {
            return unreadable(status.0, name).map_or(Ok(None), Err);
        }
        let end = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
        Ok(String::from_utf16(&buf[..end]).ok())
    }

    fn list(name: &str) -> Result<Option<Vec<String>>, String> {
        let Some(sub) = open(&format!(r"{KEY}\{name}"), KEY_READ)? else { return Ok(None) };
        let mut items = Vec::new();
        let mut failed = None;
        for n in 1..=MAX_ITEMS {
            match string(sub, &n.to_string()) {
                Ok(Some(item)) => items.push(item),
                Ok(None) => break,
                Err(why) => {
                    failed = Some(why);
                    break;
                }
            }
        }
        unsafe {
            let _ = RegCloseKey(sub);
        }
        failed.map_or(Ok(Some(items)), Err)
    }

    /// Group Policy's values for the shell's settings that could be read (empty with no key), and why
    /// the key or a value that is there couldn't be read, if one couldn't.
    pub fn read_all() -> (Vec<(String, Raw)>, Option<String>) {
        let key = match open(KEY, KEY_READ) {
            Ok(Some(key)) => key,
            Ok(None) => return (Vec::new(), None),
            Err(why) => return (Vec::new(), Some(why)),
        };
        let mut found = Vec::new();
        let mut failed = None;
        for (name, kind) in SHELL_KEYS {
            let raw = match kind {
                Kind::Bool => dword(key, name).map(|v| v.map(Raw::Dword)),
                Kind::Text => string(key, name).map(|v| v.map(Raw::Text)),
                Kind::List => list(name).map(|v| v.map(Raw::List)),
            };
            match raw {
                Ok(Some(raw)) => found.push((name.to_owned(), raw)),
                Ok(None) => {}
                Err(why) => failed = failed.or(Some(why)),
            }
        }
        unsafe {
            let _ = RegCloseKey(key);
        }
        (found, failed)
    }

    /// None when only SYSTEM and Administrators could have written Group Policy's key, its parent and
    /// the list `subkeys` named (which must exist); otherwise why not. By owner and permissions
    /// (policy_trust::key_admin_only), as the engine's LOOSENING check, so an elevated run is judged
    /// like any other. Nothing is written.
    pub fn admin_only(subkeys: &[&str]) -> Result<(), String> {
        crate::policy_trust::key_admin_only(KEY, subkeys)
            .map_err(|why| format!("HKLM\\{KEY} isn't one only administrators could have written: {why}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_missing_key_or_value_or_a_wrong_type_counts_as_not_configured() {
        for status in [0, 2, 3, 1630] {
            assert_eq!(unreadable(status, "auto_update"), None, "{status}");
        }
        for status in [5, 1009, 1015, 234] {
            // access denied, a damaged hive, the registry failed, more data than asked for
            assert_eq!(unreadable(status, "auto_update"), Some(format!("Windows error {status} reading auto_update")));
        }
    }

    fn found(items: &[(&str, Raw)]) -> Vec<(String, Raw)> {
        items.iter().map(|(k, v)| (k.to_string(), v.clone())).collect()
    }

    #[test]
    fn group_policy_wins_over_the_policy_file_for_the_settings_it_sets() {
        let file = "\u{feff}{\"auto_update\": true, \"channel\": \"stable\", \"engine_download_url\": \"https://a.example.com/e\"}";
        let gp = found(&[("auto_update", Raw::Dword(0)), ("channel", Raw::Text("preview".into())),
                         ("fabric_view_sign_in_hosts", Raw::List(vec!["adfs.contoso.com".into()]))]);
        let out: Value = serde_json::from_str(&overlay(file, &gp)).unwrap();
        assert_eq!(out["auto_update"], Value::Bool(false));
        assert_eq!(out["channel"], "preview"); // the update channel too, as the engine shows it in Settings
        assert_eq!(out["engine_download_url"], "https://a.example.com/e"); // the file's other settings stay
        assert_eq!(out["fabric_view_sign_in_hosts"][0], "adfs.contoso.com");
        assert!(sets(&gp, "auto_update") && sets(&gp, "channel") && !sets(&gp, "engine_download_url"));
    }

    #[test]
    fn a_value_of_the_wrong_kind_leaves_the_file_as_it_is() {
        let file = r#"{"auto_update": false}"#;
        let gp = found(&[("auto_update", Raw::Dword(2)), ("register_link_scheme", Raw::Text("0".into())),
                         ("engine_download_url", Raw::Dword(1)), ("channel", Raw::Dword(1))]);
        assert_eq!(overlay(file, &gp), file);
        assert!(!sets(&gp, "auto_update") && !sets(&gp, "engine_download_url") && !sets(&gp, "channel"));
    }

    #[test]
    fn group_policy_alone_needs_no_policy_file() {
        for file in ["", "not json", "[]"] {
            let out: Value = serde_json::from_str(&overlay(file, &found(&[("register_link_scheme", Raw::Dword(0))]))).unwrap();
            assert_eq!(out, serde_json::json!({"register_link_scheme": false}), "{file:?}");
        }
        assert_eq!(overlay("", &[]), "");
    }

    #[test]
    fn sign_in_hosts_come_only_from_a_source_the_user_couldnt_change() {
        let h = |x: &str| vec![x.to_string()];
        let no = || Err::<(), String>("changeable".into());
        assert_eq!(trusted_hosts(None, h("f.example.com"), Ok(()), Some(h("g.example.com")), Ok(())), (h("g.example.com"), String::new()));
        let (hosts, why) = trusted_hosts(None, h("f.example.com"), Ok(()), Some(h("g.example.com")), no());
        assert_eq!(hosts, h("f.example.com"));
        assert!(why.starts_with("Group Policy's are ignored"));
        let (hosts, why) = trusted_hosts(None, h("f.example.com"), no(), None, Ok(()));
        assert!(hosts.is_empty() && why.starts_with("the policy file's are ignored"));
        let (hosts, why) = trusted_hosts(None, h("f.example.com"), no(), Some(h("g.example.com")), no());
        assert!(hosts.is_empty() && why.contains("Group Policy's") && why.contains("policy file's"));
        assert_eq!(trusted_hosts(None, Vec::new(), no(), None, no()), (Vec::new(), String::new()));
        // Group Policy there but unreadable (a list that failed part way): no extra hosts from either
        // source, never the file's, which Group Policy may have been set to narrow.
        let (hosts, why) = trusted_hosts(Some("Windows error 5 reading 2".into()), h("f.example.com"), Ok(()),
                                         None, Ok(()));
        assert!(hosts.is_empty());
        assert_eq!(why, "none are used, because Group Policy couldn't be read (Windows error 5 reading 2)");
    }

    #[test]
    fn an_empty_list_subkey_leaves_the_files_list_in_force() {
        let file = r#"{"fabric_view_sign_in_hosts": ["adfs.contoso.com"]}"#;
        let gp = found(&[("fabric_view_sign_in_hosts", Raw::List(Vec::new()))]);
        assert_eq!(overlay(file, &gp), file);
        assert!(!sets(&gp, "fabric_view_sign_in_hosts"));
    }
}
