# Redactors: where they differ (EA-028)

This page records what five redactors hide today, run on one shared set of secret-shaped inputs. `sidecar/tests/test_redactor_characterization.py` pins the same table, so a change to any of the five changes the test as well as this page. Nothing here changes a redactor. Merging them into one is tracked under EA-028 ([AD-5](ARCHITECTURE_DECISIONS.md)).

## The five

| Redactor | Used for | Placeholder |
| --- | --- | --- |
| `redact.secrets` | Errors, messages and most engine text (about 90 importers) | `[removed]` |
| `redact.for_support` | Support bundles and text a user pastes into an issue. It runs `secrets`, then also hides emails, tenant and SharePoint names, user names in paths, and GUIDs | `[removed]`, `[email]`, `[id]`, and similar |
| `redact.walk` | Log lines (`logs._redacted`) and integration results (`integrations.core.redact_result`). It runs `secrets` on every string and blanks any field whose name looks like a secret | `[removed]` |
| `git/secrets.redact` | Git messages and the secret scan before a commit. It has its own patterns and masks a match instead of removing it | `***`, or the first characters followed by `…[hidden]` |
| `livetest.scrub` | Live-test reports. It runs `secrets` and cuts the text to 2,000 characters | `[removed]` |

## What each one does with the secret

The values are: removed; masked (only the first characters stay); kept (the secret is still in the text).

| Input | secrets | for_support | walk | git | livetest |
| --- | --- | --- | --- | --- | --- |
| `Authorization: Bearer` header | removed | removed | removed | removed | removed |
| `Authorization: Basic` header | removed | removed | removed | removed | removed |
| JWT (`eyJ….eyJ….sig`) | removed | removed | removed | masked | removed |
| SAS `sig=` | removed | removed | removed | masked | removed |
| URL `user:password@` | removed | removed | removed | removed | removed |
| URL `user@` with no password | kept | removed (as an email) | kept | removed | kept |
| Teams webhook (`*.webhook.office.com`) | removed | removed | removed | removed | removed |
| Legacy webhook (`outlook.office.com/webhook`) | removed | removed | removed | kept | removed |
| GitHub token | removed | removed | removed | masked | removed |
| Microsoft Entra client secret | removed | removed | removed | masked | removed |
| AWS access key | removed | removed | removed | masked | removed |
| Slack token | removed | removed | removed | masked | removed |
| Databricks token | removed | removed | removed | masked | removed |
| `sk-` API key | kept | kept | kept | masked | kept |
| PEM private key body | removed | removed | removed | kept | removed |
| Connection-string `Password=` | removed | removed | removed | masked | removed |
| Storage `AccountKey=` | removed | removed | removed | masked | removed |
| git `extraheader` | removed | removed | removed | removed | removed |
| Quoted `'password': '…'` | removed | removed | removed | kept | removed |
| Bare `token=` in a URL | removed | removed | removed | kept | removed |

## Where they differ

- **Two pattern sets.** `redact.py` and `git/secrets.py` each have their own patterns. Each one catches something the other misses: `sk-` keys and a URL user name alone are caught only by git, while a PEM key body, legacy webhooks, quoted passwords and a bare `token=` are caught only by `redact.py`.
- **Remove or mask.** `git/secrets` keeps the first characters of most matches, so a person can find the secret it warns about. Every other redactor removes the match.
- **Placeholders.** Most redactors write `[removed]`; git writes `***` or `…[hidden]`.
- **The walkers are already one.** `logs._redacted` and `integrations.core.redact_result` both call `redact.walk`, and a test checks that they give the same result.
