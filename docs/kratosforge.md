# Legacy names

![KratosForge](brand/kratosforge-wordmark.png)

KratosForge is the app's name everywhere you read it: the window title, the top bar, menus, messages, the docs and the docs site. Installed copies took the name through a normal update. The first time the app starts after that update it shows a one-time announcement; close it with **Enter the forge**, Escape or Enter. To see it again, search for **About the new name** in the command bar (Ctrl+K).

The top bar shows the KratosForge wordmark beside the Wolf Forge emblem, and the release titles on GitHub are **KratosForge (stable)** and **KratosForge (latest build)**.

## Names that stay as they are

Some files, folders, settings and markers keep a legacy name from before KratosForge. Nothing you built moves, and nothing has to be set up again: existing installations, notebooks and workspaces find each other by these names, so they don't change. Where the docs show one, type or look for it exactly as written.

| What | Legacy name | Why it stays |
| --- | --- | --- |
| The download, the program file and the Windows program name | `fabric-desktop.exe`, `fabric-desktop-engine.zip`, `Fabric Desktop` | Updates replace the file people already have, and download links keep working. |
| The folder on your PC | `%LOCALAPPDATA%\FabricDesktop` | Your notebooks, sign-in, settings and engine are there ([where files live](install.md#where-things-are-kept-on-your-pc)). |
| The Windows app identity and update address | `com.kratosbi.fabricdesktop`, the `fabric-desktop` GitHub releases | Changing either would stop installed copies from updating. |
| Platform workspaces it suggests | `Fabric Desktop Platform - <Environment>` | Existing workspaces are found by this name ([the platform](platform.md)). |
| The central lakehouse | `FabricDesktop_Central` | Every installation in your organization shares it ([Optimize](optimize/README.md)). |
| Cells the app adds to notebooks | `# Fabric Desktop setup`, `# Fabric Desktop proof`, `# Fabric Desktop side by side`, `# Fabric Desktop runner` | The app recognizes its own cells by these first lines, in notebooks already in Fabric too. |
| The Python package, command and settings | `fabric_desktop`, `fabric-desktop`, `FABRIC_DESKTOP_*` variables, the `fabric_desktop_*` MCP tools | Scripts, agents, policies and AI tool setups use them. |

## The repository's address

The repository is `github.com/KratosDataGod/KratosForge`. Its earlier address, `github.com/KratosDataGod/fabric-desktop`, forwards to it, so saved links, clones and installed copies' updates keep working. The docs site is [kratosdatagod.github.io/kratosforge](https://kratosdatagod.github.io/kratosforge/); the earlier docs address is not forwarded.
