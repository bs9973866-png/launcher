#define AppName "NovaCraft Launcher"
#define AppVersion "0.2.0"
#define AppExeName "NovaCraftLauncher.exe"

[Setup]
AppId={{7B9A6D20-6D6C-43B1-89F4-5CF6C90A11AC}
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
WizardStyle=modern

[Files]
Source: "dist\NovaCraftLauncher.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\NovaCraft Launcher"; Filename: "{app}\{#AppExeName}"
Name: "{commondesktop}\NovaCraft Launcher"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные параметры:"; Flags: unchecked

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Запустить NovaCraft Launcher"; Flags: nowait postinstall skipifsilent
