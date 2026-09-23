; Inno Setup 6 script — WA Outreach Windows installer
; Build: ISCC build\installer_win.iss  (from project root)

#define AppName      "WA Outreach"
#define AppVersion   "1.0.0"
#define AppPublisher "Driven Properties"
#define AppExeName   "WA Outreach.exe"
#define DistDir      "..\dist\WA Outreach"

[Setup]
AppId={{D4F2A1B3-8C5E-4F9A-B2D1-7E3C6A0F4B8D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisherURL=https://drivenproperties.com
AppSupportURL=https://drivenproperties.com
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=..\dist
OutputBaseFilename=WA_Outreach_Setup
SetupIconFile=..\assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; WebView2 runtime is needed by pywebview on Windows
; It ships with Windows 11 and Edge; for older machines a stub installer is included.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup";     Description: "Start WA Outreach when Windows starts"; GroupDescription: "Additional options:"; Flags: unchecked

[Files]
; All built files from PyInstaller COLLECT output
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
; Optional startup entry
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "{#AppName}"; \
  ValueData: """{app}\{#AppExeName}"""; \
  Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
  Flags: nowait postinstall skipifsilent
