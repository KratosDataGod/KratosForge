//! Whether the policy file can be trusted with settings that decide what code runs, such as
//! `engine_download_url`. Everyone may create folders under %ProgramData%, so on a PC whose IT
//! pushes no policy, a standard user could create %ProgramData%\FabricDesktop\policy.json
//! themselves and point the next person's engine download at their own folder. So a file is
//! trusted only when it and its folder are owned by SYSTEM or Administrators (as an Intune,
//! Group Policy or elevated copy leaves them) and the account running the app, unless it is an
//! administrator, can't change either.

use std::path::Path;

#[cfg(windows)]
pub fn admin_only(file: &Path) -> bool {
    let Some(folder) = file.parent() else { return false };
    owned_by_admins(file) && owned_by_admins(folder) && !win::user_can_change(file, folder)
}

/// Off Windows the shell doesn't download an engine, so nothing there is trusted.
#[cfg(not(windows))]
pub fn admin_only(_file: &Path) -> bool {
    false
}

#[cfg(windows)]
fn owned_by_admins(path: &Path) -> bool {
    win::owner_is_admin(path).unwrap_or(false)
}

#[cfg(windows)]
mod win {
    use std::os::windows::ffi::OsStrExt;
    use std::path::Path;
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::{CloseHandle, LocalFree, ERROR_SUCCESS, HLOCAL};
    use windows::Win32::Security::Authorization::{GetNamedSecurityInfoW, SE_FILE_OBJECT};
    use windows::Win32::Security::{
        IsWellKnownSid, WinBuiltinAdministratorsSid, WinLocalSystemSid, OWNER_SECURITY_INFORMATION,
        PSECURITY_DESCRIPTOR, PSID,
    };
    use windows::Win32::Storage::FileSystem::{
        CreateFileW, FILE_FLAG_BACKUP_SEMANTICS, FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING,
    };
    use windows::Win32::UI::Shell::IsUserAnAdmin;

    // The rights that let someone change or replace a file, and a folder's file (as in
    // app_settings._FILE_RIGHTS and _FOLDER_RIGHTS): asked for one at a time.
    const FILE_WRITE_DATA: u32 = 0x2;
    const DELETE: u32 = 0x1_0000;
    const WRITE_DAC: u32 = 0x4_0000;
    const WRITE_OWNER: u32 = 0x8_0000;
    const FILE_DELETE_CHILD: u32 = 0x40;
    const FILE_RIGHTS: [u32; 4] = [FILE_WRITE_DATA, DELETE, WRITE_DAC, WRITE_OWNER];
    const FOLDER_RIGHTS: [u32; 3] = [FILE_DELETE_CHILD, WRITE_DAC, WRITE_OWNER];

    fn wide(path: &Path) -> Vec<u16> {
        path.as_os_str().encode_wide().chain(Some(0)).collect()
    }

    /// Whether the path's owner is SYSTEM or the Administrators group; None if it can't be read.
    pub fn owner_is_admin(path: &Path) -> Option<bool> {
        let name = wide(path);
        let mut owner = PSID::default();
        let mut descriptor = PSECURITY_DESCRIPTOR::default();
        // SAFETY: name is NUL-terminated and outlives the call; owner points into descriptor,
        // which is read before descriptor is freed with LocalFree as the API requires.
        unsafe {
            let status = GetNamedSecurityInfoW(PCWSTR(name.as_ptr()), SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION,
                                               Some(&mut owner), None, None, None, &mut descriptor);
            if status != ERROR_SUCCESS {
                return None;
            }
            let admin = IsWellKnownSid(owner, WinLocalSystemSid).as_bool()
                || IsWellKnownSid(owner, WinBuiltinAdministratorsSid).as_bool();
            let _ = LocalFree(Some(HLOCAL(descriptor.0)));
            Some(admin)
        }
    }

    /// Whether the account running the app could change or replace the file; an administrator
    /// could anyway and isn't counted. Opening only asks for the right; nothing is written.
    pub fn user_can_change(file: &Path, folder: &Path) -> bool {
        // SAFETY: IsUserAnAdmin takes no arguments.
        if unsafe { IsUserAnAdmin() }.as_bool() {
            return false;
        }
        let opens = |path: &Path, access: u32| {
            let name = wide(path);
            // SAFETY: name is NUL-terminated and outlives the call; a handle that opens is closed.
            unsafe {
                match CreateFileW(PCWSTR(name.as_ptr()), access, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                  None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None) {
                    Ok(handle) => {
                        let _ = CloseHandle(handle);
                        true
                    }
                    Err(_) => false,
                }
            }
        };
        FILE_RIGHTS.iter().any(|&r| opens(file, r)) || FOLDER_RIGHTS.iter().any(|&r| opens(folder, r))
    }
}

#[cfg(test)]
mod tests {
    use super::admin_only;

    fn running_as_admin() -> bool {
        #[cfg(windows)]
        // SAFETY: IsUserAnAdmin takes no arguments.
        return unsafe { windows::Win32::UI::Shell::IsUserAnAdmin() }.as_bool();
        #[cfg(not(windows))]
        false
    }

    #[test]
    fn a_file_anyone_could_have_written_is_not_trusted() {
        // A file the test itself just made is owned by the account running the tests, never by
        // SYSTEM or Administrators, so it can't name an engine folder (on Windows by its owner,
        // elsewhere because nothing is trusted).
        let dir = std::env::temp_dir().join(format!("kf-policy-trust-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("policy.json");
        std::fs::write(&file, r#"{"auto_update": false, "engine_download_url": "https://evil.example.com/kf"}"#).unwrap();
        let trusted = admin_only(&file);
        let _ = std::fs::remove_dir_all(&dir);
        // An elevated administrator's files are owned by Administrators, and an administrator may
        // name an engine folder; CI's Windows runner is one.
        assert!(!trusted || running_as_admin());
        assert!(!admin_only(std::path::Path::new("C:\\no\\such\\folder\\policy.json")));
    }
}
