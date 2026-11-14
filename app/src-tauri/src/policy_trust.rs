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
    win::check(folder, win::FOLDER_RIGHTS, false)?;
    win::check(file, win::FILE_RIGHTS, false)
}

/// Off Windows the shell doesn't download an engine, so nothing there is trusted.
#[cfg(not(windows))]
pub fn admin_only(_file: &Path) -> Result<(), String> {
    Err("it is only trusted on Windows".into())
}

/// For a file or folder of IT's engine (engine_folder.rs): owned by SYSTEM or Administrators, and
/// nobody else may change it or, for a folder, add, replace or delete anything in it. Stricter than
/// the policy file's folder, where everyone may create files.
#[cfg(windows)]
pub fn admin_only_engine(path: &Path) -> Result<(), String> {
    // Grants that only pass down to what is inside count too: every engine file inherits them.
    win::check(path, win::ENGINE_RIGHTS, true)
}

#[cfg(not(windows))]
pub fn admin_only_engine(_path: &Path) -> Result<(), String> {
    Err("it is only trusted on Windows".into())
}

/// For a folder above IT's engine folder: owned by SYSTEM or Administrators, and nobody else may
/// delete, rename or replace what is in it or take over its permissions. Creating new folders is
/// allowed, as everyone may on C:\.
#[cfg(windows)]
pub fn admin_only_ancestor(path: &Path) -> Result<(), String> {
    win::check(path, win::FOLDER_RIGHTS, false)
}

#[cfg(not(windows))]
pub fn admin_only_ancestor(_path: &Path) -> Result<(), String> {
    Err("it is only trusted on Windows".into())
}

/// Who a permission entry is for, as far as these checks care.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
#[cfg_attr(not(windows), allow(dead_code))]
enum Who {
    /// SYSTEM, Administrators or TrustedInstaller.
    Admin,
    /// CREATOR OWNER (S-1-3-0): an entry passed down to whoever creates something inside, who is
    /// an administrator in a folder only administrators can create things in.
    CreatorOwner,
    Other,
}

#[derive(PartialEq, Eq, Debug)]
#[cfg_attr(not(windows), allow(dead_code))]
enum Entry {
    /// Grants none of the rights to anyone but an administrator.
    Fine,
    /// Grants one of the rights to someone else.
    Grants,
    /// A kind of entry the app can't check, so it counts against the path.
    Unknown,
}

const ACCESS_ALLOWED: u8 = 0;
const ACCESS_DENIED: u8 = 1;
const OBJECT_INHERIT: u8 = 0x01;
const CONTAINER_INHERIT: u8 = 0x02;
const INHERIT_ONLY: u8 = 0x08;

/// What one entry of a path's permissions means for `rights`. An entry that applies only to what
/// is created inside a folder (inherit-only) counts only when `inherited_too`, and then not for
/// CREATOR OWNER. Deny entries (1, 6, 10, 12), audit and alarm entries (2, 3, 7, 8, 13, 14, 15, 16)
/// and the mandatory label (17) grant nothing.
#[cfg_attr(not(windows), allow(dead_code))]
fn entry(ace_type: u8, flags: u8, mask: u32, who: Who, rights: u32, inherited_too: bool) -> Entry {
    if ace_type == ACCESS_DENIED || ace_type > 17 {
        return Entry::Fine;
    }
    if flags & INHERIT_ONLY != 0
        && (!inherited_too || flags & (OBJECT_INHERIT | CONTAINER_INHERIT) == 0 || who == Who::CreatorOwner)
    {
        return Entry::Fine;
    }
    if matches!(ace_type, 2 | 3 | 6 | 7 | 8 | 10 | 12 | 13 | 14 | 15 | 16 | 17) {
        return Entry::Fine;
    }
    if ace_type != ACCESS_ALLOWED {
        return Entry::Unknown;
    }
    if mask & rights != 0 && who != Who::Admin { Entry::Grants } else { Entry::Fine }
}

#[cfg(windows)]
mod win {
    use std::os::windows::ffi::OsStrExt;
    use std::path::Path;
    use windows::core::{PCWSTR, PWSTR};
    use windows::Win32::Foundation::{LocalFree, ERROR_SUCCESS, HLOCAL};
    use windows::Win32::Security::Authorization::{ConvertSidToStringSidW, GetNamedSecurityInfoW, SE_FILE_OBJECT};
    use windows::Win32::Security::{
        GetAce, IsWellKnownSid, WinBuiltinAdministratorsSid, WinCreatorOwnerSid, WinLocalSystemSid, ACCESS_ALLOWED_ACE, ACE_HEADER, ACL,
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
    /// Rights on an engine file or folder that let someone change what runs. On a folder,
    /// FILE_WRITE_DATA is FILE_ADD_FILE and FILE_APPEND_DATA is FILE_ADD_SUBDIRECTORY, so this also
    /// refuses a folder where others may create files.
    pub const ENGINE_RIGHTS: u32 = FILE_RIGHTS | FILE_DELETE_CHILD;

    /// NT SERVICE\TrustedInstaller, which Windows grants full control of %PROGRAMFILES% and the
    /// folders created in it. Only administrators can control that service, so it counts as one.
    const TRUSTED_INSTALLER: &str = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464";

    fn admin_sid(sid: PSID) -> bool {
        // SAFETY: sid points into a security descriptor that is still allocated.
        let well_known =
            unsafe { IsWellKnownSid(sid, WinLocalSystemSid).as_bool() || IsWellKnownSid(sid, WinBuiltinAdministratorsSid).as_bool() };
        well_known || sid_text(sid).as_deref() == Some(TRUSTED_INSTALLER)
    }

    fn sid_text(sid: PSID) -> Option<String> {
        let mut text = PWSTR::null();
        // SAFETY: sid is valid (see admin_sid); text is freed with LocalFree as the API requires,
        // after it is copied.
        unsafe {
            ConvertSidToStringSidW(sid, &mut text).ok()?;
            let copy = text.to_string().ok();
            let _ = LocalFree(Some(HLOCAL(text.0.cast())));
            copy
        }
    }

    /// Ok when the path is owned by SYSTEM or Administrators and its DACL grants `rights` to nobody
    /// else (super::entry decides each entry). With `inherited_too`, entries that only pass down to
    /// what is inside a folder count as well.
    pub fn check(path: &Path, rights: u32, inherited_too: bool) -> Result<(), String> {
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
                    // Only an ACCESS_ALLOWED entry is read past its header, as ACCESS_ALLOWED_ACE.
                    let (mask, who) = if header.AceType == super::ACCESS_ALLOWED {
                        let allowed = &*(ace as *const ACCESS_ALLOWED_ACE);
                        let sid = PSID(&allowed.SidStart as *const u32 as *mut _);
                        let who = if admin_sid(sid) {
                            super::Who::Admin
                        } else if IsWellKnownSid(sid, WinCreatorOwnerSid).as_bool() {
                            super::Who::CreatorOwner
                        } else {
                            super::Who::Other
                        };
                        (allowed.Mask, who)
                    } else {
                        (0, super::Who::Other)
                    };
                    match super::entry(header.AceType, header.AceFlags, mask, who, rights, inherited_too) {
                        super::Entry::Fine => {}
                        super::Entry::Unknown => return Err(format!("{} has a permission entry the app can't check", path.display())),
                        super::Entry::Grants => {
                            return Err(format!("someone other than SYSTEM and Administrators can change {}", path.display()))
                        }
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

    use super::{entry, Entry, Who};

    const MODIFY: u32 = 0x1_301BF; // Modify, as Windows writes it for a folder
    const FULL: u32 = 0x1F_01FF;
    const OI_CI_IO: u8 = 0x01 | 0x02 | 0x08;
    const ENGINE: u32 = 0x2 | 0x4 | 0x40 | 0x1_0000 | 0x4_0000 | 0x8_0000 | 0x1000_0000 | 0x4000_0000;

    #[test]
    fn an_inherit_only_modify_grant_for_users_is_refused_for_the_engine() {
        // Every file created in the folder would let Users change it.
        assert_eq!(entry(0, OI_CI_IO, MODIFY, Who::Other, ENGINE, true), Entry::Grants);
        assert_eq!(entry(0, OI_CI_IO, 0x4000_0000, Who::Other, ENGINE, true), Entry::Grants); // GENERIC_WRITE
        // The policy file's folder check keeps ignoring it: %ProgramData% has such grants.
        assert_eq!(entry(0, OI_CI_IO, MODIFY, Who::Other, ENGINE, false), Entry::Fine);
    }

    #[test]
    fn a_creator_owner_inherit_only_grant_is_accepted() {
        assert_eq!(entry(0, OI_CI_IO, FULL, Who::CreatorOwner, ENGINE, true), Entry::Fine);
        assert_eq!(entry(0, OI_CI_IO, FULL, Who::Admin, ENGINE, true), Entry::Fine);
        // Read and execute for Users, passed down, changes nothing.
        assert_eq!(entry(0, OI_CI_IO, 0x1_200A9, Who::Other, ENGINE, true), Entry::Fine);
        // Inherit-only with nothing to pass down to applies to nothing.
        assert_eq!(entry(0, 0x08, FULL, Who::Other, ENGINE, true), Entry::Fine);
    }

    #[test]
    fn deny_audit_and_unknown_entries() {
        assert_eq!(entry(1, 0, FULL, Who::Other, ENGINE, true), Entry::Fine);
        assert_eq!(entry(2, 0, FULL, Who::Other, ENGINE, true), Entry::Fine);
        assert_eq!(entry(5, 0, FULL, Who::Other, ENGINE, true), Entry::Unknown);
        assert_eq!(entry(0, 0, MODIFY, Who::Other, ENGINE, true), Entry::Grants);
        assert_eq!(entry(0, 0, MODIFY, Who::Admin, ENGINE, true), Entry::Fine);
    }

    #[test]
    fn an_engine_folder_the_user_can_write_is_not_trusted() {
        let dir = std::env::temp_dir().join(format!("kf-engine-trust-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let trusted = super::admin_only_engine(&dir);
        let _ = std::fs::remove_dir_all(&dir);
        assert!(trusted.is_err(), "{trusted:?}");
    }
}
