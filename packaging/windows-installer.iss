; Build with script/build-windows-installer.ps1. Keep AppId stable for upgrades.
#ifndef ReleaseRoot
  #error ReleaseRoot must name the clean application staging directory
#endif
#ifndef ReleaseTag
  #define ReleaseTag "v1.9.11-codex.7"
#endif
#ifndef FileVersion
  #error FileVersion must be supplied by script/build-windows-installer.ps1
#endif
#ifndef OutputRoot
  #define OutputRoot "..\release-dist"
#endif

[Setup]
AppId={{FEAEBBB2-BD3B-47D0-9E8E-7D836479A9B2}
AppName=PDFMathTranslate Codex
AppVersion={#ReleaseTag}
VersionInfoVersion={#FileVersion}
AppPublisher=phxumy (unofficial community edition)
AppPublisherURL=https://github.com/phxumy/PDFMathTranslate
AppSupportURL=https://github.com/phxumy/PDFMathTranslate/issues
AppUpdatesURL=https://github.com/phxumy/PDFMathTranslate/releases/latest
DefaultDirName={localappdata}\Programs\PDFMathTranslate-Codex
DefaultGroupName=PDFMathTranslate Codex
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputRoot}
OutputBaseFilename=PDFMathTranslate-Codex-Setup-win64
SetupIconFile={#ReleaseRoot}\site-packages\pdf2zh\assets\desktop-icon.ico
UninstallDisplayIcon={app}\site-packages\pdf2zh\assets\desktop-icon.ico
LicenseFile={#ReleaseRoot}\LICENSE
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
CreateUninstallRegKey=not IsTestMode
UsePreviousAppDir=yes
UsePreviousTasks=yes
ChangesAssociations=no
UninstallDisplayName=PDFMathTranslate Codex {#ReleaseTag}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked; Check: not IsTestMode

[Files]
Source: "{#ReleaseRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,*.pyc,*.pyo"

[Icons]
Name: "{group}\PDFMathTranslate Codex"; Filename: "{app}\PDFMathTranslate-Codex.exe"; WorkingDir: "{app}"; IconFilename: "{app}\site-packages\pdf2zh\assets\desktop-icon.ico"; Check: not IsTestMode
Name: "{autodesktop}\PDFMathTranslate Codex"; Filename: "{app}\PDFMathTranslate-Codex.exe"; WorkingDir: "{app}"; IconFilename: "{app}\site-packages\pdf2zh\assets\desktop-icon.ico"; Tasks: desktopicon; Check: not IsTestMode

[Run]
Filename: "{app}\PDFMathTranslate-Codex.exe"; Description: "Launch PDFMathTranslate Codex"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent; Check: not IsTestMode
Filename: "{app}\PDFMathTranslate-Codex.exe"; WorkingDir: "{app}"; Flags: nowait; Check: ShouldRelaunch

[Code]
function OpenProcess(DesiredAccess: LongWord; InheritHandle: Boolean; ProcessId: LongWord): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(Handle: THandle; Milliseconds: LongWord): LongWord;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function IsTestMode: Boolean;
begin
  Result := ExpandConstant('{param:TESTMODE|0}') = '1';
end;

function ShouldRelaunch: Boolean;
begin
  Result := (ExpandConstant('{param:RELAUNCH|0}') <> '0') and not IsTestMode;
end;

function InitializeSetup: Boolean;
var
  ParentId: Integer;
  ProcessHandle: THandle;
begin
  Result := True;
  ParentId := StrToIntDef(ExpandConstant('{param:WAITFORPID|0}'), 0);
  if ParentId > 0 then begin
    ProcessHandle := OpenProcess($00100000, False, ParentId);
    if ProcessHandle <> 0 then begin
      if WaitForSingleObject(ProcessHandle, 60000) = 258 then begin
        MsgBox('PDFMathTranslate is still running. Close the application and start this installer again.', mbError, MB_OK);
        Result := False;
      end;
      CloseHandle(ProcessHandle);
    end;
  end;
end;
