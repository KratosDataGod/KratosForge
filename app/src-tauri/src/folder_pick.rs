//! The Browse… button beside each folder box (#971): the system's own folder dialog, owned by
//! the window that asked, so it is modal to that window. It only returns the folder the person
//! chose; the page still sends that path through the same checks as a typed one.

/// The chosen folder, or None when the person cancels. Starts in `start` when that is an
/// existing folder on a local drive letter (a network path is never looked up just to start
/// there). Call it on a thread of its own: it sets up a single-threaded COM apartment for the
/// dialog and takes it down again.
#[cfg(windows)]
pub fn pick(owner: isize, start: Option<String>) -> Result<Option<String>, String> {
    use std::path::Path;
    use windows::core::HSTRING;
    use windows::Win32::Foundation::{ERROR_CANCELLED, HWND};
    use windows::Win32::System::Com::{
        CoCreateInstance, CoInitializeEx, CoTaskMemFree, CoUninitialize, CLSCTX_INPROC_SERVER,
        COINIT_APARTMENTTHREADED, COINIT_DISABLE_OLE1DDE,
    };
    use windows::Win32::UI::Shell::{
        FileOpenDialog, IFileOpenDialog, IShellItem, SHCreateItemFromParsingName, FOS_FORCEFILESYSTEM,
        FOS_NOCHANGEDIR, FOS_PATHMUSTEXIST, FOS_PICKFOLDERS, SIGDN_FILESYSPATH,
    };

    struct Apartment;
    impl Drop for Apartment {
        fn drop(&mut self) { unsafe { CoUninitialize() } }
    }
    let failed = |e: windows::core::Error| format!("Could not show the folder dialog: {}", e.message());

    unsafe {
        CoInitializeEx(None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE).ok().map_err(failed)?;
        // Declared before the dialog, so the dialog is released before the apartment goes.
        let _apartment = Apartment;
        let dialog: IFileOpenDialog = CoCreateInstance(&FileOpenDialog, None, CLSCTX_INPROC_SERVER).map_err(failed)?;
        let options = dialog.GetOptions().map_err(failed)?;
        dialog.SetOptions(options | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | FOS_NOCHANGEDIR)
            .map_err(failed)?;
        if let Some(dir) = start.filter(|s| on_drive(s) && Path::new(s).is_dir()) {
            // A start folder the shell can't read just leaves the dialog where Windows opens it.
            if let Ok(item) = SHCreateItemFromParsingName::<_, _, IShellItem>(&HSTRING::from(dir), None) {
                let _ = dialog.SetFolder(&item);
            }
        }
        match dialog.Show(Some(HWND(owner as *mut _))) {
            Err(e) if e.code() == ERROR_CANCELLED.to_hresult() => return Ok(None),
            other => other.map_err(failed)?,
        }
        let name = dialog.GetResult().and_then(|item| item.GetDisplayName(SIGDN_FILESYSPATH)).map_err(failed)?;
        let path = name.to_string();
        CoTaskMemFree(Some(name.0 as *const _));
        path.map(Some).map_err(|_| "The folder's name could not be read".into())
    }
}

/// `C:\...` or `C:/...`: a drive letter, so not a `\\server\share` or `\\?\` path.
#[cfg_attr(not(windows), allow(dead_code))]
fn on_drive(path: &str) -> bool {
    let b = path.as_bytes();
    b.len() >= 3 && b[0].is_ascii_alphabetic() && b[1] == b':' && (b[2] == b'\\' || b[2] == b'/')
}

#[cfg(test)]
mod tests {
    #[test]
    fn starts_only_on_a_drive_letter() {
        for ok in ["C:\\Users\\you", "d:/data", "E:\\"] { assert!(super::on_drive(ok), "{ok}"); }
        for no in ["", "C:", "C:relative", "\\\\server\\share", "\\\\?\\C:\\x", "//server/share", "relative\\x", "1:\\x"] {
            assert!(!super::on_drive(no), "{no}");
        }
    }
}

/// Off Windows the page doesn't show Browse…, so this only answers a stray call.
#[cfg(not(windows))]
pub fn pick(_owner: isize, _start: Option<String>) -> Result<Option<String>, String> {
    Err("The folder dialog is only on Windows".into())
}
