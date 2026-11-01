; Phoque Al - Windows installer (Inno Setup 6). Built by build_windows.bat from the folder dist\PhoqueAl.
[Setup]
AppId={{4D6CC492-76DA-4443-8C6F-6170DF987F16}
AppName=Phoque Al
AppVersion=2.4
AppVerName=Phoque Al 2.4
AppPublisher=Phoque Al contributors
AppPublisherURL=https://github.com/pierreoliviermarcel-ui/phoque-al
DefaultDirName={localappdata}\Programs\Phoque Al
DefaultGroupName=Phoque Al
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=PhoqueAl_Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\PhoqueAl.exe
UninstallDisplayName=Phoque Al
LicenseFile=..\LICENSE

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\PhoqueAl\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; the user's library (database, thumbnails, AI models) is never removed on uninstall
Name: "{app}\data"; Flags: uninsneveruninstall

[Registry]
; "Start with Windows" option: removed on uninstall
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "Phoque Al"; ValueType: none; Flags: uninsdeletevalue dontcreatekey

[Icons]
Name: "{autoprograms}\Phoque Al"; Filename: "{app}\PhoqueAl.exe"
Name: "{autodesktop}\Phoque Al"; Filename: "{app}\PhoqueAl.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PhoqueAl.exe"; Description: "{cm:LaunchProgram,Phoque Al}"; Flags: nowait postinstall skipifsilent
