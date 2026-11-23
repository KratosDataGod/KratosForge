# Mutation lane B, batches 263-280: FIRST score of never-scored modules (Chris 2026-11-20 20:14Z: "you are only at 54% utlization. let's ramp up production")

Worktree: /home/user/wt-h (branch claude/project-thread-0549ny-mut263, main ea988e99). SHARED by 3 forks.
- Do NOT run any git command that changes state (no add/commit/checkout/stash/merge). Read-only git is fine.
- Only CREATE new test files in sidecar/tests/. Never edit engine code or existing tests. Never edit docs/quality/mutation-testing.md.
- New test file name: sidecar/tests/test_<module path with / as _, no .py>_mutation.py; if that exists, use _mutation2.py.
- Write each batch's doc section to /tmp/claude-0/-home-user/f122e407-1dbc-505e-ad02-2b1a9a9b756f/scratchpad/m251/doc-<batch>.md, in the format of batch 140 (a first score; find it in docs/quality/mutation-testing.md): intro saying the module had no score before and what it does, run paragraph (main ea988e99, 2026-11-21, cap 150, seed 7, --kinds all, DuckDB 1.5.6), Results table (same columns as batch 140), "What the survivors were, and the tests that now kill them", "Left" table with why each is equivalent (cite earlier batch if it said so). Plain, short sentences.
- Method: cd sidecar; python tools/mutate.py MODULE --cap 150 --seed 7 --kinds all --out /tmp/.../m251/<batch>-before.json -- <tests relative to sidecar/> (find the module's existing tests with grep; deselect/skip tests reading docs/ examples/ app/ and say so). Then write tests killing every non-equivalent survivor, then run --only <survivor ids> for the after figure (or a full rerun).
- Real bug found: pin it with a strict xfail naming the bug, do not fix engine code, and report it.
- Before finishing: new tests pass on duckdb 1.5.6 AND with PYTHONPATH=/tmp/claude-0/duckdb2:$PWD (DuckDB 2); set LOCALAPPDATA=/tmp/claude-0/la; pass in a sidecar-only copy and against ast.unparse of the module; `ruff check` clean; run `python .github/scripts/secret_scan.py check <your files>` FROM THE REPO ROOT and paste its real output (must say "No new secrets"); fake secret-like strings get `# pragma: allowlist secret (test value)`.
- BATCH SHELL WORK: you have a turn limit. Combine commands; run mutate.py in the background or for several modules in one shell call where possible.
- If you get a 429 / usage-limit error, stop and report it.
- If a module's leftovers were already all explained as equivalent and today's run kills everything else, write the section anyway with "no new tests needed".
- Final report (short): per batch: module, before/after killed/total and %, new test file, survivors left and why, any bug, secret-scan output line, test counts on both DuckDB versions.
- Chris 2026-11-21 00:30Z: "ok, we are at 82% - lets get to 99% by tomorrow at 8am".
- Only 3 forks share this 4-core box; the last round OOM-killed processes with 4. Run ONE mutate.py at a time per fork. Use the wrapper /tmp/claude-0/-home-user/f122e407-1dbc-505e-ad02-2b1a9a9b756f/scratchpad/m231/mut600.py (600 s timeout) if tests are slow.
- False-kill gotcha: a mutant can leave a too-deep pytest tmp dir and then every later mutant "dies". Watch for a late streak of kills at ~identical seconds; spot-check kills by hand; check `df -h /tmp` stays healthy and clean up your temp dirs.
- Each mutate run: if the module has more than 150 mutants, 150 are sampled (say so).
- If you finish early, report back; don't start modules outside your list.
