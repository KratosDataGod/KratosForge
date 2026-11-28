# Mutation testing, batch 524

This batch scores one module for the first time: `keyed_fingerprint.py`. It ran with `--kinds all`, but the module has no `<`, `<=`, `>` or `>=` to swap, so there are no boundary mutants.
- The module makes keyed fingerprints. Each one is a short HMAC-SHA256 of the text, under a random key that stays on this PC. It covers where the key is kept, how the key file is made so that two processes starting together end up with one key, and the key used for this run alone when the folder can't hold one (`keyed_fingerprint.py`).

The run was at `main` be5ef815c (2026-11-28), using `sidecar/tools/mutate.py` with the same method as earlier batches (`--cap 150 --seed 7 --kinds all`). Every mutant ran. Test files were given to the tool as paths relative to `sidecar/`. The tests passed once on the unmutated copy first. Each pytest session got a new temporary folder, which was removed when the session ended, and no folder 40 or more levels deep was left. No `-k` filter was used. No mutant's run timed out.

Three tests in `test_delete_my_data_stores.py` read `docs/enterprise-privacy.md`. The tool's copy of `sidecar/` has no `docs/`, so those three were deselected with `--deselect`. They are `test_every_store_in_the_inventory_is_removed_or_kept_for_a_stated_reason`, `test_this_file_names_no_store_the_inventory_doesnt_have` and `test_the_audit_trail_is_kept_as_the_privacy_page_says`.

Before the "before" run, the tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, so the run can't score a false 100%.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `keyed_fingerprint.py` | test_integrations_ossie_redaction, test_backup, test_delete_my_data_stores (+ test_keyed_fingerprint_mut524) | 29 of 29 (29 + 0) | 19 / 10 | **65.5%** | 19/29 → 26/29 | none | 26 / 3 | **89.7%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- The key is kept under `LOCALAPPDATA`, in `FabricDesktop/keys/fingerprint.key`. Without `LOCALAPPDATA` it goes under the home folder's `.local/share`. The tests' conftest points `key_path` at a tmp file for the whole session, so these tests read the real `key_path` from a fresh copy of the module.
- A key is 32 bytes. A 32-byte file is read as the key, and a 33-byte file is not.
- The key file is made with mode `0o600`.
- The key's folders are made however deep they go, and a key made into a folder that already exists is kept.
- If another process repairs the same empty key file just after this one, its key is the one used, and no temporary file is left.
- These tests are in `test_keyed_fingerprint_mut524.py`.

### Mutants left, and why

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `keyed_fingerprint.py` | 47 | `token_hex(8)` to `token_hex(9)` for the temporary file's name | Equivalent: the temporary name only has to be unique beside the key, and it's removed either way. |
| `keyed_fingerprint.py` | 48 | `getattr(os, "O_BINARY", 0)` to a default of `1` | Equivalent: Windows has `O_BINARY`, so the default isn't used there. Elsewhere `1` is `O_WRONLY`, which the flags already include. |
| `keyed_fingerprint.py` | 67 | `lru_cache(maxsize=4)` to `maxsize=5` | Equivalent: the cache size changes only how often a key file is read again, not which key is used. |
