// The program file is kratosforge.exe. Builds before it were fabric-desktop.exe, and an update
// replaces the file in place under whatever name it has, so a copy saved as fabric-desktop.exe
// stays that name through updates from older builds. The first build that knows the new name
// renames its own file once, after an update to it has started cleanly (check_components), and
// points the person's shortcuts (Desktop, Start menu, taskbar pins) at the new file. If any
// shortcut can't be changed, the shortcuts and the file are put back to fabric-desktop.exe, which
// keeps updating as before. Only std, so the order is tested without a window or Windows.
//
// Releases publish the exe under both names (ci.yml, promote.yml): builds before this one only
// download fabric-desktop.exe, and older build-* releases (a rollback) only have that name.

#![cfg_attr(not(windows), allow(dead_code))]

use std::path::{Path, PathBuf};

/// The program file's name.
pub const APP_FILE: &str = "kratosforge.exe";
/// Its name before KratosForge, still published for builds that download only this name.
pub const LEGACY_FILE: &str = "fabric-desktop.exe";

/// The name this exe should take: kratosforge.exe next to it, when it is fabric-desktop.exe and
/// nothing has that name yet (a second copy someone downloaded is never replaced).
pub fn new_name(exe: &Path) -> Option<PathBuf> {
    let name = exe.file_name()?.to_str()?;
    if !name.eq_ignore_ascii_case(LEGACY_FILE) {
        return None;
    }
    let to = exe.with_file_name(APP_FILE);
    std::fs::symlink_metadata(&to).is_err().then_some(to)
}

/// What the rename needs from the PC.
pub trait Host {
    /// Shortcuts whose target is `exe`. An error means they couldn't be looked for.
    fn shortcuts_to(&mut self, exe: &Path) -> Result<Vec<PathBuf>, String>;
    /// Points each of `links` that targets `from` at `to`; links that don't are left alone.
    fn retarget(&mut self, links: &[PathBuf], from: &Path, to: &Path) -> Result<(), String>;
    fn rename(&mut self, from: &Path, to: &Path) -> Result<(), String>;
}

#[derive(Debug, PartialEq)]
pub enum Outcome {
    /// Already kratosforge.exe, another name, or kratosforge.exe exists beside it.
    Unchanged,
    Renamed { to: PathBuf, shortcuts: Vec<PathBuf> },
}

/// Renames `exe` to kratosforge.exe and points its shortcuts there. On an error the file keeps
/// its name and the shortcuts their target, unless putting them back failed too (the error says).
pub fn migrate(exe: &Path, host: &mut impl Host) -> Result<Outcome, String> {
    let Some(to) = new_name(exe) else { return Ok(Outcome::Unchanged) };
    let links = host.shortcuts_to(exe).map_err(|e| format!("Could not look for shortcuts to {}: {e}", exe.display()))?;
    host.rename(exe, &to)?;
    if let Err(e) = host.retarget(&links, exe, &to) {
        // Links already changed point back again; ones that weren't are left alone.
        let back = host.retarget(&links, &to, exe).err();
        return Err(match (host.rename(&to, exe), back) {
            (Ok(()), None) => format!("A shortcut couldn't be changed ({e}), so the app keeps the name {LEGACY_FILE}."),
            (Ok(()), Some(b)) => format!(
                "A shortcut couldn't be changed ({e}), so the app keeps the name {LEGACY_FILE}, \
                 but some shortcuts couldn't be put back ({b}): point them at {}.", exe.display()),
            (Err(r), _) => format!(
                "A shortcut couldn't be changed ({e}) and {} couldn't be renamed back ({r}). \
                 The app is now {}: point any shortcut at that file.", to.display(), to.display()),
        });
    }
    Ok(Outcome::Renamed { to, shortcuts: links })
}

/// The real PC: shortcuts are read and changed through Windows' own shortcut object, run by
/// Windows PowerShell with the paths in environment variables (never in the script).
#[cfg(windows)]
pub struct Windows;

#[cfg(windows)]
const FIND: &str = r#"
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$roots = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('StartMenu'),
           (Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch'))
foreach ($root in $roots) {
  if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
  foreach ($file in Get-ChildItem -LiteralPath $root -Filter *.lnk -Recurse -File -ErrorAction SilentlyContinue) {
    try { $target = $shell.CreateShortcut($file.FullName).TargetPath } catch { continue }
    if ($target -and [string]::Equals($target, $env:KF_FROM, 'OrdinalIgnoreCase')) { [Console]::Out.WriteLine($file.FullName) }
  }
}
"#;

#[cfg(windows)]
const RETARGET: &str = r#"
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
foreach ($path in $env:KF_LINKS -split "`n") {
  if (-not $path) { continue }
  $link = $shell.CreateShortcut($path)
  if (-not [string]::Equals($link.TargetPath, $env:KF_FROM, 'OrdinalIgnoreCase')) { continue }
  $link.TargetPath = $env:KF_TO
  if ($link.IconLocation.StartsWith($env:KF_FROM + ',', 'OrdinalIgnoreCase')) {
    $link.IconLocation = $env:KF_TO + $link.IconLocation.Substring($env:KF_FROM.Length)
  }
  $link.Save()
}
"#;

#[cfg(windows)]
fn powershell(script: &str, env: &[(&str, String)]) -> Result<String, String> {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let root = std::env::var_os("SystemRoot").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(r"C:\Windows"));
    let mut cmd = std::process::Command::new(root.join(r"System32\WindowsPowerShell\v1.0\powershell.exe"));
    cmd.args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", &encode_command(script)])
        .stdin(std::process::Stdio::null())
        .creation_flags(CREATE_NO_WINDOW);
    for (k, v) in env {
        cmd.env(k, v);
    }
    let out = cmd.output().map_err(|e| format!("Windows PowerShell didn't start ({e})"))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).trim().chars().take(500).collect());
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

/// A path as shortcuts store it: without the \\?\ prefix Rust may add.
#[cfg(windows)]
fn plain(p: &Path) -> String {
    let s = p.to_string_lossy();
    s.strip_prefix(r"\\?\").unwrap_or(&s).to_string()
}

#[cfg(windows)]
impl Host for Windows {
    fn shortcuts_to(&mut self, exe: &Path) -> Result<Vec<PathBuf>, String> {
        let out = powershell(FIND, &[("KF_FROM", plain(exe))])?;
        Ok(out.lines().map(str::trim).filter(|l| !l.is_empty()).map(PathBuf::from).collect())
    }

    fn retarget(&mut self, links: &[PathBuf], from: &Path, to: &Path) -> Result<(), String> {
        if links.is_empty() {
            return Ok(());
        }
        let list = links.iter().map(|l| plain(l)).collect::<Vec<_>>().join("\n");
        powershell(RETARGET, &[("KF_LINKS", list), ("KF_FROM", plain(from)), ("KF_TO", plain(to))]).map(|_| ())
    }

    fn rename(&mut self, from: &Path, to: &Path) -> Result<(), String> {
        crate::update_guard::rename_retrying(from, to).map_err(|e| format!("Could not rename {} to {}: {e}", from.display(), to.display()))
    }
}

/// PowerShell's -EncodedCommand: the script as UTF-16LE, in base64.
pub fn encode_command(script: &str) -> String {
    const ABC: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let bytes: Vec<u8> = script.encode_utf16().flat_map(u16::to_le_bytes).collect();
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let n = chunk.iter().enumerate().fold(0u32, |n, (i, b)| n | ((*b as u32) << (16 - 8 * i)));
        for i in 0..4 {
            out.push(if i <= chunk.len() { ABC[((n >> (18 - 6 * i)) & 63) as usize] as char } else { '=' });
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("kf-name-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// Shortcuts are files holding their target's path. Each step named in `fail` fails;
    /// "second link" fails a forward retarget after it has changed the first link.
    struct Fake {
        links: Vec<PathBuf>,
        fail: Vec<&'static str>,
        calls: Vec<String>,
    }

    impl Fake {
        fn new(links: Vec<PathBuf>, fail: &[&'static str]) -> Self {
            Fake { links, fail: fail.to_vec(), calls: vec![] }
        }
        fn step(&mut self, name: &str) -> Result<(), String> {
            self.calls.push(name.to_string());
            if self.fail.contains(&name) { Err(format!("{name} failed")) } else { Ok(()) }
        }
    }

    fn back(to: &Path) -> bool {
        to.file_name().unwrap() == LEGACY_FILE
    }

    impl Host for Fake {
        fn shortcuts_to(&mut self, exe: &Path) -> Result<Vec<PathBuf>, String> {
            self.step("find")?;
            let exe = exe.to_string_lossy().to_string();
            Ok(self.links.iter().filter(|l| std::fs::read_to_string(l).unwrap() == exe).cloned().collect())
        }
        fn retarget(&mut self, links: &[PathBuf], from: &Path, to: &Path) -> Result<(), String> {
            self.step(if back(to) { "retarget back" } else { "retarget" })?;
            for l in links {
                if std::fs::read_to_string(l).unwrap() == from.to_string_lossy() {
                    std::fs::write(l, to.to_string_lossy().as_bytes()).unwrap();
                }
                if !back(to) && self.fail.contains(&"second link") {
                    return Err("second link failed".into());
                }
            }
            Ok(())
        }
        fn rename(&mut self, from: &Path, to: &Path) -> Result<(), String> {
            self.step(if back(to) { "rename back" } else { "rename" })?;
            std::fs::rename(from, to).map_err(|e| e.to_string())
        }
    }

    fn setup(name: &str) -> (PathBuf, PathBuf, Vec<PathBuf>) {
        let dir = scratch(name);
        let exe = dir.join(LEGACY_FILE);
        std::fs::write(&exe, "build").unwrap();
        let links = vec![dir.join("Desktop.lnk"), dir.join("Taskbar.lnk"), dir.join("Other.lnk")];
        std::fs::write(&links[0], exe.to_string_lossy().as_bytes()).unwrap();
        std::fs::write(&links[1], exe.to_string_lossy().as_bytes()).unwrap();
        std::fs::write(&links[2], "C:\\Something\\else.exe").unwrap();
        (dir, exe, links)
    }

    fn target(link: &Path) -> String {
        std::fs::read_to_string(link).unwrap()
    }

    #[test]
    fn only_a_copy_named_fabric_desktop_exe_is_renamed() {
        let dir = scratch("names");
        assert_eq!(new_name(&dir.join("kratosforge.exe")), None);
        assert_eq!(new_name(&dir.join("my-copy.exe")), None);
        assert_eq!(new_name(&dir.join("Fabric-Desktop.EXE")), Some(dir.join(APP_FILE)));
        std::fs::write(dir.join(APP_FILE), "a second copy").unwrap();
        assert_eq!(new_name(&dir.join(LEGACY_FILE)), None, "a kratosforge.exe already there is never replaced");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn renames_the_file_and_points_its_shortcuts_at_it() {
        let (dir, exe, links) = setup("ok");
        let mut host = Fake::new(links.clone(), &[]);
        let to = dir.join(APP_FILE);
        assert_eq!(migrate(&exe, &mut host), Ok(Outcome::Renamed { to: to.clone(), shortcuts: links[..2].to_vec() }));
        assert!(!exe.exists() && std::fs::read_to_string(&to).unwrap() == "build");
        assert_eq!(target(&links[0]), to.to_string_lossy());
        assert_eq!(target(&links[1]), to.to_string_lossy());
        assert_eq!(target(&links[2]), "C:\\Something\\else.exe", "other shortcuts are left alone");
        // Next start: already renamed, nothing to do.
        let mut again = Fake::new(links.clone(), &[]);
        assert_eq!(migrate(&to, &mut again), Ok(Outcome::Unchanged));
        assert!(again.calls.is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn nothing_is_renamed_when_shortcuts_cant_be_looked_for_or_the_rename_fails() {
        for fail in ["find", "rename"] {
            let (dir, exe, links) = setup(fail);
            let mut host = Fake::new(links.clone(), &[fail]);
            assert!(migrate(&exe, &mut host).is_err());
            assert!(exe.exists() && !dir.join(APP_FILE).exists());
            assert_eq!(target(&links[0]), exe.to_string_lossy());
            assert!(!host.calls.iter().any(|c| c.starts_with("retarget")), "{fail}: {:?}", host.calls);
            let _ = std::fs::remove_dir_all(&dir);
        }
    }

    #[test]
    fn a_shortcut_that_cant_be_changed_puts_everything_back() {
        let (dir, exe, links) = setup("back");
        let mut host = Fake::new(links.clone(), &["second link"]);
        let err = migrate(&exe, &mut host).unwrap_err();
        assert!(err.contains("keeps the name fabric-desktop.exe"), "{err}");
        assert!(exe.exists() && !dir.join(APP_FILE).exists());
        assert_eq!(target(&links[0]), exe.to_string_lossy(), "the changed shortcut points back");
        assert_eq!(target(&links[1]), exe.to_string_lossy());
        assert_eq!(host.calls, ["find", "rename", "retarget", "retarget back", "rename back"]);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn when_the_name_cant_be_put_back_the_error_names_the_new_file() {
        let (dir, exe, links) = setup("stuck");
        let mut host = Fake::new(links.clone(), &["second link", "rename back"]);
        let err = migrate(&exe, &mut host).unwrap_err();
        assert!(err.contains("The app is now") && err.contains(APP_FILE), "{err}");
        assert!(dir.join(APP_FILE).exists() && !exe.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn encodes_scripts_the_way_powershell_reads_them() {
        // [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes('dir'))
        assert_eq!(encode_command("dir"), "ZABpAHIA");
        assert_eq!(encode_command("ab"), "YQBiAA==");
        assert_eq!(encode_command(""), "");
    }

    /// The real thing on Windows (CI's shell job runs this): a shortcut on a scratch folder is
    /// found, pointed at kratosforge.exe, and the update's swap then works under the new name.
    #[cfg(windows)]
    #[test]
    fn windows_shortcuts_follow_the_rename_and_updates_keep_working() {
        let dir = scratch("win");
        let exe = dir.join(LEGACY_FILE);
        std::fs::write(&exe, "previous build").unwrap();
        let link = dir.join("KratosForge.lnk");
        let make = r#"$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:KF_LINK)
$s.TargetPath = $env:KF_FROM; $s.IconLocation = $env:KF_FROM + ',0'; $s.Save()"#;
        powershell(make, &[("KF_LINK", plain(&link)), ("KF_FROM", plain(&exe))]).unwrap();
        // The scratch folder isn't one FIND looks in, so this host finds the scratch link only.
        struct Scratch(PathBuf);
        impl Host for Scratch {
            fn shortcuts_to(&mut self, _: &Path) -> Result<Vec<PathBuf>, String> { Ok(vec![self.0.clone()]) }
            fn retarget(&mut self, l: &[PathBuf], f: &Path, t: &Path) -> Result<(), String> { Windows.retarget(l, f, t) }
            fn rename(&mut self, f: &Path, t: &Path) -> Result<(), String> { Windows.rename(f, t) }
        }
        let to = dir.join(APP_FILE);
        assert!(matches!(migrate(&exe, &mut Scratch(link.clone())), Ok(Outcome::Renamed { .. })));
        let read = "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:KF_LINK); $s.TargetPath; $s.IconLocation";
        let out = powershell(read, &[("KF_LINK", plain(&link))]).unwrap();
        let lines: Vec<&str> = out.lines().collect();
        assert!(lines[0].eq_ignore_ascii_case(&plain(&to)), "{out}");
        assert!(lines[1].to_ascii_lowercase().ends_with("kratosforge.exe,0"), "{out}");
        // The next update swaps in place under the new name and keeps the previous exe beside it.
        let (new, old) = (to.with_extension("exe.new"), to.with_extension("exe.old"));
        std::fs::write(&new, "next build").unwrap();
        crate::update_guard::swap_in(&to, &new, &old).unwrap();
        assert_eq!(std::fs::read_to_string(&to).unwrap(), "next build");
        assert_eq!(old.file_name().unwrap(), "kratosforge.exe.old");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// FIND runs on this PC without error (it only reads shortcuts).
    #[cfg(windows)]
    #[test]
    fn windows_finds_no_shortcut_to_a_file_that_isnt_linked() {
        let dir = scratch("winfind");
        assert_eq!(Windows.shortcuts_to(&dir.join(LEGACY_FILE)).unwrap(), Vec::<PathBuf>::new());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
