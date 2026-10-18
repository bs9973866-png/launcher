# NovaCraft Launcher

Standalone Windows Minecraft launcher built from a clean repository.

## Included

- Ely.by login through the Ely.by authentication server.
- Vanilla Minecraft versions from the official version manifest.
- Fabric, Forge, NeoForge and Quilt installation through `minecraft-launcher-lib`.
- Modrinth search and one-click mod installation.
- CurseForge search/download support when a valid CurseForge API key is supplied in Settings.
- Automatic Java runtime handling provided by `minecraft-launcher-lib` where the Minecraft version declares a runtime.
- Custom dark UI and generated NovaCraft launcher icon.
- GitHub Actions builds a portable EXE and an Inno Setup installer.

The launcher does not include or distribute Minecraft game files in the repository. Game files are downloaded when the user installs a selected version.

## Build

The GitHub Actions workflow builds `NovaCraft-Launcher-Setup.exe` and a portable `NovaCraftLauncher.exe` on Windows.

## APIs

Ely.by authentication is implemented against the documented Ely.by auth server. Modrinth uses its public API. CurseForge requires an API key supplied by the user.
