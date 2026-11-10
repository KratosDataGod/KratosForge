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
pub const SHELL_KEYS: [(&str, Kind); 4] = [
    ("auto_update", Kind::Bool),
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
/// policy file's when `file_ok`. An ignored source leaves the other in force.
pub fn trusted_hosts(file: Vec<String>, file_ok: Result<(), String>, registry: Option<Vec<String>>,
                     registry_ok: Result<(), String>) -> (Vec<String>, String) {
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
pub use win::{admin_only, read};

/// Off Windows there is no Group Policy.
#[cfg(not(windows))]
pub fn read() -> Vec<(String, Raw)> {
    Vec::new()
}

#[cfg(not(windows))]
pub fn admin_only() -> Result<(), String> {
    Err("it is only trusted on Windows".into())
}

#[cfg(windows)]
mod win {
    use super::{Kind, Raw, KEY, SHELL_KEYS};
    use windows::core::{HSTRING, PCWSTR};
    use windows::Win32::Foundation::ERROR_SUCCESS;
    use windows::Win32::System::Registry::{
        RegCloseKey, RegGetValueW, RegOpenKeyExW, HKEY, HKEY_LOCAL_MACHINE, KEY_CREATE_SUB_KEY, KEY_READ,
        KEY_SET_VALUE, KEY_WOW64_64KEY, REG_SAM_FLAGS, RRF_RT_REG_DWORD, RRF_RT_REG_SZ,
    };

    const MAX_ITEMS: u32 = 1000;
    const DELETE: u32 = 0x1_0000;
    const WRITE_DAC: u32 = 0x4_0000;
    const WRITE_OWNER: u32 = 0x8_0000;

    /// The key opened with `access` in the 64-bit view, which Group Policy writes; None when it can't be.
    fn open(path: &str, access: REG_SAM_FLAGS) -> Option<HKEY> {
        let mut key = HKEY::default();
        let status = unsafe { RegOpenKeyExW(HKEY_LOCAL_MACHINE, &HSTRING::from(path), None, access | KEY_WOW64_64KEY, &mut key) };
        (status == ERROR_SUCCESS).then_some(key)
    }

    fn dword(key: HKEY, name: &str) -> Option<u32> {
        let mut data = 0u32;
        let mut size = std::mem::size_of::<u32>() as u32;
        let status = unsafe {
            RegGetValueW(key, PCWSTR::null(), &HSTRING::from(name), RRF_RT_REG_DWORD, None,
                         Some(&mut data as *mut u32 as *mut _), Some(&mut size))
        };
        (status == ERROR_SUCCESS).then_some(data)
    }

    fn string(key: HKEY, name: &str) -> Option<String> {
        let name = HSTRING::from(name);
        let mut size = 0u32;
        let status = unsafe { RegGetValueW(key, PCWSTR::null(), &name, RRF_RT_REG_SZ, None, None, Some(&mut size)) };
        if status != ERROR_SUCCESS || size > 64 * 1024 {
            return None;
        }
        let mut buf = vec![0u16; (size as usize).div_ceil(2) + 1];
        let mut size = (buf.len() * 2) as u32;
        let status = unsafe {
            RegGetValueW(key, PCWSTR::null(), &name, RRF_RT_REG_SZ, None, Some(buf.as_mut_ptr() as *mut _), Some(&mut size))
        };
        if status != ERROR_SUCCESS {
            return None;
        }
        let end = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
        String::from_utf16(&buf[..end]).ok()
    }

    /// Group Policy's values for the shell's settings; empty with no key.
    pub fn read() -> Vec<(String, Raw)> {
        let Some(key) = open(KEY, KEY_READ) else { return Vec::new() };
        let mut found = Vec::new();
        for (name, kind) in SHELL_KEYS {
            let raw = match kind {
                Kind::Bool => dword(key, name).map(Raw::Dword),
                Kind::Text => string(key, name).map(Raw::Text),
                Kind::List => open(&format!(r"{KEY}\{name}"), KEY_READ).map(|sub| {
                    let items = (1..=MAX_ITEMS).map_while(|n| string(sub, &n.to_string())).collect();
                    unsafe {
                        let _ = RegCloseKey(sub);
                    }
                    Raw::List(items)
                }),
            };
            if let Some(raw) = raw {
                found.push((name.to_owned(), raw));
            }
        }
        unsafe {
            let _ = RegCloseKey(key);
        }
        found
    }

    /// None when the account running the app couldn't change Group Policy's key or replace it, as
    /// only administrators and SYSTEM should; otherwise why not. Each right is only asked for: nothing
    /// is written. Run as an administrator, the key always reads as changeable, so the shell then
    /// doesn't take engine_download_url from it.
    pub fn admin_only() -> Result<(), String> {
        let parent = KEY.rsplit_once('\\').map(|(p, _)| p).unwrap_or(KEY);
        let rights = [KEY_SET_VALUE.0, KEY_CREATE_SUB_KEY.0, DELETE, WRITE_DAC, WRITE_OWNER];
        let parent_rights = [KEY_CREATE_SUB_KEY.0, WRITE_DAC, WRITE_OWNER];
        let changeable = |path: &str, right: u32| {
            open(path, REG_SAM_FLAGS(right)).map(|key| unsafe {
                let _ = RegCloseKey(key);
            })
        };
        if rights.iter().any(|&r| changeable(KEY, r).is_some()) || parent_rights.iter().any(|&r| changeable(parent, r).is_some()) {
            return Err(format!("HKLM\\{KEY} can be changed by the account running KratosForge"));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn found(items: &[(&str, Raw)]) -> Vec<(String, Raw)> {
        items.iter().map(|(k, v)| (k.to_string(), v.clone())).collect()
    }

    #[test]
    fn group_policy_wins_over_the_policy_file_for_the_settings_it_sets() {
        let file = "\u{feff}{\"auto_update\": true, \"channel\": \"stable\", \"engine_download_url\": \"https://a.example.com/e\"}";
        let gp = found(&[("auto_update", Raw::Dword(0)),
                         ("fabric_view_sign_in_hosts", Raw::List(vec!["adfs.contoso.com".into()]))]);
        let out: Value = serde_json::from_str(&overlay(file, &gp)).unwrap();
        assert_eq!(out["auto_update"], Value::Bool(false));
        assert_eq!(out["channel"], "stable"); // the file's other settings stay
        assert_eq!(out["engine_download_url"], "https://a.example.com/e");
        assert_eq!(out["fabric_view_sign_in_hosts"][0], "adfs.contoso.com");
        assert!(sets(&gp, "auto_update") && !sets(&gp, "engine_download_url"));
    }

    #[test]
    fn a_value_of_the_wrong_kind_leaves_the_file_as_it_is() {
        let file = r#"{"auto_update": false}"#;
        let gp = found(&[("auto_update", Raw::Dword(2)), ("register_link_scheme", Raw::Text("0".into())),
                         ("engine_download_url", Raw::Dword(1)), ("channel", Raw::Text("preview".into()))]);
        assert_eq!(overlay(file, &gp), file);
        assert!(!sets(&gp, "auto_update") && !sets(&gp, "engine_download_url"));
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
        assert_eq!(trusted_hosts(h("f.example.com"), Ok(()), Some(h("g.example.com")), Ok(())), (h("g.example.com"), String::new()));
        let (hosts, why) = trusted_hosts(h("f.example.com"), Ok(()), Some(h("g.example.com")), no());
        assert_eq!(hosts, h("f.example.com"));
        assert!(why.starts_with("Group Policy's are ignored"));
        let (hosts, why) = trusted_hosts(h("f.example.com"), no(), None, Ok(()));
        assert!(hosts.is_empty() && why.starts_with("the policy file's are ignored"));
        let (hosts, why) = trusted_hosts(h("f.example.com"), no(), Some(h("g.example.com")), no());
        assert!(hosts.is_empty() && why.contains("Group Policy's") && why.contains("policy file's"));
        assert_eq!(trusted_hosts(Vec::new(), no(), None, no()), (Vec::new(), String::new()));
    }

    #[test]
    fn an_empty_list_subkey_leaves_the_files_list_in_force() {
        let file = r#"{"fabric_view_sign_in_hosts": ["adfs.contoso.com"]}"#;
        let gp = found(&[("fabric_view_sign_in_hosts", Raw::List(Vec::new()))]);
        assert_eq!(overlay(file, &gp), file);
        assert!(!sets(&gp, "fabric_view_sign_in_hosts"));
    }
}
