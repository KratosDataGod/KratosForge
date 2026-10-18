# Release history

Every Stable promotion, newest first. Preview moves on every merge to `main`, so it isn't listed build by build; `git log --first-parent main` is its history. How a promotion is done is in [release management](README.md#promoting-to-stable).

Release assets are replaced in place on each promotion, so only the newest build of each channel can be downloaded. The checksums below identify the files that were promoted.

| Stable since | Commit | Last PR in it | Changes since previous Stable | Checks at promotion | Notes |
| --- | --- | --- | --- | --- | --- |
| 2026-10-17 22:56 UTC | `9715e28` | #132 What's new history | 52 merges to `main` (merge round 4: #118, #120 to #122, #124, #125, #127 to #130, #132 to #134, and earlier) | `main` CI green; `version.json` checksums equal the asset digests (exe `e5d97aff…`, engine `37253aec…`); engine `client_id` empty | Current Stable. Includes the Invalid Date fix (#122) |
| 2026-10-17 14:40 UTC | `76bf48c` | | Builds after `698dcd5`, including no-app-registration sign-in (#42) | Promoted on Chris's decision card | Fixed issue #96: Stable users saw the "Set up sign-in" app-ID dialog because the previous Stable predated #42 |
| 2026-10-16 12:29 UTC | `698dcd5` | | First Stable release | | Predates #42 |

## Preview right now

| Since | Commit | What's on it that Stable doesn't have |
| --- | --- | --- |
| 2026-10-18 09:46 UTC | `68198a4` | The hardening audit batch (#138, #152, #154 to #159, #162, #179, #180, #157), #140 and #184 since `9715e28`, besides #135 and #136. Checksums verified: engine `44eefec5…`; engine `client_id` empty |
