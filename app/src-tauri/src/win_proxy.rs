//! The proxy Windows is set to (Internet Options), for the engine download. The shell downloads
//! the engine with Windows' own curl.exe, which reads `HTTPS_PROXY` but not the Windows proxy
//! setting, so on a network where that setting is the only way out a new PC couldn't fetch its
//! engine. When no proxy variable is set, `curl_args` gives curl the Windows proxy for the
//! download's host (unless the bypass list names it), as Edge and the engine's own HTTPS use it.
//!
//! Only a fixed proxy server is read: a PAC file or automatic detection (WPAD) isn't, the same as
//! the engine. A proxy entry with a user name or password in it is refused, so no credential is
//! ever put on curl's command line or in a log. TLS verification is unchanged: curl still checks
//! the server's certificate against the Windows store, through the proxy's tunnel.

/// Variables curl reads for an https download. With any of them set, curl decides itself.
const CURL_PROXY_VARS: [&str; 4] = ["HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"];

/// curl's `--proxy` arguments for downloading `url`, or none.
pub fn curl_args(url: &str) -> Vec<String> {
    if CURL_PROXY_VARS.iter().any(|v| std::env::var_os(v).is_some_and(|x| !x.is_empty())) {
        return Vec::new();
    }
    let Some((proxy, bypass)) = windows_setting() else { return Vec::new() };
    args_for(url, &proxy, &bypass)
}

/// The arguments for `url`, given Windows' proxy list and bypass list (the pure part, tested).
pub fn args_for(url: &str, proxy_list: &str, bypass_list: &str) -> Vec<String> {
    let Some(host) = host_of(url) else { return Vec::new() };
    match https_proxy(proxy_list) {
        Some(proxy) if !bypassed(&host, bypass_list) => vec!["--proxy".into(), proxy],
        _ => Vec::new(),
    }
}

/// The proxy for https from Windows' list: `host:port` for every protocol, or the `https=` entry
/// of `http=a:80;https=b:8080`. As `http://host:port`; None when there is none or it isn't a plain
/// host and port (a user name, a space, a path).
pub fn https_proxy(list: &str) -> Option<String> {
    let entries: Vec<&str> = list.split([';', ' ']).map(str::trim).filter(|e| !e.is_empty()).collect();
    let pick = entries
        .iter()
        .find_map(|e| e.strip_prefix("https=").or_else(|| e.strip_prefix("HTTPS=")))
        .or_else(|| entries.iter().copied().find(|e| !e.contains('=')))?;
    let bare = pick.strip_prefix("http://").unwrap_or(pick).trim_end_matches('/');
    let (host, port) = bare.rsplit_once(':').unwrap_or((bare, "80"));
    let host_ok = !host.is_empty()
        && host.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-' || b == b'_');
    let port_ok = !port.is_empty() && port.len() <= 5 && port.bytes().all(|b| b.is_ascii_digit());
    (host_ok && port_ok).then(|| format!("http://{host}:{port}"))
}

/// Whether Windows' bypass list (`*.contoso.com;10.*;<local>`) names `host`. `<local>` is any
/// host name without a dot; `*` matches any run of characters.
pub fn bypassed(host: &str, list: &str) -> bool {
    let host = host.to_ascii_lowercase();
    list.split([';', ' ', ',']).map(str::trim).filter(|e| !e.is_empty()).any(|entry| {
        let entry = entry.to_ascii_lowercase();
        if entry == "<local>" {
            return !host.contains('.');
        }
        glob(entry.as_bytes(), host.as_bytes())
    })
}

fn glob(pattern: &[u8], text: &[u8]) -> bool {
    match pattern.split_first() {
        None => text.is_empty(),
        Some((b'*', rest)) => (0..=text.len()).any(|i| glob(rest, &text[i..])),
        Some((c, rest)) => text.first() == Some(c) && glob(rest, &text[1..]),
    }
}

fn host_of(url: &str) -> Option<String> {
    let rest = url.strip_prefix("https://")?;
    let authority = rest.split(['/', '?', '#']).next()?;
    let host = authority.rsplit_once('@').map_or(authority, |(_, h)| h);
    let host = host.split(':').next()?;
    (!host.is_empty()).then(|| host.to_ascii_lowercase())
}

/// (proxy list, bypass list) from the signed-in user's Internet Options, when a fixed proxy is set.
#[cfg(windows)]
fn windows_setting() -> Option<(String, String)> {
    use windows::Win32::Foundation::{GlobalFree, HGLOBAL};
    use windows::Win32::Networking::WinHttp::{WinHttpGetIEProxyConfigForCurrentUser, WINHTTP_CURRENT_USER_IE_PROXY_CONFIG};
    let mut config = WINHTTP_CURRENT_USER_IE_PROXY_CONFIG::default();
    unsafe { WinHttpGetIEProxyConfigForCurrentUser(&mut config) }.ok()?;
    let take = |p: windows::core::PWSTR| -> String {
        if p.is_null() {
            return String::new();
        }
        let text = unsafe { p.to_string() }.unwrap_or_default();
        let _ = unsafe { GlobalFree(Some(HGLOBAL(p.0 as _))) };
        text
    };
    let _ = take(config.lpszAutoConfigUrl);
    let proxy = take(config.lpszProxy);
    let bypass = take(config.lpszProxyBypass);
    (!proxy.is_empty()).then_some((proxy, bypass))
}

#[cfg(not(windows))]
fn windows_setting() -> Option<(String, String)> {
    None
}

#[cfg(test)]
mod tests {
    use super::{args_for, bypassed, https_proxy};

    #[test]
    fn the_https_entry_or_the_one_for_every_protocol_is_used() {
        assert_eq!(https_proxy("proxy.contoso.com:8080").as_deref(), Some("http://proxy.contoso.com:8080"));
        assert_eq!(https_proxy("http=a.contoso.com:80;https=b.contoso.com:8443").as_deref(), Some("http://b.contoso.com:8443"));
        assert_eq!(https_proxy("http://proxy:3128/").as_deref(), Some("http://proxy:3128"));
        assert_eq!(https_proxy("proxy").as_deref(), Some("http://proxy:80"));
        assert_eq!(https_proxy("http=a:80;ftp=b:21"), None);
        assert_eq!(https_proxy(""), None);
    }

    #[test]
    fn a_proxy_with_a_user_name_or_anything_odd_is_refused() {
        for bad in ["user:pass@proxy:8080", "https=user@proxy:8080", "proxy:80/path", "proxy:port", "proxy:123456", "[::1]:8080"] {
            assert_eq!(https_proxy(bad), None, "{bad}");
        }
    }

    #[test]
    fn the_bypass_list_is_honoured() {
        assert!(bypassed("files.contoso.com", "*.contoso.com;<local>"));
        assert!(bypassed("intranet", "<local>"));
        assert!(bypassed("GitHub.com", "github.com"));
        assert!(bypassed("10.1.2.3", "10.*"));
        assert!(!bypassed("github.com", "*.contoso.com;<local>"));
        assert!(!bypassed("github.com", ""));
    }

    #[test]
    fn curl_gets_the_proxy_unless_the_host_is_bypassed() {
        let url = "https://github.com/KratosDataGod/KratosForge/releases/download/latest/fabric-desktop-engine.zip?nocache=1";
        assert_eq!(args_for(url, "proxy.contoso.com:8080", "<local>"), ["--proxy", "http://proxy.contoso.com:8080"]);
        assert!(args_for("https://files.contoso.com/kf/version.json", "proxy:8080", "*.contoso.com").is_empty());
        assert!(args_for(url, "", "").is_empty());
        assert!(args_for("http://github.com/x", "proxy:8080", "").is_empty()); // only https downloads
    }
}
