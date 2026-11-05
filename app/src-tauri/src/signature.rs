//! Who signed an exe (Authenticode), so a signed build only installs updates from the same
//! publisher. The update's checksum comes from the same release as the file, so it proves the
//! download is whole, not who made it; a signature does. A build that isn't signed itself (every
//! build until the signing account is set up, and local builds) checks nothing new, so turning
//! signing on is what turns this on, with no setting to forget.

/// The signer of a validly signed file: the subject of its signing certificate, as an X.500
/// string ("CN=..., O=..., L=..., S=..., C=..."), followed by the certificate's Trusted Signing
/// identity usages (1.3.6.1.4.1.311.97.*, which name the validated identity) when it has any.
/// Trusted Signing issues a new certificate every few days with the same subject and usages, so
/// those, not the certificate, name the publisher; another authority's certificate with the same
/// subject lacks Microsoft's usage and doesn't match.
/// Ok(None) when the file has no signature; Err when it has one Windows doesn't trust (tampered,
/// or chaining to no trusted root) or can't be read. Revocation isn't checked: that needs the
/// certificate authority's servers, which many company networks block, and Trusted Signing's
/// certificates last only days, so a stolen one stops working on its own.
#[cfg(windows)]
pub fn signer(path: &std::path::Path) -> Result<Option<String>, String> {
    use windows::core::{GUID, HSTRING, PCWSTR};
    use windows::Win32::Foundation::{HANDLE, HWND, TRUST_E_NOSIGNATURE, TRUST_E_SUBJECT_FORM_UNKNOWN, TRUST_E_PROVIDER_UNKNOWN};
    use windows::Win32::Security::Cryptography::{
        CertGetEnhancedKeyUsage, CertNameToStrW, CERT_X500_NAME_STR, CTL_USAGE, X509_ASN_ENCODING,
    };
    use windows::Win32::Security::WinTrust::{
        WTHelperGetProvCertFromChain, WTHelperGetProvSignerFromChain, WTHelperProvDataFromStateData, WinVerifyTrust,
        WINTRUST_ACTION_GENERIC_VERIFY_V2, WINTRUST_DATA, WINTRUST_DATA_0, WINTRUST_FILE_INFO, WTD_CHOICE_FILE,
        WTD_REVOKE_NONE, WTD_STATEACTION_CLOSE, WTD_STATEACTION_VERIFY, WTD_UI_NONE,
    };

    let wide = HSTRING::from(path.as_os_str());
    let mut file = WINTRUST_FILE_INFO {
        cbStruct: std::mem::size_of::<WINTRUST_FILE_INFO>() as u32,
        pcwszFilePath: PCWSTR(wide.as_ptr()),
        hFile: HANDLE::default(),
        pgKnownSubject: std::ptr::null_mut(),
    };
    let mut data = WINTRUST_DATA {
        cbStruct: std::mem::size_of::<WINTRUST_DATA>() as u32,
        dwUIChoice: WTD_UI_NONE,
        fdwRevocationChecks: WTD_REVOKE_NONE,
        dwUnionChoice: WTD_CHOICE_FILE,
        Anonymous: WINTRUST_DATA_0 { pFile: &mut file },
        dwStateAction: WTD_STATEACTION_VERIFY,
        ..Default::default()
    };
    let mut action: GUID = WINTRUST_ACTION_GENERIC_VERIFY_V2;
    unsafe {
        let status = WinVerifyTrust(HWND::default(), &mut action, &mut data as *mut _ as *mut _);
        let outcome = if status == TRUST_E_NOSIGNATURE.0 || status == TRUST_E_SUBJECT_FORM_UNKNOWN.0
            || status == TRUST_E_PROVIDER_UNKNOWN.0 {
            Ok(None)
        } else if status != 0 {
            Err(format!("Windows doesn't trust its signature (0x{:08X})", status as u32))
        } else {
            // Read the signer while the state is still open; it is freed below either way.
            let prov = WTHelperProvDataFromStateData(data.hWVTStateData);
            let sgnr = if prov.is_null() { std::ptr::null_mut() } else { WTHelperGetProvSignerFromChain(prov, 0, false, 0) };
            let cert = if sgnr.is_null() { std::ptr::null_mut() } else { WTHelperGetProvCertFromChain(sgnr, 0) };
            if cert.is_null() || (*cert).pCert.is_null() || (*(*cert).pCert).pCertInfo.is_null() {
                Err("Its signing certificate couldn't be read".to_string())
            } else {
                let context = (*cert).pCert;
                let subject = &(*(*context).pCertInfo).Subject;
                let len = CertNameToStrW(X509_ASN_ENCODING, subject, CERT_X500_NAME_STR, None);
                let mut buf = vec![0u16; len as usize];
                let written = CertNameToStrW(X509_ASN_ENCODING, subject, CERT_X500_NAME_STR, Some(&mut buf));
                let name = String::from_utf16_lossy(&buf[..(written as usize).saturating_sub(1)]);
                // The certificate's own usages (flag 0x2, CERT_FIND_EXT_ONLY_ENHKEY_USAGE_FLAG): the
                // ones it carries, not what the computer's policy adds.
                let mut size = 0u32;
                let mut usages: Vec<String> = Vec::new();
                if CertGetEnhancedKeyUsage(context, 0x2, None, &mut size).is_ok() && size > 0 {
                    let mut buf = vec![0u64; (size as usize).div_ceil(8)];
                    let usage = buf.as_mut_ptr() as *mut CTL_USAGE;
                    if CertGetEnhancedKeyUsage(context, 0x2, Some(usage), &mut size).is_ok() {
                        for i in 0..(*usage).cUsageIdentifier as usize {
                            let oid = *(*usage).rgpszUsageIdentifier.add(i);
                            if let Ok(text) = oid.to_string() {
                                if text.starts_with(TRUSTED_SIGNING_IDENTITY) {
                                    usages.push(text);
                                }
                            }
                        }
                    }
                }
                usages.sort();
                if name.trim().is_empty() {
                    Err("Its signing certificate names no one".to_string())
                } else if usages.is_empty() {
                    Ok(Some(name))
                } else {
                    Ok(Some(format!("{name} [{}]", usages.join(", "))))
                }
            }
        };
        data.dwStateAction = WTD_STATEACTION_CLOSE;
        let _ = WinVerifyTrust(HWND::default(), &mut action, &mut data as *mut _ as *mut _);
        outcome
    }
}

/// Trusted Signing puts an extended key usage under this arc in every certificate it issues for a
/// validated identity; the rest of the number is that identity's.
#[cfg(windows)]
const TRUSTED_SIGNING_IDENTITY: &str = "1.3.6.1.4.1.311.97.";

/// Off Windows nothing is signed this way, so nothing is checked.
#[cfg(not(windows))]
pub fn signer(_path: &std::path::Path) -> Result<Option<String>, String> {
    Ok(None)
}

/// Whether an update may replace this build, by their signers: a signed build only takes an
/// update signed by the same publisher; an unsigned one takes any update (its checksums are still
/// checked). `ours` is this exe's signer, `theirs` the download's.
pub fn update_allowed(ours: &Result<Option<String>, String>, theirs: &Result<Option<String>, String>) -> Result<(), String> {
    let mine = match ours {
        Ok(Some(name)) => name,
        Ok(None) => return Ok(()), // unsigned: nothing to compare with
        // Signed, but Windows won't vouch for it now: there's no publisher to hold the update to,
        // so none is installed rather than any.
        Err(why) => return Err(format!("This build's own signature can't be checked ({why}), so no update was installed. \
            Download the new build from the releases page instead.")),
    };
    match theirs {
        Ok(Some(name)) if name == mine => Ok(()),
        Ok(Some(name)) => Err(format!("The update is signed by {name}, not by {mine} like this build, so it wasn't installed.")),
        Ok(None) => Err(format!("The update isn't signed, and this build is signed by {mine}, so it wasn't installed.")),
        Err(why) => Err(format!("The update's signature isn't valid ({why}), so it wasn't installed.")),
    }
}

#[cfg(test)]
mod tests {
    use super::update_allowed;

    const US: &str = "CN=KratosBI, O=KratosBI, L=Austin, S=Texas, C=US";

    #[test]
    fn an_unsigned_build_takes_any_update() {
        for theirs in [Ok(None), Ok(Some(US.to_string())), Err("bad".to_string())] {
            assert_eq!(update_allowed(&Ok(None), &theirs), Ok(()));
        }
    }

    #[test]
    fn a_signed_build_takes_only_its_own_publishers_update() {
        let ours = Ok(Some(US.to_string()));
        assert_eq!(update_allowed(&ours, &Ok(Some(US.to_string()))), Ok(()));
        assert!(update_allowed(&ours, &Ok(None)).unwrap_err().contains("isn't signed"));
        assert!(update_allowed(&ours, &Ok(Some("CN=KratosBI, O=Someone Else, C=US".into()))).unwrap_err().contains("Someone Else"));
        assert!(update_allowed(&ours, &Err("Windows doesn't trust its signature".into())).unwrap_err().contains("isn't valid"));
    }

    #[test]
    fn a_build_whose_own_signature_cant_be_checked_installs_nothing() {
        for theirs in [Ok(None), Ok(Some(US.to_string()))] {
            assert!(update_allowed(&Err("0x800B0109".into()), &theirs).unwrap_err().contains("can't be checked"));
        }
    }
}
