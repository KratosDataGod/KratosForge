// Updating the engine without downloading all of it. Most builds change only the engine's own code
// (the fabric_desktop package, a few MB), not Python or its packages (about 150 MB). CI names the
// Python-and-packages part with a key, `runtime`, in each engine's engine.json and in version.json,
// and publishes the engine's own files on their own as fabric-desktop-engine-update.zip. When an
// installed engine has the release's runtime, the app copies (or moves) that engine, swaps in the
// update zip's files and checks the result; otherwise, or if anything fails, it downloads the
// whole engine as before. Only std and serde_json, so the rules are tested without a window.

use std::path::{Path, PathBuf};

use serde_json::Value;

/// The release file holding only the engine's own files (sidecar_owned), with the build's engine.json.
pub const UPDATE_ZIP: &str = "fabric-desktop-engine-update.zip";

fn is_sha256(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit())
}

/// Whether a path inside an engine folder is the engine's own (from sidecar/, replaced by every
/// build) rather than Python's or a package's: engine.json, and in Lib\site-packages the
/// fabric_desktop package, its dist-info and its fabric-desktop launcher in bin. CI's update zip
/// holds exactly these (.github/workflows/ci.yml, "Zip the engine").
pub fn sidecar_owned(rel: &Path) -> bool {
    let parts: Vec<String> =
        rel.components().map(|c| c.as_os_str().to_string_lossy().to_ascii_lowercase()).collect();
    match parts.as_slice() {
        [file] => file == "engine.json",
        [lib, packages, first, rest @ ..] if lib == "lib" && packages == "site-packages" => {
            first == "fabric_desktop"
                || (first.starts_with("fabric_desktop-") && first.ends_with(".dist-info"))
                || (first == "bin" && rest.first().is_some_and(|f| f.starts_with("fabric-desktop")))
        }
        _ => false,
    }
}

/// The runtime key and update zip checksum a release's version.json gives, when it offers an
/// update zip at all. Releases published before this, and promotes of them, give neither.
pub fn update_of(release: &Value) -> Option<(String, String)> {
    let runtime = release["engine_runtime"].as_str().filter(|s| is_sha256(s))?;
    let sha = release["engine_update_sha256"].as_str().filter(|s| is_sha256(s))?;
    Some((runtime.to_ascii_lowercase(), sha.to_ascii_lowercase()))
}

fn engine_info(dir: &Path) -> Option<Value> {
    let text = std::fs::read_to_string(dir.join("engine.json")).ok()?;
    serde_json::from_str(text.trim_start_matches('\u{feff}')).ok()
}

/// An installed engine with this runtime (the same Python and packages), to build the new one from.
/// One that isn't `keep` comes first: it is about to be removed anyway, so it can be moved rather
/// than copied.
pub fn find_runtime(root: &Path, runtime: &str, keep: &str) -> Option<PathBuf> {
    let mut found: Vec<PathBuf> = std::fs::read_dir(root)
        .ok()?
        .flatten()
        .filter(|e| !e.file_name().to_string_lossy().starts_with('.') && e.path().join("python.exe").is_file())
        .map(|e| e.path())
        .filter(|dir| {
            engine_info(dir).is_some_and(|info| info["runtime"].as_str().is_some_and(|r| r.eq_ignore_ascii_case(runtime)))
        })
        .collect();
    found.sort_by_key(|dir| dir.file_name().is_some_and(|n| n.to_string_lossy().eq_ignore_ascii_case(keep)));
    found.into_iter().next()
}

/// Checks the downloaded update zip against the checksum its version.json gives.
pub fn check_update_zip(actual_sha256: &str, expected_sha256: &str) -> Result<(), String> {
    if actual_sha256.eq_ignore_ascii_case(expected_sha256) {
        return Ok(());
    }
    Err("The engine update doesn't match the checksum its release gives.".into())
}

/// Copies an engine folder, leaving out the engine's own files (the update zip brings them).
fn copy_runtime(src: &Path, dst: &Path, rel: &Path) -> Result<(), String> {
    std::fs::create_dir_all(dst.join(rel)).map_err(|e| format!("Could not copy the engine: {e}"))?;
    for entry in std::fs::read_dir(src.join(rel)).map_err(|e| format!("Could not copy the engine: {e}"))? {
        let entry = entry.map_err(|e| format!("Could not copy the engine: {e}"))?;
        let rel = rel.join(entry.file_name());
        if sidecar_owned(&rel) {
            continue;
        }
        let kind = entry.file_type().map_err(|e| format!("Could not copy the engine: {e}"))?;
        if kind.is_dir() {
            copy_runtime(src, dst, &rel)?;
        } else if kind.is_file() {
            std::fs::copy(src.join(&rel), dst.join(&rel)).map_err(|e| format!("Could not copy the engine: {e}"))?;
        }
    }
    Ok(())
}

/// Removes the engine's own files from an engine folder, leaving Python and its packages.
fn strip_sidecar(dir: &Path) -> Result<(), String> {
    for parent in [PathBuf::new(), PathBuf::from("Lib").join("site-packages"), PathBuf::from("Lib").join("site-packages").join("bin")] {
        let Ok(entries) = std::fs::read_dir(dir.join(&parent)) else { continue };
        for entry in entries.flatten() {
            let rel = parent.join(entry.file_name());
            if !sidecar_owned(&rel) {
                continue;
            }
            let path = dir.join(&rel);
            let removed = if entry.file_type().is_ok_and(|t| t.is_dir()) {
                std::fs::remove_dir_all(&path)
            } else {
                std::fs::remove_file(&path)
            };
            removed.map_err(|e| format!("Could not remove the old engine code: {e}"))?;
        }
    }
    Ok(())
}

/// Puts Python and the packages of the engine `src` into `staging`, without the engine's own
/// files. With `may_move` the folder is moved, which is instant; while another program holds its
/// files open Windows refuses, and it is copied instead.
pub fn stage_runtime(src: &Path, staging: &Path, may_move: bool) -> Result<(), String> {
    let _ = std::fs::remove_dir_all(staging);
    if may_move && std::fs::rename(src, staging).is_ok() {
        return strip_sidecar(staging);
    }
    copy_runtime(src, staging, Path::new("")).inspect_err(|_| {
        let _ = std::fs::remove_dir_all(staging);
    })
}

/// Checks an engine built from an update zip before it is put in place: it must be the release's
/// build, with the release's runtime, and have Python and the engine package.
pub fn check_patched(dir: &Path, commit: &str, runtime: &str) -> Result<(), String> {
    let info = engine_info(dir).ok_or("The engine update has no engine.json.")?;
    if !info["commit"].as_str().is_some_and(|c| c.eq_ignore_ascii_case(commit)) {
        return Err("The engine update is a different build from the one its release names.".into());
    }
    if !info["runtime"].as_str().is_some_and(|r| r.eq_ignore_ascii_case(runtime)) {
        return Err("The engine update was built for different Python packages.".into());
    }
    if !dir.join("python.exe").is_file() || !dir.join("Lib").join("site-packages").join("fabric_desktop").join("__init__.py").is_file() {
        return Err("The engine update left the engine incomplete.".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const RUNTIME: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const COMMIT: &str = "1111111111111111111111111111111111111111";

    fn temp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("engine-patch-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn write(path: &Path, text: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }

    /// An engine folder as CI builds it.
    fn engine(dir: &Path, commit: &str, runtime: &str) {
        write(&dir.join("python.exe"), "python");
        write(&dir.join("python312._pth"), "pth");
        write(&dir.join("engine.json"), &json!({ "commit": commit, "runtime": runtime }).to_string());
        let sp = dir.join("Lib").join("site-packages");
        write(&sp.join("fabric_desktop").join("__init__.py"), commit);
        write(&sp.join("fabric_desktop").join("old_module.py"), "gone in the next build");
        write(&sp.join("fabric_desktop-0.20.0.dist-info").join("RECORD"), "record");
        write(&sp.join("bin").join("fabric-desktop.exe"), "launcher");
        write(&sp.join("bin").join("ruff.exe"), "ruff");
        write(&sp.join("pyarrow").join("lib.pyd"), "pyarrow");
        write(&sp.join("fabric_desktop_extra").join("x.py"), "not the engine's");
    }

    #[test]
    fn sidecar_owned_is_only_the_engines_own_files() {
        let owned = |p: &str| sidecar_owned(Path::new(p));
        assert!(owned("engine.json"));
        assert!(owned("Lib/site-packages/fabric_desktop"));
        assert!(owned("lib/Site-Packages/fabric_desktop/server.py"));
        assert!(owned("Lib/site-packages/fabric_desktop-0.20.0.dist-info/RECORD"));
        assert!(owned("Lib/site-packages/bin/fabric-desktop.exe"));
        assert!(!owned("Lib/site-packages/bin/ruff.exe"));
        assert!(!owned("Lib/site-packages/bin"));
        assert!(!owned("Lib/site-packages/pyarrow/lib.pyd"));
        assert!(!owned("Lib/site-packages/fabric_desktop_extra"));
        assert!(!owned("Lib/site-packages/fabric_desktop-0.20.0.data"));
        assert!(!owned("Lib/site-packages"));
        assert!(!owned("python.exe"));
        assert!(!owned("sub/engine.json"));
    }

    #[test]
    fn update_of_needs_both_runtime_and_checksum() {
        let sha = "b".repeat(64);
        assert_eq!(
            update_of(&json!({ "engine_runtime": RUNTIME, "engine_update_sha256": sha })),
            Some((RUNTIME.to_string(), sha.clone()))
        );
        assert_eq!(update_of(&json!({ "engine_runtime": RUNTIME })), None);
        assert_eq!(update_of(&json!({ "engine_update_sha256": sha })), None);
        assert_eq!(update_of(&json!({ "engine_runtime": "short", "engine_update_sha256": sha })), None);
        assert_eq!(update_of(&json!({ "engine_runtime": RUNTIME, "engine_update_sha256": "" })), None);
        assert_eq!(update_of(&Value::Null), None);
        assert!(check_update_zip(&sha.to_uppercase(), &sha).is_ok());
        assert!(check_update_zip(&"c".repeat(64), &sha).is_err());
    }

    #[test]
    fn find_runtime_matches_the_key_and_prefers_a_folder_it_may_move() {
        let root = temp("find");
        engine(&root.join("keep"), COMMIT, RUNTIME);
        engine(&root.join("other"), COMMIT, RUNTIME);
        engine(&root.join("different"), COMMIT, &"d".repeat(64));
        engine(&root.join(".new"), COMMIT, RUNTIME);
        assert_eq!(find_runtime(&root, RUNTIME, "keep"), Some(root.join("other")));
        std::fs::remove_dir_all(root.join("other")).unwrap();
        assert_eq!(find_runtime(&root, &RUNTIME.to_uppercase(), "keep"), Some(root.join("keep")));
        assert_eq!(find_runtime(&root, &"e".repeat(64), "keep"), None);
        // Engines installed before runtimes existed never match.
        write(&root.join("keep").join("engine.json"), &json!({ "commit": COMMIT }).to_string());
        assert_eq!(find_runtime(&root, RUNTIME, "keep"), None);
        assert_eq!(find_runtime(&root.join("missing"), RUNTIME, "keep"), None);
        let _ = std::fs::remove_dir_all(&root);
    }

    fn check_staged(staging: &Path) {
        let sp = staging.join("Lib").join("site-packages");
        assert!(staging.join("python.exe").is_file());
        assert!(staging.join("python312._pth").is_file());
        assert!(sp.join("pyarrow").join("lib.pyd").is_file());
        assert!(sp.join("bin").join("ruff.exe").is_file());
        assert!(sp.join("fabric_desktop_extra").join("x.py").is_file());
        assert!(!staging.join("engine.json").exists());
        assert!(!sp.join("fabric_desktop").exists());
        assert!(!sp.join("fabric_desktop-0.20.0.dist-info").exists());
        assert!(!sp.join("bin").join("fabric-desktop.exe").exists());
    }

    #[test]
    fn stage_runtime_copies_without_the_engines_own_files() {
        let root = temp("copy");
        let src = root.join("old");
        engine(&src, COMMIT, RUNTIME);
        let staging = root.join(".new");
        write(&staging.join("leftover"), "from an earlier attempt");
        stage_runtime(&src, &staging, false).unwrap();
        check_staged(&staging);
        assert!(!staging.join("leftover").exists());
        // The source is left whole.
        assert!(src.join("engine.json").is_file());
        assert!(src.join("Lib").join("site-packages").join("fabric_desktop").join("__init__.py").is_file());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn stage_runtime_moves_when_allowed() {
        let root = temp("move");
        let src = root.join("old");
        engine(&src, COMMIT, RUNTIME);
        let staging = root.join(".new");
        stage_runtime(&src, &staging, true).unwrap();
        check_staged(&staging);
        assert!(!src.exists());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn check_patched_wants_the_release_build_and_runtime() {
        let root = temp("check");
        let dir = root.join(".new");
        engine(&dir, COMMIT, RUNTIME);
        assert!(check_patched(&dir, COMMIT, RUNTIME).is_ok());
        assert!(check_patched(&dir, &"2".repeat(40), RUNTIME).is_err());
        assert!(check_patched(&dir, COMMIT, &"f".repeat(64)).is_err());
        std::fs::remove_file(dir.join("Lib").join("site-packages").join("fabric_desktop").join("__init__.py")).unwrap();
        assert!(check_patched(&dir, COMMIT, RUNTIME).is_err());
        std::fs::remove_file(dir.join("engine.json")).unwrap();
        assert!(check_patched(&dir, COMMIT, RUNTIME).is_err());
        let _ = std::fs::remove_dir_all(&root);
    }
}
