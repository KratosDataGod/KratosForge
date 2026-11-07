# Back up and restore

KratosForge keeps some things only on your PC: notebooks you haven't published, Settings, schedules on this PC, run history, proof receipts and the workloads and incidents database. If the PC is lost or replaced, those go with it. A backup is one zip you can keep somewhere safe and restore on any PC.

Your tables in OneLake, and anything you published to Fabric, aren't part of it. They stay in Fabric. A metadata project folder is also not included; keep it in Git.

## Make a backup

The commands run in the engine's Python, the one the app installed. In PowerShell, find it once:

```powershell
$py = Get-ChildItem "$env:LOCALAPPDATA\FabricDesktop\engine\*\python.exe" | Sort-Object LastWriteTime | Select-Object -Last 1
```

Then make the backup:

```powershell
& $py -m fabric_desktop.cli backup --out "$env:USERPROFILE\OneDrive\Backups"
```

It saves `fabric-desktop-backup-<date>-<time>.zip` and says which files had something left out. You can run it while the app is open. Each database is copied through SQLite, so the copy holds every change saved before it started and none half made. If another program holds a database mid-write for more than 10 seconds, no backup is saved and the command says which database; run it again in a moment. A log line still being written is left out. The zip is written under a temporary name and renamed when it's whole, so a backup that stops part way leaves nothing behind. A database SQLite can't read goes into the zip as it is, for support, and doesn't stop the backup; a restore replaces it.

For a [headless agent](agent.md), add `--agent-home <its FABRIC_DESKTOP_HOME>` to include its job queue and alert history.

## What is never in a backup

| Left out | Why |
| --- | --- |
| Your sign-in (`msal_cache.bin`, the embedded Fabric pages' sign-in) | Tokens must not be copied. You sign in again after a restore |
| The running app's link token (`link-port`) | It's made new each time the app starts, and an old one would stop links reaching the running app |
| A Teams alerts webhook address | Anyone with it can post to that channel. Set it again in **Settings > Notifications** |
| Anything that looks like a password, key or token inside a file | Replaced with `[removed]`. The backup command names each file this happened to; if a notebook was one of them, check it after a restore |
| The engine, caches, logs and lock files | The app rebuilds them |

## Restore

1. Close KratosForge, and stop the [headless agent](agent.md) if you restore its files. A restore replaces database files, so it refuses, and changes nothing, while the app, the agent or anything else has one of them open.
2. Check the backup and see what it would do. This changes nothing:

   ```powershell
   & $py -m fabric_desktop.cli restore fabric-desktop-backup-20261017-120000.zip
   ```

   It refuses a zip that is cut short or damaged, whose files changed after it was made, that lists a file twice, that isn't a backup of this version, or that would write anywhere but the app's own folders.
3. Restore it:

   ```powershell
   & $py -m fabric_desktop.cli restore fabric-desktop-backup-20261017-120000.zip --yes
   ```

4. Open the app and sign in. Set the Teams webhook again if you used one.
5. Schedules on this PC come back in **Monitor > KratosForge > Schedules**, and the app adds them to Windows Task Scheduler again as it starts.

A restore adds and replaces files. It writes and checks every file against the backup before it replaces any, so a restore that stops part way leaves the folder as it was. Those files and the copies of what they replace are flushed to disk before the first swap. Every file it replaces is copied first to `%LOCALAPPDATA%\FabricDesktop\restore-safety\<date>-<time>`, so you can put any of them back by hand. A replaced database's `-wal`, `-shm` and `-journal` files are moved there too, beside the database's copy: left in place, they would bring back changes made after the backup. To undo, put the database and those files back together.

These commands are covered by automated tests but haven't been run on a Windows PC yet.

## On a new PC

Install the app ([Install](install.md)), start it once so the engine is installed, close it, then restore as above.
