fn main() {
    // CI sets FABRIC_RELEASE_COMMIT for builds of main. The app compares it with
    // the latest release to offer updates; local builds leave it unset and skip that.
    println!("cargo:rerun-if-env-changed=FABRIC_RELEASE_COMMIT");
    let commit = std::env::var("FABRIC_RELEASE_COMMIT").unwrap_or_default();
    println!("cargo:rustc-env=FABRIC_BUILD_COMMIT={commit}");
    // The Microsoft Entra app the app signs in with unless the organization or the person
    // sets another (repository variable FABRIC_CLIENT_ID; empty means people paste one in Settings).
    println!("cargo:rerun-if-env-changed=FABRIC_CLIENT_ID");
    let client_id = std::env::var("FABRIC_CLIENT_ID").unwrap_or_default();
    println!("cargo:rustc-env=FABRIC_BUILTIN_CLIENT_ID={client_id}");
    // CI builds the engine first and passes the SHA-256 of its two zips, so the exe accepts only
    // its own engine (main.rs check_pinned_engine). Anything but 64 hex digits is a broken build,
    // and a release build (one with a commit) must have both, so a lost CI output can't ship
    // an exe that accepts any engine.
    for name in ["FABRIC_ENGINE_SHA256", "FABRIC_ENGINE_UPDATE_SHA256"] {
        println!("cargo:rerun-if-env-changed={name}");
        let sha = std::env::var(name).unwrap_or_default().trim().to_ascii_lowercase();
        assert!(sha.is_empty() || (sha.len() == 64 && sha.bytes().all(|b| b.is_ascii_hexdigit())),
                "{name} must be a SHA-256 in hex");
        assert!(commit.is_empty() || !sha.is_empty(), "{name} must be set when FABRIC_RELEASE_COMMIT is");
        println!("cargo:rustc-env=FABRIC_BUILD_{}={sha}", name.trim_start_matches("FABRIC_"));
    }
    tauri_build::build()
}
