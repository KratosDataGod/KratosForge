# Redactors: where they differ (EA-028)

This page records the five redactors and how they differ. `sidecar/tests/test_redactor_characterization.py` runs all five on one shared set of secret-shaped inputs and pins the table below, so a change to any of them changes the test as well as this page. Merging them into one is tracked under EA-028 ([AD-5](ARCHITECTURE_DECISIONS.md)); callers move one at a time.

## The five

| Redactor | Used for | Placeholder |
| --- | --- | --- |
| `redact.secrets` | Errors, messages and most engine text (about 90 importers) | `[removed]` |
| `redact.for_support` | Support bundles and text a user pastes into an issue. It runs `secrets`, then also hides emails, tenant and SharePoint names, user names in paths, and GUIDs | `[removed]`, `[email]`, `[id]`, and similar |
| `redact.walk` | Log lines (`logs._redacted`) and integration results (`integrations.core.redact_result`). It runs `secrets` on every string and blanks any field whose name looks like a secret | `[removed]` |
| `git/secrets.redact` | Git messages. It is `redact.secrets`. The secret scan before a commit (`scan_text`) keeps its own detection patterns and shows a finding's first characters followed by `…[hidden]` | `[removed]` |
| `livetest.scrub` | Live-test reports. It runs `secrets` and cuts the text to 2,000 characters | `[removed]` |

## What each one does with the secret

The values are: removed; masked (only the first characters stay, so a person can find the secret); kept. Every redactor now removes every input below, including a URL's user name with no password, where Git remotes carry a token.

| Input | secrets | for_support | walk | git | livetest |
| --- | --- | --- | --- | --- | --- |
| `Authorization: Bearer` header | removed | removed | removed | removed | removed |
| `Authorization: Basic` header | removed | removed | removed | removed | removed |
| JWT (`eyJ….eyJ….sig`) | removed | removed | removed | removed | removed |
| SAS `sig=` | removed | removed | removed | removed | removed |
| URL `user:password@` | removed | removed | removed | removed | removed |
| URL `user@` with no password | removed | removed | removed | removed | removed |
| Teams webhook (`*.webhook.office.com`) | removed | removed | removed | removed | removed |
| Legacy webhook (`outlook.office.com/webhook`) | removed | removed | removed | removed | removed |
| GitHub token | removed | removed | removed | removed | removed |
| Microsoft Entra client secret | removed | removed | removed | removed | removed |
| AWS access key | removed | removed | removed | removed | removed |
| Slack token | removed | removed | removed | removed | removed |
| Databricks token | removed | removed | removed | removed | removed |
| `sk-` API key | removed | removed | removed | removed | removed |
| PEM private key body | removed | removed | removed | removed | removed |
| Connection-string `Password=` | removed | removed | removed | removed | removed |
| Storage `AccountKey=` | removed | removed | removed | removed | removed |
| git `extraheader` | removed | removed | removed | removed | removed |
| Quoted `'password': '…'` | removed | removed | removed | removed | removed |
| Bare `token=` in a URL | removed | removed | removed | removed | removed |

## Where they differ

- **One pattern set for messages.** Git messages use `redact.secrets`, so every redactor above removes what `redact.secrets` removes. `git/secrets.py` keeps its own patterns only to detect secrets in files before a commit.
- **Remove or mask.** Messages remove the match. Only a pre-commit finding shows the first characters, so a person can find the secret it warns about.
- **Placeholders.** Every redactor writes `[removed]`; `for_support` also writes `[email]`, `[id]` and similar.
- **The walkers are already one.** `logs._redacted` and `integrations.core.redact_result` both call `redact.walk`, and a test checks that they give the same result.
