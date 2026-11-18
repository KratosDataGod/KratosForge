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

/// None when only SYSTEM and Administrators could have written HKLM\`key` (its owner and
/// permissions, not whether this process could open it for writing, so an elevated run is judged like
/// any other), its parent, and the list `subkeys` (which must exist); otherwise why not. The engine's
/// policy_trust.key_admin_only applies the same rule.
#[cfg(windows)]
pub fn key_admin_only(key: &str, subkeys: &[&str]) -> Result<(), String> {
    use windows::Win32::Security::Authorization::SE_REGISTRY_WOW64_64KEY;
    let parent = key.rsplit_once('\\').map(|(p, _)| p).unwrap_or(key);
    win::check_named(&format!("MACHINE\\{parent}"), SE_REGISTRY_WOW64_64KEY, win::PARENT_KEY_RIGHTS)?;
    win::check_named(&format!("MACHINE\\{key}"), SE_REGISTRY_WOW64_64KEY, win::KEY_RIGHTS)?;
    for sub in subkeys {
        win::check_named(&format!("MACHINE\\{key}\\{sub}"), SE_REGISTRY_WOW64_64KEY, win::KEY_RIGHTS)?;
    }
    Ok(())
}

#[cfg(not(windows))]
pub fn key_admin_only(_key: &str, _subkeys: &[&str]) -> Result<(), String> {
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
/// CREATOR OWNER. Deny entries (1, 6, 10, 12), audit and alarm entries (2, 3, 7, 8, 13, 14, 15, 16),
/// the mandatory label (17) and the other system entries (resource attribute, scoped policy, trust
/// label and access filter, 18 to 21) grant nothing. A type past 21 isn't one Windows defines, so it
/// can't be checked and counts against the path.
#[cfg_attr(not(windows), allow(dead_code))]
fn entry(ace_type: u8, flags: u8, mask: u32, who: Who, rights: u32, inherited_too: bool) -> Entry {
    if ace_type == ACCESS_DENIED {
        return Entry::Fine;
    }
    if ace_type > 21 {
        return Entry::Unknown;
    }
    if flags & INHERIT_ONLY != 0
        && (!inherited_too || flags & (OBJECT_INHERIT | CONTAINER_INHERIT) == 0 || who == Who::CreatorOwner)
    {
        return Entry::Fine;
    }
    if matches!(ace_type, 2 | 3 | 6 | 7 | 8 | 10 | 12..=21) {
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
    use windows::Win32::Security::Authorization::{ConvertSidToStringSidW, GetNamedSecurityInfoW, SE_FILE_OBJECT, SE_OBJECT_TYPE};
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
    const KEY_SET_VALUE: u32 = 0x2;
    const KEY_CREATE_SUB_KEY: u32 = 0x4;
    /// Rights that let someone change Group Policy's key: set or add values, add subkeys, delete it,
    /// or take over its permissions.
    pub const KEY_RIGHTS: u32 = KEY_SET_VALUE | KEY_CREATE_SUB_KEY | DELETE | WRITE_DAC | WRITE_OWNER | GENERIC_WRITE | GENERIC_ALL;
    /// On its parent: add the key, or take the parent over.
    pub const PARENT_KEY_RIGHTS: u32 = KEY_CREATE_SUB_KEY | WRITE_DAC | WRITE_OWNER | GENERIC_WRITE | GENERIC_ALL;

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
        check_wide(&name, &path.display().to_string(), SE_FILE_OBJECT, rights, inherited_too)
    }

    /// `check` for a named object of another kind, such as a registry key ("MACHINE\\...").
    pub fn check_named(name: &str, kind: SE_OBJECT_TYPE, rights: u32) -> Result<(), String> {
        let wide: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
        check_wide(&wide, name, kind, rights, false)
    }

    fn check_wide(name: &[u16], shown: &str, kind: SE_OBJECT_TYPE, rights: u32, inherited_too: bool) -> Result<(), String> {
        let mut owner = PSID::default();
        let mut dacl: *mut ACL = std::ptr::null_mut();
        let mut descriptor = PSECURITY_DESCRIPTOR::default();
        // SAFETY: name is NUL-terminated and outlives the call. owner and dacl point into
        // descriptor, which is only read before it is freed with LocalFree as the API requires.
        unsafe {
            let status = GetNamedSecurityInfoW(PCWSTR(name.as_ptr()), kind,
                                               OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                                               Some(&mut owner), None, Some(&mut dacl), None, &mut descriptor);
            if status != ERROR_SUCCESS {
                return Err(format!("its permissions can't be read ({})", status.0));
            }
            let result = (|| {
                if !admin_sid(owner) {
                    return Err(format!("{} isn't owned by SYSTEM or Administrators", shown));
                }
                if dacl.is_null() {
                    return Err(format!("{} has no permissions set, so anyone can change it", shown));
                }
                for index in 0..u32::from((*dacl).AceCount) {
                    let mut ace: *mut core::ffi::c_void = std::ptr::null_mut();
                    if GetAce(dacl, index, &mut ace).is_err() {
                        return Err(format!("{}'s permissions can't be read", shown));
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
                        super::Entry::Unknown => return Err(format!("{} has a permission entry the app can't check", shown)),
                        super::Entry::Grants => {
                            return Err(format!("someone other than SYSTEM and Administrators can change {}", shown))
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

    #[cfg(windows)]
    #[test]
    fn the_registry_view_is_the_64_bit_one_the_engine_names() {
        // The engine's policy_trust.py passes 13 for the same object type.
        assert_eq!(windows::Win32::Security::Authorization::SE_REGISTRY_WOW64_64KEY.0, 13);
    }

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
    fn entry_kinds_the_app_cant_read_count_against_the_path() {
        // Allowed entries with extra data (compound, object, callback, callback object) could grant
        // anything, so they are refused even for an administrator.
        for kind in [4u8, 5, 9, 11] {
            assert_eq!(entry(kind, 0, FULL, Who::Other, ENGINE, true), Entry::Unknown, "{kind}");
            assert_eq!(entry(kind, 0, FULL, Who::Admin, ENGINE, false), Entry::Unknown, "{kind}");
        }
        // Deny, audit, alarm, label and the other system entries grant nothing.
        for kind in [1u8, 2, 3, 6, 7, 8, 10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21] {
            assert_eq!(entry(kind, 0, FULL, Who::Other, ENGINE, true), Entry::Fine, "{kind}");
        }
        // A type Windows doesn't define can't be checked, even inherit-only.
        for kind in [22u8, 255] {
            assert_eq!(entry(kind, 0, FULL, Who::Admin, ENGINE, false), Entry::Unknown, "{kind}");
            assert_eq!(entry(kind, OI_CI_IO, FULL, Who::Admin, ENGINE, false), Entry::Unknown, "{kind}");
        }
    }

    #[test]
    fn each_right_that_could_change_the_file_is_refused_for_anyone_else() {
        for right in [0x2u32, 0x4, 0x40, 0x1_0000, 0x4_0000, 0x8_0000, 0x1000_0000, 0x4000_0000] {
            assert_eq!(entry(0, 0, right, Who::Other, ENGINE, false), Entry::Grants, "{right:#x}");
            // CREATOR OWNER is only exempt on an entry that passes down; on the path itself it is someone else.
            assert_eq!(entry(0, 0, right, Who::CreatorOwner, ENGINE, false), Entry::Grants, "{right:#x}");
            assert_eq!(entry(0, 0, right, Who::Admin, ENGINE, false), Entry::Fine, "{right:#x}");
        }
        // Read, execute and synchronize change nothing.
        assert_eq!(entry(0, 0, 0x1_200A9, Who::Other, ENGINE, false), Entry::Fine);
        // A right outside the set asked about doesn't count: the policy folder lets everyone add files.
        let folder_rights = 0x40 | 0x1_0000 | 0x4_0000 | 0x8_0000 | 0x1000_0000;
        assert_eq!(entry(0, 0, 0x2 | 0x4, Who::Other, folder_rights, false), Entry::Fine);
        assert_eq!(entry(0, 0, 0x40, Who::Other, folder_rights, false), Entry::Grants);
    }

    #[test]
    fn an_inherited_grant_that_passes_down_to_files_or_folders_only_still_counts() {
        // Object-inherit or container-inherit alone, inherit-only: still reaches what is inside.
        assert_eq!(entry(0, 0x01 | 0x08, MODIFY, Who::Other, ENGINE, true), Entry::Grants);
        assert_eq!(entry(0, 0x02 | 0x08, MODIFY, Who::Other, ENGINE, true), Entry::Grants);
        // An inherited (not inherit-only) entry applies to the path itself, asked about or not.
        assert_eq!(entry(0, 0x01 | 0x02 | 0x10, MODIFY, Who::Other, ENGINE, false), Entry::Grants);
    }

    #[test]
    fn a_policy_file_without_a_folder_or_off_windows_is_not_trusted() {
        assert!(admin_only(std::path::Path::new("")).is_err());
        #[cfg(not(windows))]
        {
            let why = admin_only(&std::env::temp_dir().join("policy.json")).unwrap_err();
            assert!(why.contains("only trusted on Windows"), "{why}");
            assert!(super::admin_only_engine(&std::env::temp_dir()).is_err());
            assert!(super::admin_only_ancestor(&std::env::temp_dir()).is_err());
        }
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

/// The real permission reads, on real files and folders whose owner and permissions each test sets.
/// Setting an owner of Administrators needs an elevated account, as the Windows CI runner has; run
/// elsewhere unelevated, these tests say so and stop, but on CI that is a failure.
#[cfg(all(test, windows))]
pub(crate) mod windows_tests {
    use super::{admin_only, admin_only_ancestor, admin_only_engine};
    use std::path::{Path, PathBuf};
    use windows::core::HSTRING;
    use windows::Win32::Foundation::{LocalFree, HLOCAL};
    use windows::Win32::Security::Authorization::{ConvertStringSecurityDescriptorToSecurityDescriptorW, SDDL_REVISION_1};
    use windows::Win32::Security::{SetFileSecurityW, DACL_SECURITY_INFORMATION, OWNER_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR};

    const ADMINS_ONLY: &str = "(A;;FA;;;SY)(A;;FA;;;BA)";
    const TRUSTED_INSTALLER: &str = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464";

    /// Sets `path`'s owner and DACL from SDDL. Err(code) when Windows refuses (1307, "this security
    /// ID may not be assigned as the owner", when the account isn't elevated).
    pub(crate) fn set(path: &Path, sddl: &str) -> Result<(), u32> {
        let mut sd = PSECURITY_DESCRIPTOR::default();
        unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(&HSTRING::from(sddl), SDDL_REVISION_1, &mut sd, None)
                .unwrap_or_else(|e| panic!("{sddl}: {e}"));
            let ok = SetFileSecurityW(&HSTRING::from(path.as_os_str()), OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION, sd);
            let code = windows::Win32::Foundation::GetLastError().0;
            let _ = LocalFree(Some(HLOCAL(sd.0)));
            if ok.as_bool() { Ok(()) } else { Err(code) }
        }
    }

    /// The account running the tests, as a SID string, from whoami.
    pub(crate) fn me() -> String {
        let root = std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into());
        let whoami = PathBuf::from(root).join("System32").join("whoami.exe");
        let out = std::process::Command::new(whoami).args(["/user", "/fo", "csv", "/nh"]).output().unwrap();
        let text = String::from_utf8_lossy(&out.stdout);
        let sid = text.trim().rsplit(',').next().unwrap().trim_matches('"').to_string();
        assert!(sid.starts_with("S-1-5-"), "{text}");
        sid
    }

    /// A fresh folder with a policy.json in it, removed on drop.
    struct Place {
        dir: PathBuf,
        file: PathBuf,
    }

    impl Place {
        fn new(name: &str) -> Place {
            let dir = std::env::temp_dir().join(format!("kf-trust-{name}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&dir);
            std::fs::create_dir_all(&dir).unwrap();
            let file = dir.join("policy.json");
            std::fs::write(&file, "{}").unwrap();
            Place { dir, file }
        }

        /// Owner Administrators and the given DACLs on the folder and the file. False when the
        /// account can't do that (not elevated) and this isn't CI.
        fn lock(&self, folder_dacl: &str, file_dacl: &str) -> bool {
            for (path, dacl) in [(&self.file, file_dacl), (&self.dir, folder_dacl)] {
                match set(path, &format!("O:BAD:P{dacl}")) {
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
    }

    impl Drop for Place {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.dir);
        }
    }

    #[test]
    fn a_file_and_folder_only_admins_can_change_are_trusted() {
        let place = Place::new("admins");
        if !place.lock(ADMINS_ONLY, ADMINS_ONLY) {
            return;
        }
        assert_eq!(admin_only(&place.file), Ok(()));
        // Reading, and a deny (for Guests, so cleanup can still delete it), change nothing; nor does TrustedInstaller's full control.
        assert!(place.lock(&format!("{ADMINS_ONLY}(A;;FRFX;;;BU)(A;;FA;;;{TRUSTED_INSTALLER})"),
                           &format!("{ADMINS_ONLY}(A;;FR;;;BU)(D;;FA;;;BG)")));
        assert_eq!(admin_only(&place.file), Ok(()));
    }

    #[test]
    fn a_file_others_can_write_is_not_trusted() {
        let place = Place::new("users-write");
        if !place.lock(ADMINS_ONLY, &format!("{ADMINS_ONLY}(A;;FW;;;BU)")) {
            return;
        }
        let why = admin_only(&place.file).unwrap_err();
        assert!(why.starts_with("someone other than SYSTEM and Administrators can change"), "{why}");
        assert!(why.contains("policy.json"), "{why}");
    }

    #[test]
    fn a_folder_others_can_delete_from_is_not_trusted() {
        let place = Place::new("users-delete");
        // Delete child: anyone could swap the file for their own.
        if !place.lock(&format!("{ADMINS_ONLY}(A;;0x40;;;AU)"), ADMINS_ONLY) {
            return;
        }
        let why = admin_only(&place.file).unwrap_err();
        assert!(why.starts_with("someone other than SYSTEM and Administrators can change"), "{why}");
        assert!(!why.contains("policy.json"), "the folder, not the file, is named: {why}");
        // Creating files in the folder is allowed for the policy file, as %ProgramData% allows it.
        assert!(place.lock(&format!("{ADMINS_ONLY}(A;;0x6;;;AU)"), ADMINS_ONLY));
        assert_eq!(admin_only(&place.file), Ok(()));
    }

    #[test]
    fn a_file_owned_by_someone_else_is_not_trusted() {
        let place = Place::new("owner");
        if !place.lock(ADMINS_ONLY, ADMINS_ONLY) {
            return;
        }
        // The account itself (an administrator, but not the Administrators group) owns it.
        set(&place.file, &format!("O:{}D:P{ADMINS_ONLY}", me())).unwrap();
        let why = admin_only(&place.file).unwrap_err();
        assert!(why.ends_with("isn't owned by SYSTEM or Administrators"), "{why}");
    }

    #[test]
    fn a_file_with_no_permissions_set_is_not_trusted() {
        let place = Place::new("null-dacl");
        if !place.lock(ADMINS_ONLY, ADMINS_ONLY) {
            return;
        }
        set(&place.file, "O:BAD:NO_ACCESS_CONTROL").unwrap();
        let why = admin_only(&place.file).unwrap_err();
        assert!(why.ends_with("has no permissions set, so anyone can change it"), "{why}");
    }

    #[test]
    fn a_missing_file_is_not_trusted() {
        let place = Place::new("missing");
        // The folder must pass first, or its own check answers instead.
        if !place.lock(ADMINS_ONLY, ADMINS_ONLY) {
            return;
        }
        let why = admin_only(&place.dir.join("nothing.json")).unwrap_err();
        assert_eq!(why, "its permissions can't be read (2)");
    }

    #[test]
    fn an_engine_folder_refuses_what_it_would_pass_down_and_an_ancestor_does_not() {
        let place = Place::new("engine");
        // Users may write every file later created inside, but not the folder itself.
        if !place.lock(&format!("{ADMINS_ONLY}(A;OICIIO;FW;;;BU)"), ADMINS_ONLY) {
            return;
        }
        let why = admin_only_engine(&place.dir).unwrap_err();
        assert!(why.starts_with("someone other than SYSTEM and Administrators can change"), "{why}");
        assert_eq!(admin_only_ancestor(&place.dir), Ok(()));
        // CREATOR OWNER's full control passed down is fine: only administrators create things there.
        assert!(place.lock(&format!("{ADMINS_ONLY}(A;OICIIO;FA;;;CO)(A;OICI;FRFX;;;BU)"), ADMINS_ONLY));
        assert_eq!(admin_only_engine(&place.dir), Ok(()));
        // Adding files to an engine folder is refused, though the policy folder allows it.
        assert!(place.lock(&format!("{ADMINS_ONLY}(A;;0x2;;;BU)"), ADMINS_ONLY));
        assert!(admin_only_engine(&place.dir).is_err());
        assert_eq!(admin_only_ancestor(&place.dir), Ok(()));
    }
}
