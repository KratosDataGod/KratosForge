# Legacy names

![KratosForge](brand/kratosforge-wordmark.png)

KratosForge is the app's name everywhere you read it: the window title, the top bar, menus, messages, the docs and the docs site. Installed copies took the name through a normal update. The first time the app starts after that update it shows a one-time announcement; close it with **Enter the forge**, Escape or Enter. To see it again, search for **About the new name** in the command bar (Ctrl+K).

The top bar shows the KratosForge wordmark beside the Wolf Forge emblem, and the release titles on GitHub are **KratosForge (stable)** and **KratosForge (latest build)**.

KratosForge is made by [KratosBI](https://kratosbi.com). The KratosBI logo, the Wolf Forge emblem and the app icon in `docs/brand/`, `app/ui/` and `app/src-tauri/icons/` belong to KratosBI. Interface icons are [Fluent UI System Icons](https://github.com/microsoft/fluentui-system-icons) (MIT).

## Names that stay as they are

Some files, folders, settings and markers keep a legacy name from before KratosForge. Nothing you built moves, and nothing has to be set up again: existing installations, notebooks and workspaces find each other by these names, so they don't change. Where the docs show one, type or look for it exactly as written.

| What | Legacy name | Why it stays |
| --- | --- | --- |
| The engine download and the Windows program name | `fabric-desktop-engine.zip`, `Fabric Desktop` | Installed copies download the engine by this name, and the program name goes with the app identity below. |
| The earlier program file name | `fabric-desktop.exe`, published beside `kratosforge.exe` | Builds from before the name changed update by downloading this name ([the program file](#the-program-file)). |
| The folder on your PC | `%LOCALAPPDATA%\FabricDesktop` | Your notebooks, sign-in, settings and engine are there ([where files live](install.md#where-things-are-kept-on-your-pc)). |
| The Windows app identity and update address | `com.kratosbi.fabricdesktop`, the `fabric-desktop` GitHub releases | Changing either would stop installed copies from updating. |
| Platform workspaces it suggests | `Fabric Desktop Platform - <Environment>` | Existing workspaces are found by this name ([the platform](platform.md)). |
| The central lakehouse | `FabricDesktop_Central` | Every installation in your organization shares it ([Optimize](optimize/README.md)). |
| Cells the app adds to notebooks | `# Fabric Desktop setup`, `# Fabric Desktop proof`, `# Fabric Desktop side by side`, `# Fabric Desktop runner` | The app recognizes its own cells by these first lines, in notebooks already in Fabric too. |
| The Python package, command and settings | `fabric_desktop`, `fabric-desktop`, `FABRIC_DESKTOP_*` variables, the `fabric_desktop_*` MCP tools | Scripts, agents, policies and AI tool setups use them. |

## The program file

The program is `kratosforge.exe`. Before, it was `fabric-desktop.exe`, and an update replaces the file under the name it already has, so copies people saved keep the old name until a build that knows the new one starts.

- **Copies already installed.** The first time a copy saved as `fabric-desktop.exe` starts after an update to a build with the new name, and the update has started cleanly, it renames its own file to `kratosforge.exe` in the same folder. It then points your shortcuts to the old file at the new one: on the Desktop, in the Start menu, and pinned to the taskbar. If any of them can't be changed, the shortcuts and the file go back to `fabric-desktop.exe`, which keeps updating as before. What happened is noted in `%LOCALAPPDATA%\FabricDesktop\exe-rename.json`.
- **Left alone.** A copy on a PC whose organization rolls out updates itself (`"auto_update": false`) keeps the name IT gave it. A folder that already holds a `kratosforge.exe` keeps both files. A copy you renamed yourself keeps your name; any name works.
- **Releases** carry the same file under both names, so builds from before the change keep updating, and a rollback to an earlier build works under either name. Earlier `build-*` releases have only `fabric-desktop.exe`; the app downloads that name when a release has no `kratosforge.exe`.
- **Nothing else moves.** Your data folder (`%LOCALAPPDATA%\FabricDesktop`), the embedded browser's data and the sign-in are found by the app's identity, not by the file name.

## The repository's address

The repository is `github.com/KratosDataGod/KratosForge`. Its earlier address, `github.com/KratosDataGod/fabric-desktop`, forwards to it, so saved links, clones and installed copies' updates keep working. The docs site is [kratosdatagod.github.io/kratosforge](https://kratosdatagod.github.io/kratosforge/); the earlier docs address is not forwarded.
