; Inno Setup script for Seedr Movie Manager
; Compile with:  ISCC.exe installer.iss   (build.bat does this for you)

#define AppName "Seedr Movie Manager"
#define AppExe  "SeedrMovieManager.exe"
#define AppVer  "1.0.0"
; Shortcut icon file. Change this name whenever the icon changes: Windows caches icons by file name,
; so a new name forces it to show the new picture.
#define AppIcon "seedr-icon-v2.ico"

[Setup]
AppId={{B7D3C1A4-6E52-4F0B-9A8D-3C5E1F2A7D90}
AppName={#AppName}
AppVersion={#AppVer}
DefaultDirName={autopf}\SeedrMovieManager
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Installs per-user by default (no admin prompt); user can choose "all users".
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppIcon}
; Makes Windows refresh icons after install so old cached icons don't linger.
ChangesAssociations=yes
OutputDir=installer_output
OutputBaseFilename=SeedrMovieManager_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "dist\SeedrMovieManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "icon.ico"; DestDir: "{app}"; DestName: "{#AppIcon}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppIcon}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppIcon}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
