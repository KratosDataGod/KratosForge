//! An engine IT installs in a folder only administrators can write (Preview), from
//! `"engine_folder"` in the policy file. AppLocker's default rules allow programs under
//! `%PROGRAMFILES%`, and App Control for Business can allow a folder by path only when standard
//! users can't write to it, so the per-user engine under `%LOCALAPPDATA%` needs a rule per build
//! while this folder doesn't.
//!
//! The folder decides what code runs, so it is used only when:
//! - `"auto_update": false`, since the app can't install or update an engine there;
//! - the setting comes from `policy.json` itself, and that file passes `policy_trust::admin_only`;
//! - the path is a plain absolute local path (a drive letter, no `..`, not a network share);
//! - the engine's folders and what Python loads first (`engine_paths`) are owned by SYSTEM,
//!   Administrators or TrustedInstaller, and nobody else may create, change, replace or delete
//!   anything in them (`policy_trust::admin_only_engine`);
//! - no folder above it, up to the drive, lets anyone else rename or replace what is in it
//!   (`policy_trust::admin_only_ancestor`).
//!
//! When the setting is there but any of that fails, the app says why and doesn't fall back to
//! the per-user engine: IT asked for this folder, and the per-user one is what their rules block.

use std::path::{Component, Path, PathBuf, Prefix};

use serde_json::Value;

pub const KEY: &str = "engine_folder";

/// The engine folder's path when the setting is a plain absolute local path; None when the key is
/// missing, and Err when it is there but isn't usable.
pub fn configured(text: &str) -> Result<Option<PathBuf>, String> {
    let value: Value = serde_json::from_str(text.trim_start_matches('\u{feff}')).unwrap_or(Value::Null);
    let Some(raw) = value.get(KEY) else { return Ok(None) };
    let Some(raw) = raw.as_str().map(str::trim).filter(|s| !s.is_empty()) else {
        return Err(format!("{KEY} should be a folder path such as C:\\Program Files\\KratosForge\\engine"));
    };
    if raw.chars().any(|c| c.is_control()) || raw.contains('/') {
        return Err(format!("{KEY} should be a Windows folder path with backslashes, such as C:\\Program Files\\KratosForge\\engine"));
    }
    let path = PathBuf::from(raw);
    if !local_absolute(raw) {
        return Err(format!("{KEY} should be a full path on this PC starting with a drive letter, not a relative path or a network share"));
    }
    if raw.split('\\').any(|part| part == ".." || part == ".") {
        return Err(format!("{KEY} can't contain . or .. folders"));
    }
    Ok(Some(path))
}

/// `C:\...` and nothing else: no `\\server\share`, no `\\?\` or `\\.\` device paths. Checked on the
/// text, so the answer is the same on every platform the tests run on.
fn local_absolute(raw: &str) -> bool {
    let b = raw.as_bytes();
    let drive = b.len() >= 3 && b[0].is_ascii_alphabetic() && b[1] == b':' && b[2] == b'\\';
    if cfg!(windows) {
        let path = Path::new(raw);
        let disk = matches!(path.components().next(), Some(Component::Prefix(p)) if matches!(p.kind(), Prefix::Disk(_)));
        return drive && disk;
    }
    drive
}

/// Windows' permission checks, passed in so tests can stand in for them.
pub struct Checks<'a> {
    /// policy_trust::admin_only for the policy file.
    pub policy: Result<(), String>,
    /// One file or folder of the engine: nobody but admins may change it or add to it.
    pub engine: &'a dyn Fn(&Path) -> Result<(), String>,
    /// A folder above the engine folder: nobody but admins may delete, rename or replace what is
    /// in it, or take over its permissions (creating new folders there is fine, as on C:\).
    pub ancestor: &'a dyn Fn(&Path) -> Result<(), String>,
}

/// The engine's python.exe for `build` in IT's folder, or why it can't be used. Ok(None) when the
/// policy doesn't set the key.
pub fn resolve(text: &str, updates_allowed: bool, build: &str, checks: Checks) -> Result<Option<PathBuf>, String> {
    let Some(folder) = configured(text)? else { return Ok(None) };
    if updates_allowed {
        return Err(format!("{KEY} is used only with \"auto_update\": false, because the app can't update an engine in a folder only administrators can write"));
    }
    checks.policy.map_err(|why| format!("{KEY} is ignored because only administrators should be able to change the policy file, and {why}"))?;
    if build.is_empty() {
        return Err(format!("{KEY} is only used by published builds"));
    }
    let build_dir = folder.join(build);
    let python = build_dir.join("python.exe");
    if !python.is_file() {
        return Err(format!(
            "Your organization keeps the engine in {}, but this build's engine (build {}) isn't there. Ask IT to \
             copy build {}'s engine into {}.",
            folder.display(),
            build.get(..7).unwrap_or(build),
            build.get(..7).unwrap_or(build),
            build_dir.display()
        ));
    }
    let refuse = |why: String| {
        format!(
            "Your organization's engine folder {} isn't used because {why}. Only SYSTEM and Administrators may be able \
             to change it; ask IT to fix its permissions.",
            folder.display()
        )
    };
    // Every folder above: whoever may rename or replace one of them could swap the engine after
    // this check.
    for ancestor in folder.ancestors().skip(1) {
        (checks.ancestor)(ancestor).map_err(refuse)?;
    }
    for path in engine_paths(&folder, &build_dir) {
        (checks.engine)(&path).map_err(refuse)?;
    }
    Ok(Some(python))
}

/// What is checked in the engine: the folders, the interpreter and what it loads first (its DLL,
/// path file, standard library zip, DLLs folder and pyvenv.cfg), Lib, site-packages and each
/// folder directly in it. Files further down inherit their folder's permissions.
fn engine_paths(folder: &Path, build_dir: &Path) -> Vec<PathBuf> {
    let mut paths = vec![folder.to_path_buf(), build_dir.to_path_buf()];
    for name in ["python.exe", "pythonw.exe", "pyvenv.cfg", "DLLs", "Lib"] {
        paths.push(build_dir.join(name));
    }
    if let Ok(entries) = std::fs::read_dir(build_dir) {
        let mut found: Vec<PathBuf> = entries
            .flatten()
            .filter(|e| {
                let name = e.file_name().to_string_lossy().to_ascii_lowercase();
                name.starts_with("python3") && [".dll", "._pth", ".zip"].iter().any(|ext| name.ends_with(ext))
            })
            .map(|e| e.path())
            .collect();
        found.sort();
        paths.extend(found);
    }
    let site = build_dir.join("Lib").join("site-packages");
    paths.push(site.clone());
    if let Ok(entries) = std::fs::read_dir(&site) {
        let mut found: Vec<PathBuf> = entries.flatten().filter(|e| e.file_type().is_ok_and(|t| t.is_dir())).map(|e| e.path()).collect();
        found.sort();
        paths.extend(found);
    }
    paths.retain(|p| p.exists());
    paths
}

#[cfg(test)]
mod tests {
    use super::*;

    const BUILD: &str = "0123456789abcdef0123456789abcdef01234567"; // pragma: allowlist secret

    #[test]
    fn the_setting_is_a_plain_local_folder_or_an_error() {
        let c = |v: &str| configured(&format!(r#"{{"engine_folder": {v}}}"#));
        assert_eq!(configured("{}").unwrap(), None);
        assert_eq!(configured("not json").unwrap(), None);
        assert_eq!(c(r#""C:\\Program Files\\KratosForge\\engine""#).unwrap(), Some(PathBuf::from(r"C:\Program Files\KratosForge\engine")));
        for bad in [r#""""#, "3", "true", r#""engine""#, r#""\\\\server\\share\\engine""#, r#""\\\\?\\C:\\engine""#,
                    r#""C:\\Program Files\\..\\Users\\me""#, r#""C:/Program Files/engine""#, r#""C:engine""#,
                    r#""C:\\a\u0007b""#] {
            assert!(c(bad).is_err(), "{bad} should be refused");
        }
    }

    fn engine(tmp: &Path) -> PathBuf {
        let dir = tmp.join(BUILD);
        std::fs::create_dir_all(dir.join("Lib").join("site-packages")).unwrap();
        std::fs::write(dir.join("python.exe"), b"").unwrap();
        dir.join("python.exe")
    }

    fn policy(folder: &Path) -> String {
        serde_json::json!({"auto_update": false, "engine_folder": folder.to_string_lossy()}).to_string()
    }

    #[test]
    fn it_folder_is_used_only_when_everything_checks_out() {
        let tmp = std::env::temp_dir().join(format!("kf-engine-folder-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        let python = engine(&tmp);
        let text = policy(&tmp);
        let ok = |_: &Path| Ok(());
        let run = |text: &str, updates: bool, trusted: Result<(), String>, admin: &dyn Fn(&Path) -> Result<(), String>| {
            resolve(text, updates, BUILD, Checks { policy: trusted, engine: admin, ancestor: &ok })
        };
        if cfg!(windows) {
            assert_eq!(run(&text, false, Ok(()), &ok).unwrap(), Some(python.clone()));
            // Updates on: the folder isn't used.
            assert!(run(&text, true, Ok(()), &ok).unwrap_err().contains("\"auto_update\": false"));
            // A policy file a standard user could have written.
            assert!(run(&text, false, Err("it isn't owned by SYSTEM or Administrators".into()), &ok)
                .unwrap_err().contains("isn't owned by SYSTEM"));
            // Any checked path a standard user can change.
            let site = python.with_file_name("Lib").join("site-packages");
            let writable = |p: &Path| if p == site { Err("someone other than SYSTEM and Administrators can change it".into()) } else { Ok(()) };
            let err = run(&text, false, Ok(()), &writable).unwrap_err();
            assert!(err.contains("isn't used because someone other than SYSTEM"), "{err}");
            // Another build's engine only.
            assert!(resolve(&text, false, "fedcba9876543210fedcba9876543210fedcba98", Checks { policy: Ok(()), engine: &ok, ancestor: &ok })
                .unwrap_err().contains("isn't there"));
            // A folder above the engine folder that someone else could rename or replace.
            let above = tmp.parent().unwrap().to_path_buf();
            let loose = |p: &Path| if p == above { Err("someone other than SYSTEM and Administrators can change it".into()) } else { Ok(()) };
            assert!(resolve(&text, false, BUILD, Checks { policy: Ok(()), engine: &ok, ancestor: &loose }).is_err());
        }
        assert_eq!(run("{}", false, Ok(()), &ok).unwrap(), None);
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn the_engine_checks_cover_what_python_loads_first() {
        let tmp = std::env::temp_dir().join(format!("kf-engine-paths-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        let build = tmp.join(BUILD);
        for dir in ["DLLs", "Lib/site-packages/duckdb", "Lib/site-packages/polars"] {
            std::fs::create_dir_all(build.join(dir)).unwrap();
        }
        for file in ["python.exe", "python312.dll", "python312._pth", "python312.zip", "pyvenv.cfg", "Lib/site-packages/six.py"] {
            std::fs::write(build.join(file), b"").unwrap();
        }
        let paths = engine_paths(&tmp, &build);
        let _ = std::fs::remove_dir_all(&tmp);
        let rel: Vec<String> = paths.iter().map(|p| p.strip_prefix(&tmp).unwrap().to_string_lossy().replace('\\', "/")).collect();
        for want in ["", BUILD, &format!("{BUILD}/python.exe"), &format!("{BUILD}/python312.dll"), &format!("{BUILD}/python312._pth"),
                     &format!("{BUILD}/python312.zip"), &format!("{BUILD}/pyvenv.cfg"), &format!("{BUILD}/DLLs"),
                     &format!("{BUILD}/Lib/site-packages"), &format!("{BUILD}/Lib/site-packages/duckdb"),
                     &format!("{BUILD}/Lib/site-packages/polars")] {
            assert!(rel.iter().any(|r| r == want), "{want} not checked: {rel:?}");
        }
        assert!(!rel.iter().any(|r| r.ends_with("six.py")), "{rel:?}");
    }

    #[test]
    fn a_missing_build_says_where_it_should_be() {
        let text = r#"{"auto_update": false, "engine_folder": "C:\\Program Files\\KratosForge\\engine"}"#;
        let err = resolve(text, false, BUILD, Checks { policy: Ok(()), engine: &|_| Ok(()), ancestor: &|_| Ok(()) }).unwrap_err();
        assert!(err.contains("build 0123456") && err.contains(r"C:\Program Files\KratosForge\engine"), "{err}");
    }
}

/// `resolve` with Windows' real permission checks, on an engine folder made on the system drive
/// (the temp folder sits in a profile the account controls, so it could never pass). Owners and
/// permissions are set on every file and folder from SDDL, which needs the elevated account the
/// Windows CI runner has; elsewhere unelevated the test says so and stops, but on CI that fails.
#[cfg(all(test, windows))]
mod windows_tests {
    use super::{resolve, Checks};
    use crate::policy_trust::windows_tests::{me, set};
    use crate::policy_trust::{admin_only_ancestor, admin_only_engine};
    use std::path::{Path, PathBuf};

    const BUILD: &str = "kf-test-build";
    const ADMINS: &str = "O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)";

    /// An engine folder on the system drive with one build in it, removed on drop.
    struct Engine(PathBuf);

    impl Engine {
        fn new(name: &str) -> Engine {
            let drive = std::env::var("SystemDrive").unwrap_or_else(|_| "C:".into());
            let base = PathBuf::from(format!(r"{drive}\kf-engine-{name}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&base);
            let build = base.join(BUILD);
            std::fs::create_dir_all(build.join("Lib").join("site-packages").join("polars")).unwrap();
            std::fs::create_dir_all(build.join("DLLs")).unwrap();
            for file in ["python.exe", "pythonw.exe", "python312.dll", "python312._pth", "python312.zip"] {
                std::fs::write(build.join(file), "stand-in").unwrap();
            }
            Engine(base)
        }

        /// Sets `sddl` on the folder and everything in it. False when the account isn't elevated
        /// and this isn't CI.
        fn lock(&self, sddl: &str) -> bool {
            let mut all = vec![self.0.clone()];
            let mut at = 0;
            while at < all.len() {
                if all[at].is_dir() {
                    let mut inside: Vec<PathBuf> = std::fs::read_dir(&all[at]).unwrap().flatten().map(|e| e.path()).collect();
                    inside.sort();
                    all.extend(inside);
                }
                at += 1;
            }
            for path in &all {
                match set(path, sddl) {
                    Ok(()) => {}
                    Err(1307) if std::env::var_os("CI").is_none() => {
                        eprintln!("not elevated, so the owner can't be set to Administrators: run these tests elevated");
                        return false;
                    }
                    Err(code) => panic!("couldn't set {}'s permissions ({code})", path.display()),
                }
            }
            true
        }

        fn policy(&self) -> String {
            serde_json::json!({ "engine_folder": self.0.to_string_lossy() }).to_string()
        }

        fn resolve(&self) -> Result<Option<PathBuf>, String> {
            let engine = |p: &Path| admin_only_engine(p);
            let ancestor = |p: &Path| admin_only_ancestor(p);
            resolve(&self.policy(), false, BUILD, Checks { policy: Ok(()), engine: &engine, ancestor: &ancestor })
        }
    }

    impl Drop for Engine {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn an_engine_folder_only_admins_can_change_is_used() {
        let engine = Engine::new("admins");
        if !engine.lock(ADMINS) {
            return;
        }
        assert_eq!(engine.resolve(), Ok(Some(engine.0.join(BUILD).join("python.exe"))));
    }

    #[test]
    fn a_package_folder_others_can_add_to_stops_the_engine_folder_being_used() {
        let engine = Engine::new("package");
        if !engine.lock(ADMINS) {
            return;
        }
        let package = engine.0.join(BUILD).join("Lib").join("site-packages").join("polars");
        set(&package, "O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1301bf;;;BU)").unwrap();
        let why = engine.resolve().unwrap_err();
        assert!(why.starts_with("Your organization's engine folder"), "{why}");
        assert!(why.contains("polars"), "the folder at fault is named: {why}");
    }

    #[test]
    fn a_dll_python_loads_first_that_others_can_change_stops_the_engine_folder_being_used() {
        let engine = Engine::new("dll");
        if !engine.lock(ADMINS) {
            return;
        }
        set(&engine.0.join(BUILD).join("python312.dll"), "O:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FW;;;BU)").unwrap();
        let why = engine.resolve().unwrap_err();
        assert!(why.contains("python312.dll"), "{why}");
    }

    #[test]
    fn an_engine_folder_owned_by_the_account_is_not_used() {
        let engine = Engine::new("owner");
        if !engine.lock(ADMINS) {
            return;
        }
        // Only admins may change it, but the account (an administrator, not the Administrators
        // group) owns the top folder, and an owner can always change its permissions.
        set(&engine.0, &format!("O:{}D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)", me())).unwrap();
        let why = engine.resolve().unwrap_err();
        assert!(why.starts_with("Your organization's engine folder"), "{why}");
        assert!(why.contains("isn't owned by SYSTEM or Administrators"), "{why}");
    }
}
