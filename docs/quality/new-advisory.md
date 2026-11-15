# When a new advisory turns the audit red

The `audit` job in `ci.yml` fails when a lock file that ships names a package with a known vulnerability. It runs on every pull request and on every push to main. The checks themselves are in `.github/actions/dependency-audit`. `.github/workflows/dependency-audit.yml` also runs them against main every 6 hours.

A new advisory therefore shows up in two ways:

- **The scheduled run.** A red run opens the issue labelled `dependency-audit`, or comments on it if it's already open. The next green run closes it.
- **Pull requests.** Every pull request's audit job goes red at once, whatever the pull request changes, because the advisory is in main's locks, not in the pull request.

The PR audit gate stays as it is. Don't skip, soften or `continue-on-error` it to get a merge through.

## 1. Read what failed

The failed step's log names each advisory, the package and version, and the line that would let it through. `audit_exceptions.py hint` writes these for pip and cargo, and the npm check writes them for npm. Note:

- which lock file it is in (the engine's `sidecar/constraints.txt`, a Databricks Connect lock, a UI or extension `package-lock.json`, or `app/src-tauri/Cargo.lock`);
- whether a fixed version exists.

## 2. Move the package alone

If a fixed version exists, open one pull request that moves only that package, and nothing else. CONTRIBUTING has the rule: a package moves in its own pull request so CI tests the new version first. Keep it small so Quality and Release management can clear it fast, since every other pull request waits on it.

- **Engine packages (pip).**
  - Change the version in `sidecar/constraints.txt`.
  - Regenerate every hashed lock that pins it, using the `uv pip compile` command in each file's header: `sidecar/engine-requirements.txt` for shipped packages, and `.github/requirements/test-linux.txt`, `test-windows.txt` and `lint.txt`.
  - `tests/test_toolchain_pins.py` and `tests/test_engine_requirements.py` fail if a lock and `constraints.txt` disagree.
- **Databricks Connect locks.** Regenerate the affected `sidecar/fabric_desktop/engines/dbconnect_locks/*.txt` with the command in its header.
- **npm (UI bundles, VS Code extension).**
  - Move just that package in the lock (`npm update <name> --package-lock-only` in its folder, or an `overrides` entry for a nested dependency).
  - Keep every package's `sha512` integrity; `tests/test_toolchain_pins.py` checks it.
  - If the package goes into a committed bundle (`codemirror.js`, `vega-frame.js`), rebuild it with the folder's `npm run build`, since the app ships the built file.
- **Rust crates.** Run `cargo update -p <crate> --precise <version>` in `app/src-tauri`.

Hashes are never written by hand: a lock is always regenerated with the tool its header names.

If no fixed version exists yet, or the advisory doesn't reach anything the app runs, the other way through is a line in `.github/audit-exceptions.txt`:

- It names the advisory, the date it lapses (90 days at most) and why it's accepted.
- It's a security decision, so it needs a Security read.
- When it lapses, the audit fails again, so someone decides again.

## 3. Merge, then bring main into open pull requests

Release management merges the fix as soon as it's green with a Quality PASS, ahead of the batch. Once it's on main:

- Each open pull request's owner merges main into their branch. Use a merge commit: never rebase or force-push someone else's branch.
- The pull request's audit job then runs against the fixed locks.
- Don't re-run an audit job without bringing main in: it will fail the same way.

The next scheduled run, or a hand run from Actions > Dependency audit > Run workflow, closes the `dependency-audit` issue.
