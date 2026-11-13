// Open in SSMS on the Warehouse page (docs/warehouse.md, "Open in SSMS"). The shell starts SQL Server
// Management Studio on a warehouse: `ssms.exe [query.sql] -S <server> -d <database> -A
// ActiveDirectoryInteractive [-U <user>] -nosplash`, so SSMS signs in on its own with Microsoft
// Entra. The app passes it no password or token. Every value is checked here before it goes on the
// command line: the server must be a Fabric SQL endpoint, and nothing may start with "-", so a
// value can't become another SSMS switch, and SSMS itself must be signed by Microsoft. The same values make VS Code's MSSQL extension link.
// Only std, so it is tested without a window or Windows.

use std::path::{Path, PathBuf};

/// The host every Fabric warehouse and SQL analytics endpoint has (warehouse.py HOST_SUFFIX).
pub const HOST_SUFFIX: &str = ".datawarehouse.fabric.microsoft.com";

/// The most query text handed to SSMS in a file.
pub const QUERY_MAX: usize = 1 << 20;

/// A Fabric SQL endpoint's host name: letters, digits, "-" and ".", ending in HOST_SUFFIX.
pub fn valid_server(s: &str) -> bool {
    let lower = s.to_ascii_lowercase();
    s.len() <= 253 && lower.len() > HOST_SUFFIX.len() && lower.ends_with(HOST_SUFFIX) && !s.starts_with(['-', '.'])
        && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'.')
}

/// A warehouse's name as Fabric shows it: letters, digits, spaces, "_", "-" and ".", at most 256,
/// not starting with "-" or a space.
pub fn valid_database(s: &str) -> bool {
    !s.is_empty() && s.chars().count() <= 256 && !s.starts_with(['-', ' '])
        && s.chars().all(|c| c.is_alphanumeric() || " _-.".contains(c))
}

/// A sign-in name for SSMS's sign-in box: empty (SSMS asks), or one name@domain without spaces,
/// quotes or anything that could start a switch.
pub fn valid_user(s: &str) -> bool {
    s.is_empty()
        || (s.len() <= 256 && !s.starts_with('-') && s.matches('@').count() == 1 && !s.starts_with('@') && !s.ends_with('@')
            && s.bytes().all(|b| b.is_ascii_alphanumeric() || b"._%+-@'".contains(&b)))
}

/// SSMS's arguments: the query file first when there is one, then the connection.
pub fn args(server: &str, database: &str, user: &str, file: Option<&Path>) -> Result<Vec<String>, String> {
    if !valid_server(server) {
        return Err(format!("{server} isn't a Fabric SQL endpoint, so SSMS wasn't opened."));
    }
    if !valid_database(database) {
        return Err("That warehouse name can't be passed to SSMS.".into());
    }
    if !valid_user(user) {
        return Err("That sign-in name can't be passed to SSMS.".into());
    }
    let mut out = Vec::new();
    if let Some(f) = file {
        let text = f.to_str().ok_or("The query file's path isn't valid text.")?;
        if !f.is_absolute() || text.starts_with('-') {
            return Err("The query file needs a full path.".into());
        }
        out.push(text.to_string());
    }
    out.extend(["-S", server, "-d", database, "-A", "ActiveDirectoryInteractive"].map(String::from));
    if !user.is_empty() {
        out.extend(["-U".to_string(), user.to_string()]);
    }
    out.push("-nosplash".into());
    Ok(out)
}

/// Where SSMS 22 and 21 install by default, newest first, under Program Files.
pub fn candidates(program_files: &Path) -> Vec<PathBuf> {
    ["22", "21"].iter()
        .map(|v| program_files.join(format!("Microsoft SQL Server Management Studio {v}"))
            .join("Release").join("Common7").join("IDE").join("SSMS.exe"))
        .collect()
}

/// Whether `path` names an ssms.exe the person chose: a full path whose file is named ssms.exe.
/// (The shell also checks it is on a drive of this PC and exists.)
pub fn named_ssms(path: &Path) -> bool {
    path.is_absolute() && path.file_name().and_then(|n| n.to_str()).is_some_and(|n| n.eq_ignore_ascii_case("ssms.exe"))
}

/// Whether SSMS's signer, as signature::signer reads it, is Microsoft: a signature Windows trusts
/// whose certificate subject names Microsoft Corporation as both CN and O. An unsigned file, one
/// Windows doesn't trust, or another publisher's isn't started, so a file merely named ssms.exe
/// (one dropped in Downloads) never is.
pub fn microsoft_signed(signer: &Result<Option<String>, String>) -> Result<(), String> {
    const MS: &str = "Microsoft Corporation";
    match signer {
        Ok(Some(subject)) => {
            let parts: Vec<&str> = subject.split(", ").map(str::trim).collect();
            let has = |key: &str| parts.iter().any(|p| p.strip_prefix(key) == Some(MS));
            if !subject.contains('"') && has("CN=") && has("O=") {
                Ok(())
            } else {
                Err("That ssms.exe isn't signed by Microsoft, so KratosForge didn't start it. Choose the folder of SSMS from Microsoft.".into())
            }
        }
        Ok(None) => Err("That ssms.exe isn't signed, so KratosForge didn't start it. Choose the folder of SSMS from Microsoft.".into()),
        Err(why) => Err(format!("Windows doesn't trust that ssms.exe's signature ({why}), so KratosForge didn't start it.")),
    }
}

/// A name for the query file: the warehouse's name with anything but letters, digits, "-" and "_"
/// as "_", and a stamp, so two opens never share a file.
pub fn query_file_name(database: &str, stamp: u128) -> String {
    let safe: String = database.chars().take(64)
        .map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' { c } else { '_' }).collect();
    format!("{safe}-{stamp}.sql")
}

/// encodeURIComponent: everything but A-Z a-z 0-9 - _ . ! ~ * ' ( ) as UTF-8 %XX.
fn encode_component(s: &str) -> String {
    let mut out = String::with_capacity(s.len() * 3);
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || b"-_.!~*'()".contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

/// The link that opens VS Code's MSSQL extension connected to the warehouse with Microsoft Entra.
pub fn mssql_link(server: &str, database: &str, user: &str) -> Result<String, String> {
    if !valid_server(server) || !valid_database(database) || !valid_user(user) {
        return Err("That warehouse can't be opened in VS Code.".into());
    }
    let mut link = format!("vscode://ms-mssql.mssql/connect?server={}&database={}&authenticationType=AzureMFA",
                           encode_component(server), encode_component(database));
    if !user.is_empty() {
        link.push_str(&format!("&email={}", encode_component(user)));
    }
    Ok(link)
}

#[cfg(test)]
mod tests {
    use super::*;

    const SERVER: &str = "x6eps4xrq2xudenlfv6naeo3i4-abc123.datawarehouse.fabric.microsoft.com";

    #[test]
    fn only_a_fabric_sql_endpoint_is_a_server() {
        assert!(valid_server(SERVER) && valid_server(&SERVER.to_ascii_uppercase()));
        for bad in ["", HOST_SUFFIX, "evil.com", "a.datawarehouse.fabric.microsoft.com.evil.com",
                    "-S.datawarehouse.fabric.microsoft.com", "a b.datawarehouse.fabric.microsoft.com",
                    "a;b.datawarehouse.fabric.microsoft.com", "a\".datawarehouse.fabric.microsoft.com",
                    ".a.datawarehouse.fabric.microsoft.com"] {
            assert!(!valid_server(bad), "{bad}");
        }
    }

    #[test]
    fn a_name_or_user_cant_become_a_switch() {
        assert!(valid_database("Sales WH") && valid_database("sales_wh-2.v1") && valid_database("Ventes_été"));
        for bad in ["", "-nosplash", " lead", "a\"b", "a\nb", "a/b", "a\\b", &"a".repeat(257)] {
            assert!(!valid_database(bad), "{bad:?}");
        }
        assert!(valid_user("") && valid_user("chris@contoso.com") && valid_user("o'neil+x@contoso.co.uk"));
        for bad in ["-U@x.com", "a b@x.com", "nobody", "a@b@c", "@x.com", "x@", "a\"@x.com"] {
            assert!(!valid_user(bad), "{bad}");
        }
    }

    #[test]
    fn ssms_gets_the_file_then_the_connection_with_entra_sign_in() {
        let file = if cfg!(windows) { PathBuf::from(r"C:\Users\c\AppData\Local\FabricDesktop\ssms\q.sql") } else { PathBuf::from("/tmp/q.sql") };
        let a = args(SERVER, "Sales WH", "chris@contoso.com", Some(&file)).unwrap();
        assert_eq!(a, vec![file.to_str().unwrap(), "-S", SERVER, "-d", "Sales WH", "-A", "ActiveDirectoryInteractive",
                           "-U", "chris@contoso.com", "-nosplash"]);
        assert_eq!(args(SERVER, "wh", "", None).unwrap(),
                   vec!["-S", SERVER, "-d", "wh", "-A", "ActiveDirectoryInteractive", "-nosplash"]);
        assert!(args("evil.com", "wh", "", None).is_err());
        assert!(args(SERVER, "-E", "", None).is_err());
        assert!(args(SERVER, "wh", "-P", None).is_err());
        assert!(args(SERVER, "wh", "", Some(Path::new("q.sql"))).is_err());
    }

    #[test]
    fn ssms_22_is_looked_for_before_21() {
        let found = candidates(Path::new("/pf"));
        assert_eq!(found[0], Path::new("/pf/Microsoft SQL Server Management Studio 22/Release/Common7/IDE/SSMS.exe"));
        assert!(found[1].to_str().unwrap().contains("Management Studio 21"));
    }

    #[test]
    fn a_chosen_program_must_be_named_ssms_exe() {
        let root = if cfg!(windows) { r"C:\Tools\" } else { "/opt/tools/" };
        assert!(named_ssms(&PathBuf::from(format!("{root}Ssms.exe"))));
        assert!(!named_ssms(&PathBuf::from(format!("{root}cmd.exe"))));
        assert!(!named_ssms(Path::new("ssms.exe")));
        assert!(!named_ssms(&PathBuf::from(format!("{root}ssms.exe.bat"))));
    }

    #[test]
    fn only_ssms_signed_by_microsoft_is_started() {
        let ms = "CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US";
        assert_eq!(microsoft_signed(&Ok(Some(ms.into()))), Ok(()));
        assert!(microsoft_signed(&Ok(None)).unwrap_err().contains("isn't signed"));
        assert!(microsoft_signed(&Err("0x800B0109".into())).unwrap_err().contains("doesn't trust"));
        for other in ["CN=Contoso, O=Contoso, C=US", "CN=Microsoft Corporation, O=Contoso, C=US",
                      "CN=Contoso, O=Microsoft Corporation, C=US", "CN=\"x, CN=Microsoft Corporation\", O=\"y, O=Microsoft Corporation\"",
                      "CN=Microsoft Corporation Evil, O=Microsoft Corporation Evil", ""] {
            assert!(microsoft_signed(&Ok(Some(other.into()))).unwrap_err().contains("isn't signed by Microsoft"), "{other}");
        }
    }

    #[test]
    fn the_query_file_is_named_after_the_warehouse_without_odd_characters() {
        assert_eq!(query_file_name("Sales WH/..\\x", 7), "Sales_WH____x-7.sql");
    }

    #[test]
    fn the_mssql_link_carries_the_connection_encoded() {
        assert_eq!(mssql_link(SERVER, "Sales WH", "chris@contoso.com").unwrap(),
                   format!("vscode://ms-mssql.mssql/connect?server={SERVER}&database=Sales%20WH&authenticationType=AzureMFA&email=chris%40contoso.com"));
        assert!(!mssql_link(SERVER, "wh", "").unwrap().contains("email"));
        assert!(mssql_link("evil.com", "wh", "").is_err());
    }
}
