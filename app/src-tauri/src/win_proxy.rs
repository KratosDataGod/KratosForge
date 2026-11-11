//! The proxy Windows is set to, for the engine download and for the engine itself.
//!
//! The shell downloads the engine with Windows' own curl.exe, which reads `HTTPS_PROXY` but not the
//! Windows proxy setting. The engine's Python (requests, pip, the updater) reads only the signed-in
//! user's own fixed proxy from the registry (`HKCU`), and delta-rs and gRPC read only `HTTPS_PROXY`.
//! None of them runs a proxy auto-configuration (PAC) file or automatic detection (WPAD), and none
//! sees a proxy IT set for the whole machine ("ProxySettingsPerUser" off), which Edge and Windows use.
//!
//! So, when no proxy variable is set, the shell asks Windows (WinHTTP, the same answer Edge gets)
//! which proxy it uses for the hosts the app talks to, and:
//! - gives curl the proxy for the download's own address (`curl_args`);
//! - starts the engine, and so everything it starts, with `HTTPS_PROXY` and `NO_PROXY` set to that
//!   answer (`engine_env`), when Windows uses PAC or WPAD, or a fixed proxy the engine can't see.
//!   A fixed proxy the engine already reads is left to it, as before.
//!
//! A proxy entry with a user name or password in it is refused, so no credential is ever put on
//! curl's command line, in a variable or in a log. TLS verification is unchanged.

use std::collections::BTreeMap;

/// Variables curl reads for an https download. With any of them set, curl decides itself.
const CURL_PROXY_VARS: [&str; 4] = ["HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"];

/// curl's `--proxy` arguments for downloading `url`, or none.
pub fn curl_args(url: &str) -> Vec<String> {
    if CURL_PROXY_VARS.iter().any(|v| std::env::var_os(v).is_some_and(|x| !x.is_empty())) {
        return Vec::new();
    }
    let Some(windows) = windows_config() else { return Vec::new() };
    if windows.automatic() {
        if let Some(host) = host_of(url) {
            match routes(&windows, &[host]).pop() {
                Some(Route::Proxy(list)) => return https_proxy(&list).map(|p| vec!["--proxy".into(), p]).unwrap_or_default(),
                Some(Route::Direct) => return Vec::new(),
                _ => {} // Windows couldn't run its PAC file or detection: its fixed proxy, if any, as before
            }
        }
    }
    if windows.proxy.is_empty() {
        return Vec::new();
    }
    args_for(url, &windows.proxy, &windows.bypass)
}

/// Windows' proxy settings for the signed-in user, as WinHTTP reads them (the machine's when IT set
/// one for every user).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct WindowsProxy {
    /// "Automatically detect settings" (WPAD).
    pub auto_detect: bool,
    /// "Use setup script": the PAC file's address.
    pub pac_url: String,
    /// The fixed proxy list and its bypass list.
    pub proxy: String,
    pub bypass: String,
    /// Whether the engine's Python reads that fixed proxy itself: it reads only the user's own
    /// setting (`HKCU ... Internet Settings`, `ProxyEnable` = 1).
    pub python_sees_fixed: bool,
}

impl WindowsProxy {
    /// Whether Windows works the proxy out per address (PAC or WPAD), which it does before any fixed proxy.
    pub fn automatic(&self) -> bool {
        self.auto_detect || !self.pac_url.is_empty()
    }
}

/// Windows' answer for one address.
#[derive(Clone, Debug, PartialEq)]
pub enum Route {
    Direct,
    /// The proxy list, as WinHTTP gives it (`proxy:8080;backup:8080`).
    Proxy(String),
    /// Windows couldn't say (no WPAD server, a PAC file it couldn't fetch or run): why.
    Unknown(String),
}

/// The hosts whose route sets the engine's proxy: what most of its calls go to (Network access in
/// docs/enterprise-install.md).
pub const PROBE_HOSTS: [&str; 8] = [
    "login.microsoftonline.com",
    "api.fabric.microsoft.com",
    "onelake.dfs.fabric.microsoft.com",
    "api.powerbi.com",
    "graph.microsoft.com",
    "github.com",
    "pypi.org",
    "files.pythonhosted.org",
];

/// Never sent to a proxy: this PC, and an Azure VM's identity endpoint (169.254.169.254).
pub const ALWAYS_DIRECT: &str = "localhost,127.0.0.1,::1,169.254.169.254";

/// Every variable a client reads for its proxy. With any set, the person or IT chose, so nothing is added.
const PROXY_VARS: [&str; 6] = ["HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"];

/// `HTTPS_PROXY` and `NO_PROXY` for the engine, worked out once per run of the app (WPAD can take
/// seconds, so `warm_up` starts it as the app starts). Empty when a proxy variable is set, or the
/// engine reads Windows' proxy itself.
pub fn engine_env() -> &'static [(String, String)] {
    static ENV: std::sync::OnceLock<Vec<(String, String)>> = std::sync::OnceLock::new();
    ENV.get_or_init(|| {
        if PROXY_VARS.iter().any(|v| std::env::var_os(v).is_some_and(|x| !x.is_empty())) {
            return Vec::new();
        }
        let Some(windows) = windows_config() else { return Vec::new() };
        let hosts: Vec<String> = PROBE_HOSTS.iter().map(|h| h.to_string()).collect();
        let found = if windows.automatic() { routes(&windows, &hosts) } else { Vec::new() };
        // NO_PROXY is one variable on Windows, whatever its case.
        let existing = std::env::var("NO_PROXY").unwrap_or_default();
        let env = plan(&windows, &hosts.into_iter().zip(found).collect::<Vec<_>>(), &existing);
        if !env.is_empty() {
            eprintln!("proxy: the engine uses Windows' proxy settings: {env:?}");
        }
        env
    })
}

/// Starts `engine_env` on a thread of its own, so the engine's first start doesn't wait for WPAD.
pub fn warm_up() {
    let _ = std::thread::Builder::new().name("proxy".into()).spawn(|| {
        engine_env();
    });
}

/// The engine's proxy variables from Windows' settings and its answer for each probed host (the pure
/// part, tested). With PAC or WPAD: the proxy most hosts get, and the hosts it sends direct in
/// `NO_PROXY`. Otherwise, or when Windows couldn't answer for any host: its fixed proxy, but only
/// when the engine can't read that itself. The bypass list goes to `NO_PROXY` as far as it can be
/// written there. A `NO_PROXY` IT or the person already set (`existing`) is kept and added to;
/// `NO_PROXY=*` sends everything direct, so nothing is added at all.
pub fn plan(windows: &WindowsProxy, routes: &[(String, Route)], existing: &str) -> Vec<(String, String)> {
    let theirs: Vec<String> = existing.split(',').map(str::trim).filter(|e| !e.is_empty()).map(str::to_string).collect();
    if theirs.iter().any(|e| e == "*") {
        return Vec::new();
    }
    let env = |proxy: String, direct: Vec<String>| {
        let mut no_proxy: Vec<String> = Vec::new();
        for entry in std::iter::once(ALWAYS_DIRECT.to_string()).chain(direct).chain(theirs.iter().cloned()) {
            for e in entry.split(',').map(str::trim).filter(|e| !e.is_empty()) {
                if !no_proxy.iter().any(|n| n.eq_ignore_ascii_case(e)) {
                    no_proxy.push(e.to_string());
                }
            }
        }
        vec![("HTTPS_PROXY".to_string(), proxy), ("NO_PROXY".to_string(), no_proxy.join(","))]
    };
    if windows.automatic() && routes.iter().any(|(_, r)| !matches!(r, Route::Unknown(_))) {
        let mut counts: BTreeMap<String, usize> = BTreeMap::new();
        for (_, route) in routes {
            if let Route::Proxy(list) = route {
                if let Some(p) = https_proxy(list) {
                    *counts.entry(p).or_default() += 1;
                }
            }
        }
        let direct: Vec<String> = routes.iter().filter(|(_, r)| *r == Route::Direct).map(|(h, _)| h.clone()).collect();
        // The most common proxy; the first in name order when two tie, so each start gives the same.
        return match counts.into_iter().max_by(|a, b| a.1.cmp(&b.1).then(b.0.cmp(&a.0))) {
            Some((proxy, _)) => env(proxy, direct),
            // Windows sends everything direct; the engine should too, even past a fixed proxy it reads.
            None if windows.python_sees_fixed => vec![("NO_PROXY".to_string(), "*".to_string())],
            None => Vec::new(),
        };
    }
    if windows.python_sees_fixed || windows.proxy.is_empty() {
        return Vec::new();
    }
    match https_proxy(&windows.proxy) {
        Some(proxy) => env(proxy, vec![no_proxy_from_bypass(&windows.bypass)]),
        None => Vec::new(),
    }
}

/// Windows' bypass list as `NO_PROXY`: `*.contoso.com` as `.contoso.com`, a host as itself, and
/// `10.*`, `10.1.*` or `10.1.2.*` as a network (`10.0.0.0/8`). `<local>` and other patterns can't be
/// written there and are left out.
pub fn no_proxy_from_bypass(list: &str) -> String {
    let mut out: Vec<String> = Vec::new();
    for entry in list.split([';', ' ', ',']).map(str::trim).filter(|e| !e.is_empty()) {
        let entry = entry.to_ascii_lowercase();
        let host_ok = |h: &str| !h.is_empty() && h.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-');
        let written = if let Some(suffix) = entry.strip_prefix("*.") {
            host_ok(suffix).then(|| format!(".{suffix}"))
        } else if let Some(net) = entry.strip_suffix(".*") {
            let parts: Vec<&str> = net.split('.').collect();
            let octets = parts.iter().all(|p| !p.is_empty() && p.len() <= 3 && p.parse::<u8>().is_ok());
            (octets && parts.len() <= 3).then(|| {
                let mut full: Vec<&str> = parts.clone();
                full.resize(4, "0");
                format!("{}/{}", full.join("."), parts.len() * 8)
            })
        } else {
            (host_ok(&entry) && !entry.contains('*')).then_some(entry)
        };
        if let Some(w) = written {
            if !out.contains(&w) {
                out.push(w);
            }
        }
    }
    out.join(",")
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

#[cfg(windows)]
fn windows_config() -> Option<WindowsProxy> {
    use windows::core::w;
    use windows::Win32::Foundation::{GlobalFree, ERROR_SUCCESS, HGLOBAL};
    use windows::Win32::Networking::WinHttp::{WinHttpGetIEProxyConfigForCurrentUser, WINHTTP_CURRENT_USER_IE_PROXY_CONFIG};
    use windows::Win32::System::Registry::{RegGetValueW, HKEY_CURRENT_USER, RRF_RT_REG_DWORD};
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
    let (mut enabled, mut size) = (0u32, std::mem::size_of::<u32>() as u32);
    let read = unsafe {
        RegGetValueW(
            HKEY_CURRENT_USER,
            w!(r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"),
            w!("ProxyEnable"),
            RRF_RT_REG_DWORD,
            None,
            Some(&mut enabled as *mut u32 as *mut _),
            Some(&mut size),
        )
    };
    Some(WindowsProxy {
        auto_detect: config.fAutoDetect.as_bool(),
        pac_url: take(config.lpszAutoConfigUrl),
        proxy: take(config.lpszProxy),
        bypass: take(config.lpszProxyBypass),
        python_sees_fixed: read == ERROR_SUCCESS && enabled == 1,
    })
}

#[cfg(not(windows))]
fn windows_config() -> Option<WindowsProxy> {
    None
}

/// Windows' route to `https://<host>/` for each host, from its PAC file or WPAD, through one WinHTTP
/// session (which keeps the PAC file it fetched). After a failure the rest are Unknown too, so a
/// network with no WPAD server costs one wait, not one per host.
#[cfg(windows)]
fn routes(windows: &WindowsProxy, hosts: &[String]) -> Vec<Route> {
    use windows::core::{w, HSTRING, PCWSTR};
    use windows::Win32::Foundation::{GlobalFree, HGLOBAL};
    use windows::Win32::Networking::WinHttp::{
        WinHttpCloseHandle, WinHttpGetProxyForUrl, WinHttpOpen, WinHttpSetTimeouts, WINHTTP_ACCESS_TYPE_NAMED_PROXY,
        WINHTTP_ACCESS_TYPE_NO_PROXY, WINHTTP_AUTOPROXY_AUTO_DETECT, WINHTTP_AUTOPROXY_CONFIG_URL,
        WINHTTP_AUTOPROXY_OPTIONS, WINHTTP_AUTO_DETECT_TYPE_DHCP, WINHTTP_AUTO_DETECT_TYPE_DNS_A, WINHTTP_PROXY_INFO,
    };
    let session = unsafe { WinHttpOpen(w!("KratosForge"), WINHTTP_ACCESS_TYPE_NO_PROXY, PCWSTR::null(), PCWSTR::null(), 0) };
    if session.is_null() {
        return hosts.iter().map(|_| Route::Unknown("WinHTTP couldn't start".into())).collect();
    }
    let _ = unsafe { WinHttpSetTimeouts(session, 5_000, 5_000, 5_000, 10_000) };
    let pac = HSTRING::from(windows.pac_url.as_str());
    // Never sign in on the first try (autologon would hand the Windows account's NTLM or Kerberos
    // answer to whichever PAC server challenges): see autologon_retry.
    let options = |detect: bool, autologon: bool| {
        let mut o = WINHTTP_AUTOPROXY_OPTIONS { fAutoLogonIfChallenged: autologon.into(), ..Default::default() };
        if detect && windows.auto_detect {
            o.dwFlags |= WINHTTP_AUTOPROXY_AUTO_DETECT;
            o.dwAutoDetectFlags = WINHTTP_AUTO_DETECT_TYPE_DHCP | WINHTTP_AUTO_DETECT_TYPE_DNS_A;
        }
        if !windows.pac_url.is_empty() {
            o.dwFlags |= WINHTTP_AUTOPROXY_CONFIG_URL;
            o.lpszAutoConfigUrl = PCWSTR(pac.as_ptr());
        }
        o
    };
    let take = |p: windows::core::PWSTR| -> String {
        if p.is_null() {
            return String::new();
        }
        let text = unsafe { p.to_string() }.unwrap_or_default();
        let _ = unsafe { GlobalFree(Some(HGLOBAL(p.0 as _))) };
        text
    };
    let mut detect = true;
    let mut out = Vec::new();
    for host in hosts {
        if let Some(Route::Unknown(why)) = out.last() {
            out.push(Route::Unknown(why.clone()));
            continue;
        }
        let url = HSTRING::from(format!("https://{host}/"));
        let ask = |detect: bool, autologon: bool| {
            let mut opts = options(detect, autologon);
            let mut info = WINHTTP_PROXY_INFO::default();
            unsafe { WinHttpGetProxyForUrl(session, &url, &mut opts, &mut info) }.map(|()| {
                let (proxy, _) = (take(info.lpszProxy), take(info.lpszProxyBypass));
                if info.dwAccessType == WINHTTP_ACCESS_TYPE_NAMED_PROXY && !proxy.is_empty() {
                    Route::Proxy(proxy)
                } else {
                    Route::Direct
                }
            })
        };
        // As Microsoft advises: when detection fails and there is a PAC address too, try that alone.
        let route = match ask(detect, false) {
            Err(_) if detect && windows.auto_detect && !windows.pac_url.is_empty() => {
                detect = false;
                ask(false, false)
            }
            other => other,
        };
        let route = match route {
            Err(e) if autologon_retry(e.code().0 as u32 & 0xFFFF, detect && windows.auto_detect, &windows.pac_url) => ask(false, true),
            other => other,
        };
        out.push(route.unwrap_or_else(|e| Route::Unknown(e.message())));
    }
    let _ = unsafe { WinHttpCloseHandle(session) };
    out
}

/// WinHTTP's "the PAC server asked for a sign-in" (ERROR_WINHTTP_LOGIN_FAILURE).
pub const LOGIN_FAILURE: u32 = 12015;

/// Whether to ask again signed in with the Windows account, after `code`: only when the PAC
/// server asked for a sign-in, and only for the PAC address IT set, never one WPAD found (anyone
/// on the network can answer WPAD and collect the sign-in), as Microsoft's WinHTTP guidance has it.
pub fn autologon_retry(code: u32, detected: bool, pac_url: &str) -> bool {
    code == LOGIN_FAILURE && !detected && !pac_url.is_empty()
}

#[cfg(not(windows))]
fn routes(_windows: &WindowsProxy, hosts: &[String]) -> Vec<Route> {
    hosts.iter().map(|_| Route::Unknown("not Windows".into())).collect()
}

#[cfg(test)]
mod tests {
    use super::{args_for, autologon_retry, bypassed, https_proxy, no_proxy_from_bypass, plan, Route, WindowsProxy, ALWAYS_DIRECT, LOGIN_FAILURE};

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

    fn routes(list: &[(&str, Route)]) -> Vec<(String, Route)> {
        list.iter().map(|(h, r)| (h.to_string(), r.clone())).collect()
    }

    fn pac() -> WindowsProxy {
        WindowsProxy { pac_url: "http://wpad.contoso.com/proxy.pac".into(), ..Default::default() }
    }

    #[test]
    fn with_a_pac_file_the_engine_gets_the_proxy_most_hosts_use_and_the_direct_ones_in_no_proxy() {
        let env = plan(&pac(), &routes(&[
            ("login.microsoftonline.com", Route::Proxy("proxy.contoso.com:8080;backup.contoso.com:8080".into())),
            ("api.fabric.microsoft.com", Route::Proxy("proxy.contoso.com:8080".into())),
            ("github.com", Route::Proxy("other.contoso.com:3128".into())),
            ("pypi.org", Route::Direct),
        ]), "");
        assert_eq!(env, [
            ("HTTPS_PROXY".to_string(), "http://proxy.contoso.com:8080".to_string()),
            ("NO_PROXY".to_string(), format!("{ALWAYS_DIRECT},pypi.org")),
        ]);
    }

    #[test]
    fn wpad_counts_like_a_pac_file() {
        let wpad = WindowsProxy { auto_detect: true, ..Default::default() };
        let env = plan(&wpad, &routes(&[("github.com", Route::Proxy("proxy:8080".into()))]), "");
        assert_eq!(env[0], ("HTTPS_PROXY".to_string(), "http://proxy:8080".to_string()));
    }

    #[test]
    fn a_pac_file_that_sends_everything_direct_adds_nothing_unless_a_fixed_proxy_would_be_used() {
        let all_direct = routes(&[("github.com", Route::Direct), ("pypi.org", Route::Direct)]);
        assert!(plan(&pac(), &all_direct, "").is_empty());
        let seen = WindowsProxy { proxy: "proxy:8080".into(), python_sees_fixed: true, ..pac() };
        assert_eq!(plan(&seen, &all_direct, ""), [("NO_PROXY".to_string(), "*".to_string())]);
    }

    #[test]
    fn when_windows_cant_run_its_pac_file_the_fixed_proxy_rules_apply() {
        let unknown = routes(&[("github.com", Route::Unknown("no WPAD server".into()))]);
        assert!(plan(&WindowsProxy { auto_detect: true, ..Default::default() }, &unknown, "").is_empty());
        let machine = WindowsProxy { auto_detect: true, proxy: "proxy:8080".into(), bypass: "*.contoso.com".into(), ..Default::default() };
        assert_eq!(plan(&machine, &unknown, "")[0].1, "http://proxy:8080");
    }

    #[test]
    fn a_fixed_proxy_the_engine_reads_itself_is_left_alone() {
        let user = WindowsProxy { proxy: "proxy:8080".into(), python_sees_fixed: true, ..Default::default() };
        assert!(plan(&user, &[], "").is_empty());
        assert!(plan(&WindowsProxy::default(), &[], "").is_empty());
    }

    #[test]
    fn a_machine_wide_fixed_proxy_goes_to_the_engine_with_its_bypass_list() {
        let machine = WindowsProxy { proxy: "proxy.contoso.com:8080".into(), bypass: "*.contoso.com;10.*;<local>".into(), ..Default::default() };
        assert_eq!(plan(&machine, &[], ""), [
            ("HTTPS_PROXY".to_string(), "http://proxy.contoso.com:8080".to_string()),
            ("NO_PROXY".to_string(), format!("{ALWAYS_DIRECT},.contoso.com,10.0.0.0/8")),
        ]);
        let with_password = WindowsProxy { proxy: "user:pass@proxy:8080".into(), ..Default::default() };
        assert!(plan(&with_password, &[], "").is_empty(), "a credential never goes into a variable");
    }

    #[test]
    fn the_bypass_list_is_written_as_no_proxy_where_it_can_be() {
        assert_eq!(no_proxy_from_bypass("*.contoso.com; intranet.fabrikam.com;10.1.*;192.168.1.*;<local>;*corp*;10.*.5.*"),
                   ".contoso.com,intranet.fabrikam.com,10.1.0.0/16,192.168.1.0/24");
        assert_eq!(no_proxy_from_bypass(""), "");
    }

    #[test]
    fn the_windows_sign_in_goes_only_to_the_pac_address_it_set_and_only_when_asked() {
        let it_pac = "http://pac.contoso.com/proxy.pac";
        assert!(autologon_retry(LOGIN_FAILURE, false, it_pac));
        assert!(!autologon_retry(LOGIN_FAILURE, true, it_pac), "never to a PAC server WPAD found");
        assert!(!autologon_retry(LOGIN_FAILURE, true, ""));
        assert!(!autologon_retry(LOGIN_FAILURE, false, ""));
        assert!(!autologon_retry(12167, false, it_pac), "a PAC file that couldn't be fetched isn't a sign-in request");
    }

    #[test]
    fn a_no_proxy_it_already_set_is_kept_and_added_to_and_star_switches_this_off() {
        let machine = WindowsProxy { proxy: "proxy:8080".into(), bypass: "*.contoso.com".into(), ..Default::default() };
        let env = plan(&machine, &[], "intranet, .fabrikam.com,LOCALHOST");
        assert_eq!(env[1], ("NO_PROXY".to_string(), format!("{ALWAYS_DIRECT},.contoso.com,intranet,.fabrikam.com")));
        assert!(plan(&machine, &[], "*").is_empty(), "NO_PROXY=* sends everything direct: nothing added");
        let pac_env = plan(&pac(), &routes(&[("github.com", Route::Proxy("proxy:8080".into()))]), "files.contoso.com");
        assert!(pac_env[1].1.ends_with(",files.contoso.com"));
    }
}
