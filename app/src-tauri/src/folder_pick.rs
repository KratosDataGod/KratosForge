//! The Browse… button beside each folder box (#971): the system's own folder dialog, owned by
//! the window that asked, so it is modal to that window. It only returns the folder the person
//! chose; the page still sends that path through the same checks as a typed one.

/// The chosen folder, or None when the person cancels. Starts in `start` when that is an
/// existing folder on a local drive letter (a UNC path or a mapped network drive is never looked
/// up just to start there). Call it on a thread of its own: it sets up a single-threaded COM apartment for the
/// dialog and takes it down again.
#[cfg(windows)]
pub fn pick(owner: isize, start: Option<String>) -> Result<Option<String>, String> {
    use std::path::Path;
    use windows::core::HSTRING;
    use windows::Win32::Foundation::{ERROR_CANCELLED, HWND};
    use windows::Win32::Storage::FileSystem::GetDriveTypeW;
    use windows::Win32::System::Com::{
        CoCreateInstance, CoInitializeEx, CoTaskMemFree, CoUninitialize, CLSCTX_INPROC_SERVER,
        COINIT_APARTMENTTHREADED, COINIT_DISABLE_OLE1DDE,
    };
    use windows::Win32::UI::Shell::{
        FileOpenDialog, IFileOpenDialog, IShellItem, SHCreateItemFromParsingName, FOS_FORCEFILESYSTEM,
        FOS_NOCHANGEDIR, FOS_PATHMUSTEXIST, FOS_PICKFOLDERS, SIGDN_FILESYSPATH,
    };
    const DRIVE_REMOTE: u32 = 4; // winbase.h

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
        // GetDriveTypeW reads only the drive letter's type, so a network drive isn't touched.
        let local = |s: &str| GetDriveTypeW(&HSTRING::from(format!("{}:\\", &s[..1]))) != DRIVE_REMOTE;
        if let Some(dir) = start_folder(start, local, |s| Path::new(s).is_dir()) {
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

/// The folder to open the dialog in: `start` when it is on a drive letter, that drive isn't a
/// network drive (`local`), and it is a folder (`is_dir`), asked in that order, so neither of the
/// later two is ever asked about a UNC path, and `is_dir` never about a network drive.
#[cfg_attr(not(windows), allow(dead_code))]
fn start_folder(start: Option<String>, local: impl Fn(&str) -> bool, is_dir: impl Fn(&str) -> bool) -> Option<String> {
    start.filter(|s| on_drive(s) && local(s) && is_dir(s))
}

/// `C:\...` or `C:/...`: a drive letter, so not a `\\server\share` or `\\?\` path.
#[cfg_attr(not(windows), allow(dead_code))]
fn on_drive(path: &str) -> bool {
    let b = path.as_bytes();
    b.len() >= 3 && b[0].is_ascii_alphabetic() && b[1] == b':' && (b[2] == b'\\' || b[2] == b'/')
}

/// Off Windows the page doesn't show Browse…, so this only answers a stray call.
#[cfg(not(windows))]
pub fn pick(_owner: isize, _start: Option<String>) -> Result<Option<String>, String> {
    Err("The folder dialog is only on Windows".into())
}

/// Editor parity S38: the system's own dialog to open a .sql file, or (with `save_name`, the name it
/// suggests) to save one, owned by the window that asked. Only returns the path the person chose; the
/// engine still checks it (a .sql file, not too big) before it reads or writes anything. Saving over a
/// file asks in the dialog itself. Starts in the folder of `start` (a file or a folder) under the same
/// rules as `pick`. Call it on a thread of its own, as `pick`.
#[cfg(windows)]
pub fn pick_sql(owner: isize, start: Option<String>, save_name: Option<String>) -> Result<Option<String>, String> {
    use std::path::Path;
    use windows::core::{w, Interface, HSTRING};
    use windows::Win32::Foundation::{ERROR_CANCELLED, HWND};
    use windows::Win32::Storage::FileSystem::GetDriveTypeW;
    use windows::Win32::System::Com::{
        CoCreateInstance, CoInitializeEx, CoTaskMemFree, CoUninitialize, CLSCTX_INPROC_SERVER,
        COINIT_APARTMENTTHREADED, COINIT_DISABLE_OLE1DDE,
    };
    use windows::Win32::UI::Shell::Common::COMDLG_FILTERSPEC;
    use windows::Win32::UI::Shell::{
        FileOpenDialog, FileSaveDialog, IFileDialog, IFileOpenDialog, IFileSaveDialog, IShellItem,
        SHCreateItemFromParsingName, FOS_FILEMUSTEXIST, FOS_FORCEFILESYSTEM, FOS_NOCHANGEDIR, FOS_OVERWRITEPROMPT,
        FOS_PATHMUSTEXIST, SIGDN_FILESYSPATH,
    };
    const DRIVE_REMOTE: u32 = 4; // winbase.h

    struct Apartment;
    impl Drop for Apartment {
        fn drop(&mut self) { unsafe { CoUninitialize() } }
    }
    let failed = |e: windows::core::Error| format!("Could not show the file dialog: {}", e.message());

    unsafe {
        CoInitializeEx(None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE).ok().map_err(failed)?;
        let _apartment = Apartment;
        let dialog: IFileDialog = if save_name.is_some() {
            CoCreateInstance::<_, IFileSaveDialog>(&FileSaveDialog, None, CLSCTX_INPROC_SERVER).map_err(failed)?.cast().map_err(failed)?
        } else {
            CoCreateInstance::<_, IFileOpenDialog>(&FileOpenDialog, None, CLSCTX_INPROC_SERVER).map_err(failed)?.cast().map_err(failed)?
        };
        let options = dialog.GetOptions().map_err(failed)?;
        let which = if save_name.is_some() { FOS_OVERWRITEPROMPT } else { FOS_FILEMUSTEXIST };
        dialog.SetOptions(options | which | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | FOS_NOCHANGEDIR).map_err(failed)?;
        let types = [COMDLG_FILTERSPEC { pszName: w!("SQL files (*.sql)"), pszSpec: w!("*.sql") }];
        dialog.SetFileTypes(&types).map_err(failed)?;
        dialog.SetDefaultExtension(w!("sql")).map_err(failed)?;
        if let Some(name) = save_name.as_deref().filter(|n| !n.is_empty()) {
            dialog.SetFileName(&HSTRING::from(name)).map_err(failed)?;
        }
        let local = |s: &str| GetDriveTypeW(&HSTRING::from(format!("{}:\\", &s[..1]))) != DRIVE_REMOTE;
        let folder = start.map(|s| if s.to_ascii_lowercase().ends_with(".sql") {
            Path::new(&s).parent().map(|p| p.to_string_lossy().into_owned()).unwrap_or_default()
        } else { s });
        if let Some(dir) = start_folder(folder, local, |s| Path::new(s).is_dir()) {
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
        path.map(Some).map_err(|_| "The file's name could not be read".into())
    }
}

/// Off Windows the page has no file to open, so this only answers a stray call.
#[cfg(not(windows))]
pub fn pick_sql(_owner: isize, _start: Option<String>, _save_name: Option<String>) -> Result<Option<String>, String> {
    Err("The file dialog is only on Windows".into())
}

#[cfg(test)]
mod tests {
    use super::start_folder;
    use std::cell::RefCell;

    /// Runs start_folder with stand-ins that record each question asked.
    fn ask(start: Option<&str>, remote: bool, dir: bool) -> (Option<String>, Vec<String>) {
        let asked = RefCell::new(Vec::new());
        let got = start_folder(
            start.map(str::to_string),
            |s| { asked.borrow_mut().push(format!("local {s}")); !remote },
            |s| { asked.borrow_mut().push(format!("is_dir {s}")); dir },
        );
        (got, asked.into_inner())
    }

    #[test]
    fn a_local_folder_on_a_drive_letter_is_where_the_dialog_starts() {
        assert_eq!(ask(Some("C:\\Data"), false, true), (Some("C:\\Data".into()), vec!["local C:\\Data".into(), "is_dir C:\\Data".into()]));
        assert_eq!(ask(Some("d:/data"), false, true).0.as_deref(), Some("d:/data"));
    }

    #[test]
    fn nothing_is_asked_about_a_path_that_isnt_on_a_drive_letter() {
        for path in ["\\\\server\\share\\x", "\\\\?\\C:\\x", "//server/share", "relative", "C:", ""] {
            assert_eq!(ask(Some(path), false, true), (None, vec![]), "{path}");
        }
        assert_eq!(ask(None, false, true), (None, vec![]));
    }

    #[test]
    fn a_network_drive_is_never_looked_at() {
        // A mapped drive: only its type is read, never the folder.
        assert_eq!(ask(Some("Z:\\Team"), true, true), (None, vec!["local Z:\\Team".into()]));
    }

    #[test]
    fn a_folder_that_isnt_there_leaves_the_dialog_where_windows_opens_it() {
        assert_eq!(ask(Some("C:\\Gone"), false, false).0, None);
    }

    #[test]
    fn starts_only_on_a_drive_letter() {
        for ok in ["C:\\Users\\you", "d:/data", "E:\\"] { assert!(super::on_drive(ok), "{ok}"); }
        for no in ["", "C:", "C:relative", "\\\\server\\share", "\\\\?\\C:\\x", "//server/share", "relative\\x", "1:\\x"] {
            assert!(!super::on_drive(no), "{no}");
        }
    }
}
