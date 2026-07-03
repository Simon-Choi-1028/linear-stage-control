#define MyAppName "Linear Stage Control"
#ifndef MyAppVersion
#define MyAppVersion "0.1.4"
#endif
#define MyAppPublisher "Linear Stage Control"
#define MyAppExeName "LinearStageControl.exe"
#define PylonRuntimeFile "..\dist\LinearStageControl\_internal\sdk_downloads\installers\pylon_Runtime_26.04.1.exe"

[Setup]
AppId={{A0F7CE33-A85E-4C38-B1C6-0FF11B284C88}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Linear Stage Control
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=LinearStageControlSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\LinearStageControl\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
#if FileExists(PylonRuntimeFile)
Filename: "{app}\_internal\sdk_downloads\installers\pylon_Runtime_26.04.1.exe"; Description: "Install Basler pylon Runtime (required on clean PCs)"; Flags: postinstall skipifsilent shellexec
#endif
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
