# Redactors: where they differ (EA-028)

This page records the five redactors and how they differ. `sidecar/tests/test_redactor_characterization.py` runs all five on one shared set of secret-shaped inputs and pins what each does today, so a change to any of them changes the test. Nothing here changes a redactor. Merging them into one is tracked under EA-028 ([AD-5](ARCHITECTURE_DECISIONS.md)).

## The five

| Redactor | Used for | Placeholder |
| --- | --- | --- |
| `redact.secrets` | Errors, messages and most engine text (about 90 importers) | `[removed]` |
| `redact.for_support` | Support bundles and text a user pastes into an issue. It runs `secrets`, then also hides emails, tenant and SharePoint names, user names in paths, and GUIDs | `[removed]`, `[email]`, `[id]`, and similar |
| `redact.walk` | Log lines (`logs._redacted`) and integration results (`integrations.core.redact_result`). It runs `secrets` on every string and blanks any field whose name looks like a secret | `[removed]` |
| `git/secrets.redact` | Git messages and the secret scan before a commit. It has its own patterns and masks a match instead of removing it | `***`, or the first characters followed by `…[hidden]` |
| `livetest.scrub` | Live-test reports. It runs `secrets` and cuts the text to 2,000 characters | `[removed]` |

## Where they differ

- **Two pattern sets.** `redact.py` and `git/secrets.py` each have their own patterns.
- **Remove or mask.** `git/secrets` keeps the first characters of most matches, so a person can find the secret it warns about. Every other redactor removes the match.
- **Placeholders.** Most redactors write `[removed]`; git writes `***` or `…[hidden]`.
- **The walkers are already one.** `logs._redacted` and `integrations.core.redact_result` both call `redact.walk`, and a test checks that they give the same result.
