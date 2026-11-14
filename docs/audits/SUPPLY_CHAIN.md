# Supply chain: SBOMs, licenses and pinning

This page records what KratosForge ships, which licenses it ships under, and how each part is pinned. It was checked against main on 2026-11-06. License data comes from two sources:
- **Engine packages:** PyPI's metadata for each pinned package. Where the metadata was blank, I read the wheel itself.
- **Shell crates:** `cargo metadata` for the Windows target.

## What ships

| Part | What it is | Where its parts are pinned | SBOM |
| --- | --- | --- | --- |
| Engine | python.org's embeddable Python 3.12.10, the 76 packages the engine installs, and the engine's own package | `sidecar/engine-requirements.txt` holds versions and SHA-256 hashes. `ci.yml` holds the Python zip's MD5 and SHA-256 | `sbom-engine.cdx.json`, made by `cyclonedx-py environment` from the built engine folder, each package with the SHA-256 of the file pip installed (`.github/scripts/sbom_npm.py add-hashes`, from pip's install report), plus the embeddable Python itself with its pinned SHA-256 (`sbom_npm.py add-python`) |
| Shell | The Tauri app (`app/src-tauri`) and its 243 Rust crates on Windows | `app/src-tauri/Cargo.lock` holds versions and crate checksums | `sbom-app.cdx.json`, made by `cargo cyclonedx --target x86_64-pc-windows-msvc` from `Cargo.lock`, with each crate's checksum |
| Notebook editor | CodeMirror, bundled into `app/ui/vendor/codemirror/codemirror.js` | That folder's `package.json` gives exact versions for the direct packages. The built file is committed | `sbom-ui.cdx.json`: the packages `package.json` names (build tools left out) and everything they depend on, from `package-lock.json` with its SHA-512 hashes. Its license is in `LICENSE` there |
| Charts | Vega, Vega-Lite, vega-embed and the d3 packages they bundle, in `app/ui/vendor/vega/vega-frame.js` | That folder's `package.json` gives exact versions for the direct packages. `THIRD-PARTY-NOTICES.md` there lists each bundled package and its version | `sbom-ui.cdx.json`: Vega, Vega-Lite and vega-embed from `package-lock.json` with their hashes, and the packages their prebuilt files bundle from `THIRD-PARTY-NOTICES.md` (the versions the upstream build used, so no hash) |
| Skin typefaces | Inter and DM Sans, latin `woff2` files copied from Fontsource into `app/ui/vendor/fonts` | That folder's `package.json` gives exact versions. The font files are committed | `sbom-ui.cdx.json`: both packages from `package-lock.json` with their hashes. Their licence (SIL OFL 1.1) is in `LICENSE-inter` and `LICENSE-dm-sans` there |
| Model converter | The Apache Ossie Microsoft converter, copied into `sidecar/fabric_desktop/_vendor/ossie_microsoft` | Committed source | Covered by the engine's own package |
| VS Code extension | `vscode/` | `vscode/package-lock.json`. It has no runtime dependencies, only build and test tools | `sbom-vscode.cdx.json`, made from `kratosforge.vsix` itself: the file's SHA-256, every file inside it with its SHA-256, and the build tools from `package-lock.json` with their SHA-512 hashes, scope `excluded` (it is packaged with `--no-dependencies`, so none ships). It is also listed as itself in `sbom-ui.cdx.json`. The `.vsix` is reproducible: CI builds it twice and fails if the two differ |

Every CI build makes four SBOMs: `sbom-engine` in the engine job, `sbom-app` and `sbom-ui` in the `sbom-app` job, and `sbom-vscode` in the `vscode-extension` job. `sbom-ui` is made by `.github/scripts/sbom_npm.py` from the committed lock files, with no extra tool. All four are CycloneDX JSON. The release job attaches them to each Preview and Stable release, and `version.json` gives each one's SHA-256 (`sbom_engine_sha256`, `sbom_app_sha256`, `sbom_ui_sha256`, `sbom_vscode_sha256`). Promote to stable checks those before copying them. The engine and the shell need no new CI job.

`.github/scripts/sbom_check.py` fails CI when a dependency is pinned without a hash, or an SBOM doesn't match its lock file. In the `sbom-app` job it checks every lock file: each npm package comes from registry.npmjs.org with a SHA-512, each crate from crates.io with a checksum, and each pip pin in a hashed requirements file is `name==version` with SHA-256 hashes. It then checks that `sbom-app` lists exactly the crates the Windows build compiles (from `cargo metadata --filter-platform`), each with its `Cargo.lock` checksum, and that `sbom-ui` lists every bundled package with its lock file's hash. In the `vscode-extension` job it checks that `sbom-vscode` names the `.vsix` by its SHA-256, lists every file inside it and no other, each with its SHA-256, and lists each build tool with its lock file's hash; that job also runs `npm audit signatures`. In the engine job it checks that `sbom-engine` lists every package `engine-requirements.txt` pins, at that version, with a hash the file pins. The packages Vega's prebuilt files bundle are the one listed exception: they come from `THIRD-PARTY-NOTICES.md` and have no hash of their own.

## Licenses

**No GPL, LGPL or AGPL code ships, and every component has a known license.** Four things are worth knowing:

- **MPL-2.0, a file-level weak copyleft.** It applies to `certifi` in the engine and to five crates in the shell: `cssparser`, `cssparser-macros`, `dtoa-short`, `option-ext` and `selectors`. All six ship unmodified. MPL-2.0 asks that changes to those files stay under the same license and that their source is available. It doesn't reach the rest of the app.
- **Microsoft's own terms.** Two packages ship Microsoft components under Microsoft's license terms:
  - `mssql-python-odbc` carries Microsoft ODBC Driver 18 for SQL Server and the Visual C++ runtime.
  - `mssql-python` ships its DLLs under the same terms. Its own code is MIT.

  `NOTICE` and `docs/install.md` already say this, and the license files ship in the engine.
- **PSF-style licenses, which are permissive.** The engine's Python, `typing-extensions` and `pywin32` use the PSF license. `matplotlib` uses its own PSF-based license.
- **Blank PyPI metadata.** Two packages have none, so I read their wheels instead. `azure-core`'s wheel says MIT. `mssql-python`'s wheel says MIT, apart from its DLLs.

### Shell crates (Windows target, 243)

| License | Crates |
| --- | --- |
| A choice that includes MIT or Apache-2.0 (dual or triple licensed, sometimes with Zlib, 0BSD, Unlicense or Unicode-3.0) | 170 |
| MIT | 43 |
| Unicode-3.0 | 18 |
| MPL-2.0 | 5 (named above) |
| BSD-3-Clause, alone or with MIT | 4 |
| Zlib | 2 |
| Apache-2.0 | 1 |

The full list, crate by crate, is in each release's `sbom-app.cdx.json`.

### Engine packages (76)

| Package | Version | License | Note |
| --- | --- | --- | --- |
| altair | 6.3.0 | BSD-3-Clause |  |
| annotated-types | 0.8.0 | MIT |  |
| anyio | 4.15.1 | MIT |  |
| arro3-core | 0.9.1 | MIT OR Apache-2.0 |  |
| attrs | 26.1.0 | MIT |  |
| azure-core | 1.41.0 | MIT | PyPI metadata blank; read from the wheel |
| azure-identity | 1.26.0 | MIT |  |
| bokeh | 3.10.1 | BSD-3-Clause |  |
| certifi | 2026.10.19 | MPL-2.0 | Weak copyleft (file-level), shipped unmodified |
| cffi | 2.1.1 | MIT-0 |  |
| charset-normalizer | 3.5.1 | MIT |  |
| click | 8.5.0 | BSD-3-Clause |  |
| contourpy | 1.4.0 | BSD-3-Clause |  |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause |  |
| cycler | 0.12.1 | BSD-3-Clause |  |
| deltalake | 1.6.6 | Apache-2.0 |  |
| deprecated | 3.0.0 | MIT |  |
| duckdb | 1.5.6 | MIT |  |
| fonttools | 4.67.0 | MIT |  |
| h11 | 0.16.0 | MIT |  |
| httpcore2 | 2.13.1 | BSD-3-Clause |  |
| httpx2 | 2.13.1 | BSD-3-Clause |  |
| idna | 3.20 | BSD-3-Clause |  |
| jinja2 | 3.1.6 | BSD-3-Clause |  |
| jsonschema | 4.26.0 | MIT |  |
| jsonschema-specifications | 2025.9.1 | MIT |  |
| kiwisolver | 1.5.1 | BSD-3-Clause |  |
| markupsafe | 3.0.4 | BSD-3-Clause |  |
| matplotlib | 3.11.2 | Matplotlib license (PSF-based) |  |
| mcp | 2.3.0 | MIT |  |
| mcp-types | 2.3.0 | MIT |  |
| msal | 1.39.0 | MIT |  |
| msal-extensions | 1.3.1 | MIT |  |
| mssql-python | 1.16.0 | MIT, except its bundled DLLs | DLLs under Microsoft's terms; PyPI metadata blank |
| mssql-python-odbc | 18.6.2.2 | Microsoft software license terms | ODBC Driver 18 and the Visual C++ runtime, redistributed under Microsoft's terms |
| mssql-python-rs | 0.3.0 | MIT |  |
| narwhals | 2.27.1 | MIT |  |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |  |
| opentelemetry-api | 1.45.2 | Apache-2.0 |  |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |  |
| pandas | 2.3.3 | BSD-3-Clause |  |
| pillow | 13.0.0 | MIT-CMU |  |
| pip | 26.2.2 | MIT |  |
| plotly | 7.1.0 | MIT |  |
| polars | 2.0.0 | MIT |  |
| polars-runtime-32 | 2.0.0 | MIT |  |
| pyarrow | 26.0.0 | Apache-2.0 |  |
| pycparser | 3.0 | BSD-3-Clause |  |
| pydantic | 2.14.1 | MIT |  |
| pydantic-core | 2.50.1 | MIT |  |
| pyjwt | 2.15.0 | MIT |  |
| pymsalruntime | 0.20.7 | MIT |  |
| pyparsing | 3.1.1 | MIT |  |
| python-dateutil | 2.9.0.post0 | Apache-2.0 OR BSD-3-Clause |  |
| python-multipart | 0.0.32 | Apache-2.0 |  |
| pytz | 2026.5 | MIT |  |
| pywin32 | 312 | PSF-2.0 |  |
| pyyaml | 6.0.3 | MIT |  |
| referencing | 0.37.0 | MIT |  |
| requests | 2.34.2 | Apache-2.0 |  |
| rpds-py | 2026.9.1 | MIT |  |
| ruff | 0.17.1 | MIT |  |
| seaborn | 0.13.2 | BSD-3-Clause |  |
| six | 1.17.0 | MIT |  |
| sqlglot | 30.23.0 | MIT |  |
| sse-starlette | 3.5.0 | BSD-3-Clause |  |
| starlette | 1.8.0 | BSD-3-Clause |  |
| tornado | 6.6.1 | Apache-2.0 |  |
| truststore | 0.10.5 | MIT |  |
| typing-extensions | 4.16.0 | PSF-2.0 |  |
| typing-inspection | 0.4.4 | MIT |  |
| tzdata | 2026.5 | Apache-2.0 |  |
| urllib3 | 2.8.0 | MIT |  |
| uvicorn | 0.54.0 | BSD-3-Clause |  |
| wrapt | 2.5.1 | BSD-2-Clause |  |
| xyzservices | 2026.9.1 | BSD-3-Clause |  |

Some wheels bundle native libraries of their own, for example pyarrow and polars. A package's license notes for those libraries ship inside the package. The SBOM lists the package, not each library it bundles.

### Bundled JavaScript

- **CodeMirror:** MIT.
- **Vega, Vega-Lite and vega-embed:** BSD-3-Clause.
- **The packages the Vega build bundles:** ISC, BSD, MIT and Apache-2.0 licenses, listed one by one in `app/ui/vendor/vega/THIRD-PARTY-NOTICES.md`.

## Pinning

| What | Pinned by | Hash-checked on install |
| --- | --- | --- |
| Engine packages | `engine-requirements.txt` | Yes: `pip install --require-hashes --only-binary=:all:`, then `pip check` |
| Engine packages and test tools in CI's test and lint jobs | `.github/requirements/test-linux.txt`, `test-windows.txt` and `lint.txt`, at `constraints.txt`'s versions (`tests/test_toolchain_pins.py` checks they match) | Yes: `pip install --require-hashes --only-binary=:all:`. The engine itself is then installed with `--no-deps --no-build-isolation`, built by the hashed setuptools |
| Engine Python | `PYTHON_VERSION` in `ci.yml` | Yes: python.org's MD5 and a pinned SHA-256 |
| Shell crates | `Cargo.lock` | Yes: `cargo build --locked` checks each crate's checksum against the lock file |
| GitHub Actions | A commit SHA on every `uses:` line in every workflow (16 actions). `.github/scripts/check_workflows.py` fails one named by a tag or branch | Yes, by the commit SHA |
| CI-only Rust tools (cargo-audit, cargo-deny, cargo-cyclonedx) | An exact version, with `cargo install --locked` | Yes, through each tool's own lock file |
| Bundled JavaScript | The built files are committed | They can't change without a PR |


## Known vulnerabilities

The CI `audit` job checks every lock file that ships, on every pull request, and the release job waits for it:

| Lock file | Tool | Fails on |
| --- | --- | --- |
| `sidecar/constraints.txt`, `sidecar/engine-requirements.txt`, `sidecar/fabric_desktop/engines/dbconnect_locks/*.txt` | `pip-audit` | Any advisory |
| `app/ui/vendor/*/package-lock.json`, `vscode/package-lock.json` (build tools too) | `npm audit` | High or critical; moderate and low are warnings |
| `app/src-tauri/Cargo.lock` | `cargo audit` | Any advisory |

An advisory can be let through only by a line in `.github/audit-exceptions.txt` that names it, says why, and lapses within 90 days (`.github/scripts/audit_exceptions.py`).

Gap: the packages that Vega's prebuilt files bundle aren't in a lock file (they come from `THIRD-PARTY-NOTICES.md`), so `npm audit` doesn't see them.

## Secret scanning

The CI `secrets` job runs detect-secrets (pinned with hashes in `.github/requirements/secret-scan.txt`) through `.github/scripts/secret_scan.py`. A pull request's changed files are scanned, and a push to main scans every tracked file. The release job waits for it. Anything that looks like a token, a key or a password fails the job unless `.secrets.baseline` lists it as reviewed (`"is_secret": false`). Entries are matched by file, kind and a hash of the value, so moving a line doesn't need a new review, and the job never prints the value. On a pull request the job also warns on each entry the pull request adds to the baseline, each filter or plugin it adds or removes there, and each line it adds with `pragma: allowlist secret`, so its reviewers see everything it lets through. It also scans each of the pull request's own commits, and warns on a value one commit adds and a later one takes out: the final files no longer have it, but the pull request's history does, so a real secret there still needs revoking. That is a warning rather than a failure, because failing can't take it out of the history and the baseline can only mark what the files hold now. A line holding only a `commit`, `sha` or `revision` field with a 40-character commit id is skipped, because product checks and benchmarks record a new one each run.

The baseline started with 235 entries, each checked by hand. All are test fixtures (redaction and credential-handling cases), example strings in docs, or hashes and commit ids. The repository's history was scanned once too, and the result was reviewed. `tests/test_secret_scan.py` checks that every entry is reviewed and that the job is wired as described.

## Reproducible builds

Checked in CI on 2026-11-09 by building each part twice from the same commit and diffing the results.

| Part | Reproducible? | How it's kept that way, or why not |
| --- | --- | --- |
| Engine zip (`fabric-desktop-engine.zip`, and the update zip) | Yes, byte for byte, unsigned | The engine job builds the engine folder twice, in two different folders, and "Check the engine zip is reproducible" fails when the two zips differ. It lists each differing entry, with the changed lines of small text files |
| MSI | No, by design. Everything but four fields matches | Two MSIs built from the same exe differed in 70 bytes: the ProductCode, the PackageCode (summary field 9), and the created and saved times (fields 12 and 13). The exe and icon inside were byte-identical, and every other table matched once decompiled (`wix msi decompile`) |
| Shell exe | Not checked | A second Rust release build would double the shell job. The PE header's link timestamp and the paths rustc records are the known sources |
| Signed builds of main | No | Each Authenticode signature carries its own timestamp (RFC 3161), so signed files never match byte for byte |

### What made the engine zip differ, and the fix

`.github/scripts/engine_build.py` does each fix. It is stdlib only and runs with the engine's own Python.

- **Compiled files:** pip's own compiled files (`.pyc`) contained the path of its random temp folder (`pip-target-xxxx`) and the install time. Packages now install with `--no-compile`. `engine_build.py finish` then compiles them in one process, in sorted order, as checked-hash pycs that name each file by its path inside the engine (for example `Lib/site-packages/polars/__init__.py`). A checked-hash pyc stays valid whatever time its source file has, so a fixed zip time never makes Python recompile on a user's PC. Python checks the source's hash when it imports the module instead of its time.
- **`direct_url.json`:** the engine's own package recorded the runner's wheel folder here. `finish` removes it, along with its line in `RECORD`.
- **Zip times and order:** 7z stored each file's build-time mtime, in the order the file system listed them. `engine_build.py zip` writes entries in sorted order, with the commit's time (`SOURCE_DATE_EPOCH`, from `git log -1 --format=%ct`) on every entry, MS-DOS attributes and zlib's default level.
- **`engine.json`:** its keys are now in a fixed order.
- **Launchers:** `SOURCE_DATE_EPOCH` also fixes the time pip writes into console-script launchers.
- **Runtime key:** `engine_runtime.py` LAYOUT went to 2 with this change, because every package's `.pyc` changed. Installed engines download the full engine once.

### What the zip still depends on

- **Build Python:** the zip's bytes depend on the build Python's zlib. `setup-python` resolves `3.12` to a patch release (3.12.10 when this was checked). A later patch release with a different zlib can change the compressed bytes without changing any file. The per-entry CRCs that `compare` lists stay the same.
- **Signing:** on main, once signing is set up, signing changes the packages' DLLs after the check runs. The check then proves the unsigned folder is reproducible, and the signatures are the only difference.

### MSI

- **ProductCode:** WiX generates a new one for each build, as a major upgrade needs.
- **PackageCode:** a new one for each build, as Windows Installer requires for each distinct package.
- **Version:** comes from the run number (`0.1.<run>`), so two runs of the same commit differ anyway.

To check that an MSI holds the published exe, extract it (`wix msi decompile -x`) and compare the exe's SHA-256 with `exe_sha256` in `version.json`.
