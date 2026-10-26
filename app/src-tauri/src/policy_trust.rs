//! Whether the policy file can be trusted with settings that decide what code runs, such as
//! `engine_download_url`. Everyone may create folders under %ProgramData%, so on a PC whose IT
//! pushes no policy, a standard user could create %ProgramData%\FabricDesktop\policy.json
//! themselves and point the next person's engine download at their own folder. So a file is
//! trusted only when it and its folder are owned by SYSTEM or Administrators (as an Intune,
//! Group Policy or elevated copy leaves them) and nobody else is granted a right that could
//! change or replace the file: anyone else could otherwise rewrite it for the next person.

use std::path::Path;

/// None when only SYSTEM and Administrators could have written the file; otherwise why not.
#[cfg(windows)]
pub fn admin_only(file: &Path) -> Result<(), String> {
    let folder = file.parent().ok_or("it has no folder")?;
    win::check(folder, win::FOLDER_RIGHTS)?;
    win::check(file, win::FILE_RIGHTS)
}

/// Off Windows the shell doesn't download an engine, so nothing there is trusted.
#[cfg(not(windows))]
pub fn admin_only(_file: &Path) -> Result<(), String> {
    Err("it is only trusted on Windows".into())
}

#[cfg(windows)]
mod win {
    use std::os::windows::ffi::OsStrExt;
    use std::path::Path;
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::{LocalFree, ERROR_SUCCESS, HLOCAL};
    use windows::Win32::Security::Authorization::{GetNamedSecurityInfoW, SE_FILE_OBJECT};
    use windows::Win32::Security::{
        GetAce, IsWellKnownSid, WinBuiltinAdministratorsSid, WinLocalSystemSid, ACCESS_ALLOWED_ACE, ACE_HEADER, ACL,
        DACL_SECURITY_INFORMATION, OWNER_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR, PSID,
    };

    const FILE_WRITE_DATA: u32 = 0x2;
    const FILE_APPEND_DATA: u32 = 0x4;
    const FILE_DELETE_CHILD: u32 = 0x40;
    const DELETE: u32 = 0x1_0000;
    const WRITE_DAC: u32 = 0x4_0000;
    const WRITE_OWNER: u32 = 0x8_0000;
    const GENERIC_ALL: u32 = 0x1000_0000;
    const GENERIC_WRITE: u32 = 0x4000_0000;
    /// Rights that let someone change or replace the file itself.
    pub const FILE_RIGHTS: u32 = FILE_WRITE_DATA | FILE_APPEND_DATA | DELETE | WRITE_DAC | WRITE_OWNER | GENERIC_WRITE | GENERIC_ALL;
    /// Rights on the folder that let someone delete or replace a file in it, or the folder.
    /// Creating new files isn't one: %ProgramData% lets everyone do that in folders under it.
    pub const FOLDER_RIGHTS: u32 = FILE_DELETE_CHILD | DELETE | WRITE_DAC | WRITE_OWNER | GENERIC_ALL;
    const ACCESS_ALLOWED: u8 = 0;
    const ACCESS_DENIED: u8 = 1;
    const INHERIT_ONLY: u8 = 0x08;

    fn admin_sid(sid: PSID) -> bool {
        // SAFETY: sid points into a security descriptor that is still allocated.
        unsafe { IsWellKnownSid(sid, WinLocalSystemSid).as_bool() || IsWellKnownSid(sid, WinBuiltinAdministratorsSid).as_bool() }
    }

    /// Ok when the path is owned by SYSTEM or Administrators and its DACL grants `rights` to nobody
    /// else. A deny entry is ignored (it can only take rights away), as is one that applies only
    /// to what is created inside a folder; any other kind of grant counts against it.
    pub fn check(path: &Path, rights: u32) -> Result<(), String> {
        let name: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut owner = PSID::default();
        let mut dacl: *mut ACL = std::ptr::null_mut();
        let mut descriptor = PSECURITY_DESCRIPTOR::default();
        // SAFETY: name is NUL-terminated and outlives the call. owner and dacl point into
        // descriptor, which is only read before it is freed with LocalFree as the API requires.
        unsafe {
            let status = GetNamedSecurityInfoW(PCWSTR(name.as_ptr()), SE_FILE_OBJECT,
                                               OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                                               Some(&mut owner), None, Some(&mut dacl), None, &mut descriptor);
            if status != ERROR_SUCCESS {
                return Err(format!("its permissions can't be read ({})", status.0));
            }
            let result = (|| {
                if !admin_sid(owner) {
                    return Err(format!("{} isn't owned by SYSTEM or Administrators", path.display()));
                }
                if dacl.is_null() {
                    return Err(format!("{} has no permissions set, so anyone can change it", path.display()));
                }
                for index in 0..u32::from((*dacl).AceCount) {
                    let mut ace: *mut core::ffi::c_void = std::ptr::null_mut();
                    if GetAce(dacl, index, &mut ace).is_err() {
                        return Err(format!("{}'s permissions can't be read", path.display()));
                    }
                    let header = &*(ace as *const ACE_HEADER);
                    if header.AceType == ACCESS_DENIED || header.AceType > 17 || header.AceFlags & INHERIT_ONLY != 0 {
                        continue;
                    }
                    // Deny entries (6, 10, 12), audit and alarm entries (2, 3, 7, 8, 13, 14, 15, 16)
                    // and the mandatory label (17) grant nothing.
                    if matches!(header.AceType, 2 | 3 | 6 | 7 | 8 | 10 | 12 | 13 | 14 | 15 | 16 | 17) {
                        continue;
                    }
                    if header.AceType != ACCESS_ALLOWED {
                        return Err(format!("{} has a permission entry the app can't check", path.display()));
                    }
                    let allowed = &*(ace as *const ACCESS_ALLOWED_ACE);
                    let sid = PSID(&allowed.SidStart as *const u32 as *mut _);
                    if allowed.Mask & rights != 0 && !admin_sid(sid) {
                        return Err(format!("someone other than SYSTEM and Administrators can change {}", path.display()));
                    }
                }
                Ok(())
            })();
            let _ = LocalFree(Some(HLOCAL(descriptor.0)));
            result
        }
    }
}

#[cfg(test)]
mod tests {
    use super::admin_only;

    #[test]
    fn a_file_anyone_could_have_written_is_not_trusted() {
        // A file in the temp folder: on Windows its folder grants the account running the tests
        // full control (whoever owns it), elsewhere nothing is trusted.
        let dir = std::env::temp_dir().join(format!("kf-policy-trust-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("policy.json");
        std::fs::write(&file, r#"{"auto_update": false, "engine_download_url": "https://evil.example.com/kf"}"#).unwrap();
        let trusted = admin_only(&file);
        let _ = std::fs::remove_dir_all(&dir);
        assert!(trusted.is_err(), "{trusted:?}");
        assert!(admin_only(std::path::Path::new("C:\\no\\such\\folder\\policy.json")).is_err());
    }
}
