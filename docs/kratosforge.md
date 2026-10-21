# Fabric Desktop is now KratosForge

![KratosForge](brand/kratosforge-wordmark.png)

The app called Fabric Desktop (and Fabric Desktop by KratosBI) is now **KratosForge**. It is the same app from the same team, KratosBI, and it updates itself to the new name like any other update. The first time it starts after that update it shows a one-time announcement; close it with **Enter the forge**, Escape or Enter. To see it again, search for **About the new name** in the command bar (Ctrl+K).

## What changed

- The name in the window title, the top bar, menus, messages, the docs and the docs site.
- The top bar shows the KratosForge wordmark beside the Wolf Forge emblem.
- The release titles on GitHub: **KratosForge (stable)** and **KratosForge (latest build)**.

## What didn't change

Nothing you built moves, and nothing has to be set up again. These keep their old names on purpose, because existing installations, notebooks and workspaces find each other by them:

| What | Still called | Why it stays |
| --- | --- | --- |
| The download, the program file and the Windows program name | `fabric-desktop.exe`, `fabric-desktop-engine.zip`, "Fabric Desktop" | Updates replace the file people already have, and download links keep working. |
| The folder on your PC | `%LOCALAPPDATA%\FabricDesktop` | Your notebooks, sign-in, settings and engine are there ([where files live](install.md#where-things-are-kept-on-your-pc)). |
| The Windows app identity and update address | `com.kratosbi.fabricdesktop`, the `fabric-desktop` GitHub releases | Changing either would stop installed copies from updating. |
| Platform workspaces it suggests | `Fabric Desktop Platform - <Environment>` | Existing workspaces are found by this name ([the platform](platform.md)). |
| The central lakehouse | `FabricDesktop_Central` | Every installation in your organization shares it ([Optimize](optimize/README.md)). |
| Cells the app adds to notebooks | `# Fabric Desktop setup`, `# Fabric Desktop proof`, `# Fabric Desktop side by side`, `# Fabric Desktop runner` | The app recognizes its own cells by these first lines, in notebooks already in Fabric too. |
| The Python package, command and settings | `fabric_desktop`, `fabric-desktop`, `FABRIC_DESKTOP_*` variables, the `fabric_desktop_*` MCP tools | Scripts, agents, policies and AI tool setups use them. |

## The repository's new address

The repository moved from `github.com/KratosDataGod/fabric-desktop` to `github.com/KratosDataGod/KratosForge`. GitHub forwards the old address, so saved links, clones and installed copies' updates keep working. The docs site moved to [kratosdatagod.github.io/kratosforge](https://kratosdatagod.github.io/kratosforge/); the old docs address is not forwarded.
