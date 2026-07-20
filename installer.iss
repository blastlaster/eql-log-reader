; EQL Log Reader -- Inno Setup installer script
; ================================================
; Packages dist\EQL Log Reader\* (built by build_exe.bat / eql_suite.spec)
; into Output\EQL-Log-Reader-Setup.exe -- the single file players
; download and run. No Python required on their machine.
;
; Build with make_installer.bat, or open this file in the Inno Setup
; IDE and click Compile.
;
; Bump MyAppVersion below each release.

#define MyAppName "EQL Log Reader"
#define MyAppVersion "1.8"
#define MyAppPublisher "EQL Log Reader"
#define MyAppExeName "eql_launcher.exe"

[Setup]
; Fixed AppId -- keep this the same across releases so Setup upgrades
; an existing install in place instead of creating a second copy.
AppId={{B6C9F0B0-6E2E-4E9B-9C1C-4C1F7B8E9A10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=Output
OutputBaseFilename=EQL-Log-Reader-Setup
Compression=lzma2
SolidCompression=yes
SetupIconFile=icon.ico
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; Code signing: make_installer.bat passes /DSIGN plus an "eqlsign"
; SignTool definition when signing.bat provides a certificate command
; (see BUILDING.md "Code signing"); the installer and its uninstaller
; both get signed. Unsigned builds skip this entirely.
#ifdef SIGN
SignTool=eqlsign
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Everything build_exe.bat produced -- the four .exe's, the shared
; _internal\ runtime, and icon.png/LICENSE/README.md.
Source: "dist\EQL Log Reader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\EQL Log Reader"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\EQL Log Reader"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
