# Shell test coverage

Line coverage of the Rust shell (`app/src-tauri`) by its unit tests, per file. This is a measurement, not a gate: nothing fails on these numbers. It shows where the tests are thin, so new tests go where they count.

## How it was measured

```
cd app/src-tauri
cargo llvm-cov --locked --summary-only
```

- Tool: cargo-llvm-cov 0.9.1, with the `llvm-tools-preview` component of the pinned Rust release (1.99.1).
- Date: 2026-11-16, on Linux (x86_64).
- **Linux only.** Code behind `#[cfg(windows)]` isn't compiled on Linux, so it is neither counted nor covered here. That includes Authenticode in `signature.rs`, the permission reads in `policy_trust.rs`, the Windows proxy lookup in `win_proxy.rs`, and the window and process code in `main.rs`. The shell job in CI runs the same tests on Windows (`cargo test --release`) on every pull request. Windows coverage is measured separately, below.
- "Before" is main before the shell test work; "after" adds its tests (signature, policy trust and update rules first, then the engine bridge and the refused links, then the rules split out of `main.rs` and `folder_pick.rs`).

## Line coverage by file

| File | Lines (Linux) | Covered before | Covered after |
| --- | ---: | ---: | ---: |
| `crash.rs` | 318 | 96.9% | 96.9% |
| `deep_link.rs` | 679 | 96.9% | 96.9% |
| `disk_space.rs` | 59 | 94.9% | 94.9% |
| `engine_folder.rs` | 178 | 79.2% | 79.2% |
| `engine_patch.rs` | 259 | 96.5% | 96.5% |
| `exe_name.rs` | 143 | 97.9% | 97.9% |
| `folder_pick.rs` | 12 → 39 | 75.0% | 92.3% |
| `main.rs` | 2,566 → 3,205 | 35.1% | 54.6% |
| `policy_file.rs` | 33 → 59 | 100% | 100% |
| `policy_trust.rs` | 60 → 89 | 95.0% | 100% |
| `replies.rs` | 94 | 100% | 100% |
| `signature.rs` | 30 → 55 | 90.0% | 100% |
| `ssms_link.rs` | 153 | 100% | 100% |
| `update_guard.rs` | 546 → 587 | 96.3% | 96.4% |
| `vscode_link.rs` | 133 | 97.0% | 97.0% |
| `watchdog.rs` | 70 | 97.1% | 97.1% |
| `win_proxy.rs` | 284 | 81.0% | 81.0% |
| **Total** | 5,617 → 6,404 | 67.3% | 74.7% |

A file's line count grows with its own tests, since the tests sit in the same file.

## What the numbers mean

- **`main.rs` is the gap.** Most of its untested lines are Tauri commands, window and tray setup, and starting and talking to the engine process. They run only inside the app, with a window and a real engine. The parts that are plain logic are tested: replies, the sidecar log's keep-and-trim rules, sign-in cookies, engine time limits, and the links and files the page may not open. So is the bridge to the engine, against a stand-in engine (the test binary run again): each call getting its own answer, an engine error or crash coming back with the end of its log, a stopped or stuck engine being ended, and no start while a crash loop is waited out. What the `rpc` command around that bridge decides after each call (whether the engine crashed and counts toward a crash loop, whether a crash loop is over, whether the next call starts a fresh engine) is in `call_outcome`, which is tested; `rpc` itself needs the app's state, so it isn't reached.
- **Startup rules split out of `main.rs`, and tested:** which engine runs and which engine build may install (`pick_engine`, `engine_build_allowed`), which release channel a PC follows (`channel_tag`), whether IT's engine folder is used (`mirror_from`), whether the channel's release is accepted (`release_rule`), where engine files come from (`release_url`, `mirror_host`, `setting`), the `--updated-to` argument (`updated_to`), the message when the engine's Python won't start (`python_failed_for`), the updater's log and error (`updater_tail`, `updater_failure`), and a scheduled run's interpreter (`schedule_interpreter`). Each keeps a thin caller that reads the files, settings or process it needs, so the app behaves as before. What remains untested in `main.rs` is that thin layer and the code that needs a window or a running app: Tauri commands, window and tray setup, `rpc`, and starting the engine.
- **The security-relevant files are fully covered on Linux:** `signature.rs`, `policy_trust.rs` and `policy_file.rs`. On Linux, though, that covers only the decisions:
  - which signer may update which build;
  - which permission entries count against a path;
  - which copy of the policy applies.

  The Windows calls that feed those decisions (`WinVerifyTrust`, `GetNamedSecurityInfoW`) run only in the Windows shell job's tests, and aren't measured.
- **`watch_sidecar_log`** waits 30 seconds before its first check, so no unit test runs it. The trimming it calls is tested directly.
- **`update_guard.rs`'s remaining lines** are the message for a swap whose rollback also fails, and the 250 ms retry loop on a locked file. The rename can't be made to fail at that point without changing the code to take the rename as a parameter, as `restore_with` already does.

## Windows

The Shell coverage (Windows) workflow (`.github/workflows/shell-coverage.yml`) runs the same tests with cargo-llvm-cov on the Windows runner, so the `#[cfg(windows)]` code is counted too. It runs once a day and by hand (Actions, then Run workflow), never on a pull request, so it doesn't hold Windows runners that pull request CI needs. Each run puts the per-file table in its summary, and keeps the table and an `lcov.info` file as the `shell-coverage-windows` artifact for 30 days. Like the numbers above, it's advisory: nothing fails on them.

## Next

- The plain rules in `main.rs`'s startup and engine handling have been split out and tested. What is left needs the app running (a window, the Tauri state or a real engine), so more unit tests there would mean bigger changes to how the app is put together, not more small splits.
