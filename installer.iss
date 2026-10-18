#define AppName "NovaCraft Launcher"
#define AppVersion "0.1.0"
#define AppExeName "NovaCraftLauncher.exe"

[Setup]
AppId={{A4B5A7C3-3E9C-4B47-9F9B-NOVACRAFT01}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\NovaCraft Launcher
DefaultGroupName=NovaCraft Launcher
OutputDir=dist
OutputBaseFilename=NovaCraft-Launcher-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExeName}

[Files]
Source: "dist\NovaCraftLauncher.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\NovaCraft Launcher"; Filename: "{app}\{#AppExeName}"
Name: "{commondesktop}\NovaCraft Launcher"; Filename: "{app}\{#AppExeName}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Запустить NovaCraft Launcher"; Flags: nowait postinstall skipifsilent
