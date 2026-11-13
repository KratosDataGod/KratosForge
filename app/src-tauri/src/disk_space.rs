//! Room for the engine in the person's profile, and plain words when there isn't any.
//!
//! The engine is unpacked into `%LOCALAPPDATA%\FabricDesktop\engine`: a download of about 330 MB
//! that unpacks to about 950 MB. On a shared or virtual desktop that folder sits on a profile disk
//! (FSLogix, a roaming profile) that can be small, full, or attached read-only. curl, tar and the
//! file calls then fail with their own words ("curl: (23) Failure writing output", "os error 112"),
//! which read like a broken download. `check_room` stops a whole-engine install before it starts
//! when the drive can't hold it, and `explain` puts one line in front of a failure to write there.

/// Bytes a whole-engine install needs free: the zip and the unpacked engine side by side, with room
/// to spare (the update zip that replaces only the engine's code is a few MB and isn't checked).
pub const ENGINE_NEEDS: u64 = 1_500 * 1024 * 1024;

/// Free bytes for this account on `folder`'s drive, or None when Windows can't say.
#[cfg(windows)]
pub fn free_bytes(folder: &std::path::Path) -> Option<u64> {
    use windows::core::HSTRING;
    use windows::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;
    let mut free = 0u64;
    unsafe { GetDiskFreeSpaceExW(&HSTRING::from(folder.as_os_str()), Some(&mut free), None, None) }.ok()?;
    Some(free)
}

#[cfg(not(windows))]
pub fn free_bytes(_folder: &std::path::Path) -> Option<u64> {
    None
}

/// Refuses a whole-engine install into `folder` when `free` (its drive's free bytes) is under what
/// it needs. None (Windows couldn't say) lets it go ahead.
pub fn check_room(folder: &std::path::Path, free: Option<u64>) -> Result<(), String> {
    match free {
        Some(free) if free < ENGINE_NEEDS => Err(format!(
            "There isn't room for the engine in {}: it needs about {} free and the drive has {}. Free some space and \
             choose Retry. On a shared or virtual desktop, ask IT to make your profile disk bigger.",
            folder.display(),
            size(ENGINE_NEEDS),
            size(free)
        )),
        _ => Ok(()),
    }
}

/// `err` from installing the engine into `folder`, after one line that says the drive is full or
/// can't be written when that is the cause. Anything else is returned as it was.
pub fn explain(err: &str, folder: &std::path::Path) -> String {
    let low = err.to_ascii_lowercase();
    let full = ["curl: (23)", "os error 112", "os error 39", "no space left", "not enough space", "disk is full"]
        .iter()
        .any(|t| low.contains(t));
    let refused = ["os error 5)", "os error 19)", "access is denied", "write-protected", "read-only file system"]
        .iter()
        .any(|t| low.contains(t));
    let hint = if full {
        format!(
            "The engine couldn't be saved: the drive that holds {} is full. Free some space and choose Retry. On a \
             shared or virtual desktop, ask IT to make your profile disk bigger.",
            folder.display()
        )
    } else if refused {
        format!(
            "Windows won't let KratosForge write to {}. On a shared or virtual desktop the profile disk may be \
             attached read-only; sign out and in again, or ask IT to check it.",
            folder.display()
        )
    } else {
        return err.to_string();
    };
    format!("{hint} ({err})")
}

fn size(n: u64) -> String {
    let mb = n as f64 / (1024.0 * 1024.0);
    if mb >= 1024.0 {
        format!("{:.1} GB", mb / 1024.0)
    } else {
        format!("{mb:.0} MB")
    }
}

#[cfg(test)]
mod tests {
    use super::{check_room, explain, ENGINE_NEEDS};
    use std::path::Path;

    #[test]
    fn a_drive_too_small_for_the_engine_is_refused_before_the_download() {
        let folder = Path::new(r"C:\Users\a\AppData\Local\FabricDesktop\engine");
        let err = check_room(folder, Some(200 * 1024 * 1024)).unwrap_err();
        assert!(err.starts_with("There isn't room for the engine in"), "{err}");
        assert!(err.contains("1.5 GB") && err.contains("200 MB"), "{err}");
        assert!(check_room(folder, Some(ENGINE_NEEDS)).is_ok());
        assert!(check_room(folder, None).is_ok(), "Windows couldn't say: go ahead");
    }

    #[test]
    fn a_full_or_read_only_profile_gets_one_line_first() {
        let folder = Path::new(r"C:\Users\a\AppData\Local\FabricDesktop\engine");
        for full in ["Could not download the engine: curl: (23) Failure writing output to destination",
                     "Could not unpack the engine: There is not enough space on the disk. (os error 112)"] {
            let text = explain(full, folder);
            assert!(text.starts_with("The engine couldn't be saved: the drive that holds"), "{text}");
            assert!(text.ends_with(&format!("({full})")), "the original stays for IT");
        }
        let denied = explain("Could not put the engine in place: Access is denied. (os error 5)", folder);
        assert!(denied.starts_with("Windows won't let KratosForge write to"), "{denied}");
        let other = "Could not download the engine: curl: (6) Could not resolve host: github.com";
        assert_eq!(explain(other, folder), other);
    }
}
