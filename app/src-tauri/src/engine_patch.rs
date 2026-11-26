// Updating the engine without downloading all of it. Most builds change only the engine's own code
// (the fabric_desktop package, a few MB), not Python or its packages (about 150 MB). CI names the
// Python-and-packages part with a key, `runtime`, in each engine's engine.json and in version.json,
// and publishes the engine's own files on their own as fabric-desktop-engine-update.zip. When an
// installed engine has the release's runtime, the app copies that engine (hard links where it can), swaps in the
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
pub fn find_runtime(root: &Path, runtime: &str) -> Option<PathBuf> {
    std::fs::read_dir(root)
        .ok()?
        .flatten()
        .filter(|e| !e.file_name().to_string_lossy().starts_with('.') && e.path().join("python.exe").is_file())
        .map(|e| e.path())
        .find(|dir| {
            engine_info(dir).is_some_and(|info| info["runtime"].as_str().is_some_and(|r| r.eq_ignore_ascii_case(runtime)))
        })
}

/// Checks the downloaded update zip against the checksum its version.json gives.
pub fn check_update_zip(actual_sha256: &str, expected_sha256: &str) -> Result<(), String> {
    if actual_sha256.eq_ignore_ascii_case(expected_sha256) {
        return Ok(());
    }
    Err("The engine update doesn't match the checksum its release gives.".into())
}

/// Copies an engine folder, leaving out the engine's own files (the update zip brings them). Files
/// are hard-linked where the disk allows, which is instant and takes no space; Python only ever
/// replaces files (never writes into them), so the two engines never change each other's.
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
        } else if kind.is_file() && std::fs::hard_link(src.join(&rel), dst.join(&rel)).is_err() {
            std::fs::copy(src.join(&rel), dst.join(&rel)).map_err(|e| format!("Could not copy the engine: {e}"))?;
        }
    }
    Ok(())
}

/// Puts Python and the packages of the engine `src` into `staging`, without the engine's own
/// files. `src` is never changed: after an exe update it is the engine the app falls back to, so a
/// failed update (and then a failed full download, offline say) must leave it whole.
pub fn stage_runtime(src: &Path, staging: &Path) -> Result<(), String> {
    let _ = std::fs::remove_dir_all(staging);
    copy_runtime(src, staging, Path::new("")).inspect_err(|_| {
        let _ = std::fs::remove_dir_all(staging);
    })
}

/// Removes the engines of builds other than `keep`. An engine is first renamed to `.old-<name>` and
/// only then deleted: Windows refuses to rename a folder while a program runs from it, so an engine
/// still running is left whole (and goes next time). Deleting it in place would remove every file
/// Windows doesn't hold open, such as certifi's cacert.pem, and its process would then fail every
/// sign-in. Leftover `.old-` folders from an earlier pass go too; other dot folders (`.new`) and
/// anything not named for a build are never touched.
pub fn prune_engines(root: &Path, keep: &[&str]) {
    for entry in std::fs::read_dir(root).into_iter().flatten().flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        if name.starts_with(".old-") {
            let _ = std::fs::remove_dir_all(&path);
            continue;
        }
        let is_build = name.len() == 40 && name.bytes().all(|b| b.is_ascii_hexdigit());
        if !is_build || keep.iter().any(|k| k.eq_ignore_ascii_case(&name)) {
            continue;
        }
        let aside = root.join(format!(".old-{name}"));
        let _ = std::fs::remove_dir_all(&aside);
        if std::fs::rename(&path, &aside).is_ok() {
            let _ = std::fs::remove_dir_all(&aside);
        }
    }
}

/// Moves a checked engine from `staging` to `dest`, the folder named for its build, in one rename.
/// Whatever was at `dest` goes first. Defender, other antivirus and the search indexer open the
/// files tar has just unpacked, and Windows refuses to rename a folder while a file in it is open
/// ("Access is denied"): that is waited out as update_guard::rename_retrying does (about 15 s).
/// Still held after that, the message says so and what to do; the unpacked engine is kept.
pub fn put_in_place(staging: &Path, dest: &Path) -> Result<(), String> {
    put_in_place_with(staging, dest, |from, to| std::fs::rename(from, to), std::time::Duration::from_millis(250))
}

/// put_in_place with the rename and the pause between tries given, so tests can make the rename fail.
fn put_in_place_with(
    staging: &Path,
    dest: &Path,
    rename: impl FnMut(&Path, &Path) -> std::io::Result<()>,
    pause: std::time::Duration,
) -> Result<(), String> {
    let _ = std::fs::remove_dir_all(dest);
    crate::update_guard::rename_retrying_with(staging, dest, rename, pause).map_err(|e| {
        if crate::update_guard::is_lock(&e) {
            "The new engine couldn't be put in place because another program, such as a virus scanner or the \
             search indexer, still has one of its files open. Wait a minute and try again. If it keeps happening, \
             ask IT to let your antivirus allow KratosForge's engine folder."
                .to_string()
        } else {
            format!("Could not put the engine in place: {e}")
        }
    })
}

/// What an engine install leaves in `root` when the app is closed, the PC sleeps or the power goes
/// part way through: the downloaded zips (the whole engine is about 330 MB), the release's
/// version.json and the half-unpacked `.new` folder. None of it is ever run (an engine runs only
/// from a folder named for its build, put in place by one rename once it is complete), so the
/// installed engine is untouched either way; this only gives the space back. Called at startup,
/// before anything can start an install.
pub const UNFINISHED: [&str; 4] = ["download.zip", "update.zip", "version.json", ".new"];

pub fn discard_unfinished(root: &Path) {
    for name in UNFINISHED {
        let path = root.join(name);
        // symlink_metadata: a link named like these is removed itself, never followed.
        match std::fs::symlink_metadata(&path) {
            Ok(meta) if meta.is_dir() => {
                let _ = std::fs::remove_dir_all(&path);
            }
            Ok(_) => {
                let _ = std::fs::remove_file(&path);
            }
            Err(_) => {}
        }
    }
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

/// `file` as `cmd` should name it: by its path inside `folder`, with `cmd` run in `folder`, for Windows'
/// own tar.exe and curl.exe. They read their command line in the ANSI code page, so the engine folder
/// under a profile named outside it (C:\Users\日本 on a PC set to English) reached tar as C:\Users\??,
/// and the engine never unpacked. The names inside the engine folder (download.zip, .new) are ASCII,
/// and Windows takes the folder itself as UTF-16. A file outside `folder` keeps its whole path.
pub fn named_in<'a>(cmd: &mut std::process::Command, folder: &Path, file: &'a Path) -> &'a std::ffi::OsStr {
    cmd.current_dir(folder);
    file.strip_prefix(folder).map(Path::as_os_str).unwrap_or(file.as_os_str())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn tar_and_curl_name_the_engine_files_in_ascii_under_a_profile_named_outside_the_ansi_code_page() {
        let root = Path::new(r"C:\Users\Jöhn O'Brien 日本\AppData\Local\FabricDesktop\engine");
        let (zip, staging) = (root.join("download.zip"), root.join(".new"));
        let mut tar = std::process::Command::new("tar.exe");
        let (zip_name, into) = (named_in(&mut tar, root, &zip), named_in(&mut tar, root, &staging));
        tar.arg("-xf").arg(zip_name).arg("-C").arg(into);
        assert_eq!(tar.get_current_dir(), Some(root));
        let args: Vec<_> = tar.get_args().map(|a| a.to_str().unwrap().to_string()).collect();
        assert_eq!(args, ["-xf", "download.zip", "-C", ".new"]);
        assert!(args.iter().all(|a| a.is_ascii()), "{args:?}");

        let elsewhere = Path::new(r"D:\Spiegel Ünited\engine.zip");
        assert_eq!(named_in(&mut tar, root, elsewhere), elsewhere.as_os_str(), "a file outside keeps its path");
    }

    const RUNTIME: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const COMMIT: &str = "1111111111111111111111111111111111111111";

    #[test]
    fn an_install_cut_off_part_way_leaves_the_installed_engine_and_gives_back_the_space() {
        let root = temp("unfinished");
        let engine = root.join(COMMIT);
        std::fs::create_dir_all(engine.join("Lib")).unwrap();
        std::fs::write(engine.join("python.exe"), b"MZ").unwrap();
        std::fs::write(root.join("download.zip"), b"PK half").unwrap();
        std::fs::write(root.join("update.zip"), b"PK").unwrap();
        std::fs::write(root.join("version.json"), b"{").unwrap();
        std::fs::create_dir_all(root.join(".new").join("Lib")).unwrap();
        std::fs::write(root.join(".new").join("python.exe"), b"MZ half").unwrap();
        std::fs::write(root.join("engine-verified.json"), b"{}").unwrap();
        discard_unfinished(&root);
        for name in UNFINISHED {
            assert!(!root.join(name).exists(), "{name} was left");
        }
        assert!(engine.join("python.exe").is_file(), "the installed engine was touched");
        assert!(root.join("engine-verified.json").is_file(), "a file that isn't an unfinished install went");
        discard_unfinished(&root); // nothing left to remove is fine
        let _ = std::fs::remove_dir_all(&root);
    }

    /// Windows refusing a folder rename while a virus scanner or the indexer has a file in it open.
    fn held() -> std::io::Error {
        std::io::Error::new(std::io::ErrorKind::PermissionDenied, "Access is denied. (os error 5)")
    }

    fn unpacked(name: &str) -> (PathBuf, PathBuf, PathBuf) {
        let root = temp(name);
        let staging = root.join(".new");
        write(&staging.join("python.exe"), "MZ");
        (root.clone(), staging, root.join(COMMIT))
    }

    #[test]
    fn an_engine_a_scanner_holds_for_a_moment_is_put_in_place_once_it_lets_go() {
        let (root, staging, dest) = unpacked("held-moment");
        let mut tries = 0;
        let rename = |from: &Path, to: &Path| {
            tries += 1;
            if tries <= 3 { Err(held()) } else { std::fs::rename(from, to) }
        };
        assert_eq!(put_in_place_with(&staging, &dest, rename, std::time::Duration::ZERO), Ok(()));
        assert_eq!(tries, 4);
        assert!(dest.join("python.exe").is_file() && !staging.exists());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn an_engine_still_held_says_what_holds_it_and_what_to_do() {
        let (root, staging, dest) = unpacked("held-long");
        let mut tries = 0;
        let rename = |_: &Path, _: &Path| {
            tries += 1;
            Err(held())
        };
        let err = put_in_place_with(&staging, &dest, rename, std::time::Duration::ZERO).unwrap_err();
        assert_eq!(
            err,
            "The new engine couldn't be put in place because another program, such as a virus scanner or the \
             search indexer, still has one of its files open. Wait a minute and try again. If it keeps happening, \
             ask IT to let your antivirus allow KratosForge's engine folder."
        );
        assert_eq!(tries, 61, "about 15 seconds of tries, then the error");
        assert!(staging.join("python.exe").is_file(), "a move that failed deletes nothing");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn any_other_failure_to_put_the_engine_in_place_is_said_at_once() {
        let (root, staging, dest) = unpacked("missing");
        let mut tries = 0;
        let rename = |_: &Path, _: &Path| {
            tries += 1;
            Err(std::io::Error::new(std::io::ErrorKind::NotFound, "The system cannot find the path specified."))
        };
        let err = put_in_place_with(&staging, &dest, rename, std::time::Duration::ZERO).unwrap_err();
        assert_eq!(err, "Could not put the engine in place: The system cannot find the path specified.");
        assert_eq!(tries, 1);
        let _ = std::fs::remove_dir_all(&root);
    }

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
    fn find_runtime_matches_the_key() {
        let root = temp("find");
        engine(&root.join("same"), COMMIT, RUNTIME);
        engine(&root.join("different"), COMMIT, &"d".repeat(64));
        engine(&root.join(".new"), COMMIT, RUNTIME);
        assert_eq!(find_runtime(&root, RUNTIME), Some(root.join("same")));
        assert_eq!(find_runtime(&root, &RUNTIME.to_uppercase()), Some(root.join("same")));
        assert_eq!(find_runtime(&root, &"e".repeat(64)), None);
        // Engines installed before runtimes existed never match.
        write(&root.join("same").join("engine.json"), &json!({ "commit": COMMIT }).to_string());
        assert_eq!(find_runtime(&root, RUNTIME), None);
        assert_eq!(find_runtime(&root.join("missing"), RUNTIME), None);
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
        stage_runtime(&src, &staging).unwrap();
        check_staged(&staging);
        assert!(!staging.join("leftover").exists());
        // The source is left whole.
        assert!(src.join("engine.json").is_file());
        assert!(src.join("Lib").join("site-packages").join("fabric_desktop").join("__init__.py").is_file());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_failed_update_leaves_the_source_engine_whole() {
        let root = temp("fail");
        let src = root.join("old");
        engine(&src, COMMIT, RUNTIME);
        let staging = root.join(".new");
        stage_runtime(&src, &staging).unwrap();
        // Python changes an engine only by replacing files, so the copy's never reach the source.
        let pyd = staging.join("Lib").join("site-packages").join("pyarrow").join("lib.pyd");
        std::fs::remove_file(&pyd).unwrap();
        write(&pyd, "replaced in the copy");
        // The update zip didn't unpack: the new engine fails its check and staging is removed.
        assert!(check_patched(&staging, COMMIT, RUNTIME).is_err());
        std::fs::remove_dir_all(&staging).unwrap();
        assert!(check_patched(&src, COMMIT, RUNTIME).is_ok());
        let sp = src.join("Lib").join("site-packages");
        assert_eq!(std::fs::read_to_string(sp.join("pyarrow").join("lib.pyd")).unwrap(), "pyarrow");
        assert!(sp.join("fabric_desktop").join("old_module.py").is_file());
        assert!(sp.join("bin").join("fabric-desktop.exe").is_file());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn prune_engines_removes_other_builds_only() {
        let root = temp("prune");
        let keep = "2".repeat(40);
        let other = "3".repeat(40);
        engine(&root.join(COMMIT), COMMIT, RUNTIME);
        engine(&root.join(&keep), &keep, RUNTIME);
        engine(&root.join(&other), &other, RUNTIME);
        engine(&root.join(".new"), COMMIT, RUNTIME);
        engine(&root.join(format!(".old-{}", "4".repeat(40))), COMMIT, RUNTIME);
        engine(&root.join("not-a-build"), COMMIT, RUNTIME);
        write(&root.join("version.json"), "{}");
        prune_engines(&root, &[COMMIT, &keep.to_uppercase()]);
        assert!(check_patched(&root.join(COMMIT), COMMIT, RUNTIME).is_ok());
        assert!(check_patched(&root.join(&keep), &keep, RUNTIME).is_ok());
        assert!(!root.join(&other).exists());
        assert!(!root.join(format!(".old-{other}")).exists());
        assert!(!root.join(format!(".old-{}", "4".repeat(40))).exists());
        assert!(root.join(".new").join("python.exe").is_file());
        assert!(root.join("not-a-build").join("python.exe").is_file());
        assert!(root.join("version.json").is_file());
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
