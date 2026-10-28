# Contributing to KratosForge

Thanks for helping. There are several ways in, and most don't need code.

| You want to | Do this |
| --- | --- |
| Suggest a feature | Post it in the [Ideas forum](https://github.com/KratosDataGod/KratosForge/discussions/categories/ideas), or vote for an existing one. How ideas are prioritized: [docs/ideas.md](docs/ideas.md). |
| Report a bug | **Report a problem** in the app, then paste it into a [bug report](https://github.com/KratosDataGod/KratosForge/issues/new/choose). Check [troubleshooting](docs/troubleshooting.md) first. |
| Ask a question | [Q&A](https://github.com/KratosDataGod/KratosForge/discussions/categories/q-a) |
| Try new builds | Switch to the **Preview** channel in **Settings > Updates**, and tick "Would you try an early version?" on ideas you care about. |
| Fix the docs | Edit the file under `docs/` and open a pull request. Small fixes are always welcome. To preview the site, `pip install zensical` then `zensical serve` in the repository root and open http://localhost:8000. Links are checked on every pull request. |
| Change the code | Read on. For anything bigger than a small fix, start from an idea or issue so the approach is agreed first. |

## Set up

You need Windows for the desktop app. The Python engine and its tests also run on Linux and macOS.

```powershell
git clone https://github.com/KratosDataGod/KratosForge
cd fabric-desktop\sidecar
python -m pip install -e ".[dev]"     # Python 3.11 or later
python -m pytest -q                   # every test runs without Fabric
python -m ruff check .               # the lint CI runs: unused or undefined names, syntax errors
python ../.github/scripts/mypy_ratchet.py   # type check: fails on new errors (pip install mypy); --update after renaming a file or moving a pin
```

To run the desktop app from source, install Rust and the [Tauri prerequisites](https://v2.tauri.app/start/prerequisites/), then:

```powershell
$env:FABRIC_DESKTOP_PYTHON = (Get-Command python).Source
cd app\src-tauri
cargo run
```

`FABRIC_DESKTOP_PYTHON` must be the full path to `python.exe`. A debug build (`cargo run`) without it falls back to `python` on `PATH`; a release build (`cargo build --release`, `cargo tauri build` or CI) never does, and refuses a bare name such as `python`, so set the full path to test one with your own Python. The browser tests read `FABRIC_DESKTOP_PYTHON` themselves to start the sidecar and don't go through the shell.

The UI is plain HTML and JavaScript in `app/ui/` with no build step; reload the window to see a change. How the pieces fit is in [docs/architecture.md](docs/architecture.md).

## Rules for changes

- **Tests pass.** `python -m pytest -q` in `sidecar/` and `node --test app/tests/ui.test.cjs` before you push. CI also builds the exe and the engine on Windows, and runs the shell's Rust tests there (`cargo test --release --locked` in `app/src-tauri`; on Linux they need the GTK and WebKitGTK development packages). Pages with browser tests (`app/tests/browser/`, for example the SQL query page) open the real UI in Chromium with the shell stubbed: the SQL and Optimize pages also start the real Python engine, so install the sidecar first. Install Playwright 1.56.1, the version CI uses (`npm install -g playwright@1.56.1`, then `npx playwright install chromium`), and run `node --test --test-concurrency=1 "app/tests/browser/*.test.cjs"` with `NODE_PATH` set to `npm root -g`. CI runs them in the `ui-browser` job. `ui.test.cjs` also fails on a new raw HTML sink in `app/ui` (`innerHTML`, `insertAdjacentHTML`, `document.write`, `srcdoc` and the like): build the element with `h()`, or explain in the test's reviewed list why the new use is safe.
- **SQL dialect changes update the T-SQL contract cases.** A change to the compatibility check, Format SQL or the Spark SQL translation adds or updates a case in `sidecar/tests/tsql_contract/cases.jsonl`; how, and how the scheduled live job is turned on, is in [docs/live-testing.md](docs/live-testing.md#t-sql-contract-cases).
- **Package versions are pinned.** The engine ships the versions in `sidecar/constraints.txt`. To move a package, change it there in its own pull request so CI tests it first. Install with `python -m pip install -e ".[dev]" -c constraints.txt` to test what users get.
- **New UI goes where people already look.** Read [docs/ui-guidelines.md](docs/ui-guidelines.md): register pages and commands in `features.js`, keep the left rail at 8 items or fewer, put new features in an existing page.
- **One visual identity.** Illustration, animation, onboarding and achievement visuals follow [docs/design/KRATOSFORGE_VISUAL_STYLE_GUIDE.md](docs/design/KRATOSFORGE_VISUAL_STYLE_GUIDE.md): answer its eight checks in the pull request, add new images to the [asset manifest](docs/design/assets.md), and give every effect an event in the [catalog](docs/design/animation-events.md).
- **Untested on a tenant means Preview.** A feature that hasn't run against a live Fabric tenant is added to `PREVIEW_FEATURES` and shows a Preview tag.
- **Docs in the same pull request.** A new feature updates [docs/user-guide.md](docs/user-guide.md) (and the notebooks or metadata guide if it belongs there), and its page's **Learn more** link points at that section.
- **Permissions in one place.** A change that asks for a new scope (`sidecar/fabric_desktop/auth.py`) or needs a new agent role adds it to [docs/permissions.md](docs/permissions.md) in alphabetical order, in the same pull request.
- **Plain words.** Errors and labels say what happened and what to do, in words people use. Errors go through `friendlyError` in `activity.js`.
- **No silent failures.** In `sidecar/fabric_desktop`, a broad handler (`except:`, `except Exception` or `except BaseException`) that only passes, continues or returns a constant fails `tests/test_silent_except.py`. Log it (`_log = logging.getLogger(__name__)`, with the exception passed through `redact.describe` so no token reaches the log), catch a narrower exception, or re-raise. Where silence really is right, such as best-effort cleanup, end the `except` line with `# silent-ok: <reason>`; the reason is required and reviewed.
- **No secrets.** Never commit tokens, client secrets, tenant data or real connection strings, including in tests and screenshots.

## Pull requests

1. Branch from `main`.
2. Keep the change focused; one feature or fix per pull request.
3. Describe what a user sees before and after, and how you tested it, using the sections of the pull request template. Screenshots help for UI changes.
4. Link the idea or issue it comes from.

Merges to `main` go to the **Preview** channel straight away. A maintainer moves a build to **Stable** with the **Promote to stable** workflow once it has been tried on Preview. The whole path, including how parallel pull requests are integrated, is in [docs/release-management/README.md](docs/release-management/README.md).
