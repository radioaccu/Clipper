; Inno Setup-script voor Radio ACCU Clipper. Wordt aangeroepen door packaging/build.py:
;   ISCC /DAppVersion=… /DSourceDir=… /DOutputDir=… /DIconFile=… /DOutputName=… installer.iss
#define AppName "Radio ACCU Clipper"
#define AppExe  "Radio ACCU Clipper.exe"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; Vaste AppId: zo herkent Windows een nieuwe versie als update van dezelfde app.
AppId={{3125B396-7D2E-56F1-9A8E-6CC9D6F9D852}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Radio ACCU
VersionInfoVersion={#AppVersion}
; Per gebruiker installeren: geen beheerdersrechten nodig, en yt-dlp kan zichzelf bijwerken.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
CloseApplications=yes
ChangesAssociations=yes

[Languages]
Name: "dutch"; MessagesFile: "compiler:Languages\Dutch.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; .accuclip-projecten openen met een dubbelklik
Root: HKA; Subkey: "Software\Classes\.accuclip\OpenWithProgids"; ValueType: string; ValueName: "RadioACCU.Project"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\RadioACCU.Project"; ValueType: string; ValueName: ""; ValueData: "Radio ACCU-project"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\RadioACCU.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"
Root: HKA; Subkey: "Software\Classes\RadioACCU.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; alleen de cache weggooien; instellingen en projecten van de gebruiker blijven staan
Type: filesandordirs; Name: "{localappdata}\Radio ACCU Clipper\Cache"
