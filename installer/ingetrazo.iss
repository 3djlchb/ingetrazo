; Inno Setup script for IngeTrazo.
;
; Builds a professional Windows installer: multilingual wizard, GPL license page,
; shortcuts, "Add or Remove Programs" entry and a clean uninstaller.
;
; Local build (needs Inno Setup 6+):
;     iscc /DMyAppVersion=0.2.0 installer\ingetrazo.iss
;
; CI build: see .github/workflows/build-windows.yml
;
; The AppId is a FIXED GUID — never change it between versions, or Windows
; treats every release as a different app and upgrades stop being clean.
;
; 3djlchb edition: its own AppId, name, folder and ProgIDs, so it installs
; BESIDE the official IngeTrazo instead of over it (core/edition.py keeps
; the preferences apart too). Double-clicking .igz or .skp stays with the
; official build unless the matching task is ticked.

#define MyAppName "IngeTrazo 3djlchb"
#define MyAppPublisher "3djlchb (IngeTrazo de Ing. Marco Sumari Tellez)"
#define MyAppURL "https://github.com/3djlchb/ingetrazo"
#define MyAppExeName "ingetrazo.exe"
; Prefix of this edition's ProgIDs; the official build uses «IngeTrazo».
#define MyProgId "IngeTrazo3djlchb"

; MyAppVersion is injected from the build command with /DMyAppVersion=X.Y.Z
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif
; VersionInfoVersion takes numbers only: «0.6.0-3djlchb» → «0.6.0».
#define MyDashPos Pos("-", MyAppVersion)
#if MyDashPos > 0
  #define MyAppFileVersion Copy(MyAppVersion, 1, MyDashPos - 1)
#else
  #define MyAppFileVersion MyAppVersion
#endif

[Setup]
; FIXED AppId GUID — generated once for the 3djlchb edition. Changing it
; breaks upgrades; the official one is {63D1C88D-591C-48C8-A13B-5E41810D05E4}.
AppId={{C8D6F86A-BE83-4152-A84F-89C10F4E4573}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
VersionInfoVersion={#MyAppFileVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Modelador 3D libre para ingenieria y arquitectura
VersionInfoProductName={#MyAppName}

DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

; Free software: the "license" page shows the GPL-3.0 text.
LicenseFile=..\LICENSE

OutputDir=..\dist
OutputBaseFilename=ingetrazo-setup-v{#MyAppVersion}

WizardStyle=modern
; (Saved as UTF-8 WITH a BOM: that is how Inno Setup knows the texts
; below are UTF-8 — without it «Associações» would come out garbled.)
; The wizard speaks the language of Windows (English, Spanish, Brazilian
; Portuguese or Polish) and asks only when none of them matches (issue #135).
ShowLanguageDialog=auto
; Follow Windows on every install, upgrades included. Inno Setup reuses the
; language of the previous install by default, and every install up to
; 0.5.3 was Spanish-only: upgrading kept showing Spanish on an English
; Windows (issue #591).
UsePreviousLanguage=no
DisableProgramGroupPage=yes
; We register file-type icons/associations — tells Explorer to refresh them.
ChangesAssociations=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\resources\icons\ingetrazo.ico

[Languages]
; English first: it is the fallback when Windows speaks something else.
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"

[CustomMessages]
english.SkpDefault=Open .skp files with {#MyAppName} (double-click)
english.IgzDefault=Open .igz documents with {#MyAppName} (double-click)
english.FileAssociations=File associations:
english.IgzDocument={#MyAppName} document
english.DaeModel=COLLADA model ({#MyAppName})
english.SkpModel=SKP model ({#MyAppName})
spanish.SkpDefault=Abrir archivos .skp con {#MyAppName} (doble clic)
spanish.IgzDefault=Abrir documentos .igz con {#MyAppName} (doble clic)
spanish.FileAssociations=Asociaciones de archivos:
spanish.IgzDocument=Documento de {#MyAppName}
spanish.DaeModel=Modelo COLLADA ({#MyAppName})
spanish.SkpModel=Modelo SKP ({#MyAppName})
brazilianportuguese.SkpDefault=Abrir arquivos .skp com o {#MyAppName} (clique duplo)
brazilianportuguese.IgzDefault=Abrir documentos .igz com o {#MyAppName} (clique duplo)
brazilianportuguese.FileAssociations=Associações de arquivos:
brazilianportuguese.IgzDocument=Documento do {#MyAppName}
brazilianportuguese.DaeModel=Modelo COLLADA ({#MyAppName})
brazilianportuguese.SkpModel=Modelo SKP ({#MyAppName})
polish.SkpDefault=Otwieraj pliki .skp w {#MyAppName} (dwukrotne kliknięcie)
polish.IgzDefault=Otwieraj dokumenty .igz w {#MyAppName} (dwukrotne kliknięcie)
polish.FileAssociations=Skojarzenia plików:
polish.IgzDocument=Dokument {#MyAppName}
polish.DaeModel=Model COLLADA ({#MyAppName})
polish.SkpModel=Model SKP ({#MyAppName})

[Tasks]
; Both unchecked: beside the official build, double-click stays with it.
Name: "igzdefault"; Description: "{cm:IgzDefault}"; \
    GroupDescription: "{cm:FileAssociations}"; Flags: unchecked
Name: "skpdefault"; Description: "{cm:SkpDefault}"; \
    GroupDescription: "{cm:FileAssociations}"; Flags: unchecked
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An update copies the new bundle over the old one, and [Files] only adds
; and replaces: whatever the new version no longer ships stayed behind.
; 83 files of earlier releases were found in one upgraded install, among
; them Qt's OpenSSL TLS backend and libssl that 0.5.9 dropped (#564), so
; the extension manager still failed its certificate check (#783). The
; bundle's _internal folder is rebuilt whole by [Files]; nothing of the
; user's lives there (settings, extensions and autosaves are per-user).
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; The whole PyInstaller one-dir bundle.
Source: "..\dist\ingetrazo\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
; Branded document icons, copied to the {app} root so [Registry] DefaultIcon
; entries can reference them with a stable path.
Source: "..\resources\icons\mimetypes\ingetrazo-igz.ico"; DestDir: "{app}"; \
    Flags: ignoreversion
Source: "..\resources\icons\mimetypes\ingetrazo-dae.ico"; DestDir: "{app}"; \
    Flags: ignoreversion
Source: "..\resources\icons\mimetypes\ingetrazo-skp.ico"; DestDir: "{app}"; \
    Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Tasks: desktopicon

[Registry]
; ── Hybrid laptops (Intel + NVIDIA/AMD): run on the discrete GPU ─────────────
; This is the value Settings ▸ System ▸ Display ▸ Graphics writes when the user
; picks "High performance" for a program; without it Windows starts IngeTrazo
; on the integrated chip and the viewport crawls. Only written when the user
; has not chosen anything yet (the app repeats the check at start-up).
Root: HKCU; Subkey: "Software\Microsoft\DirectX\UserGpuPreferences"; \
    ValueType: string; ValueName: "{app}\{#MyAppExeName}"; \
    ValueData: "GpuPreference=2;"; Flags: createvalueifdoesntexist uninsdeletevalue

; ── .igz — IngeTrazo's own format: full association + branded document icon ──
; Double-click opens the document in IngeTrazo and Explorer shows the .igz icon.
; This edition always appears in «Open with»; it takes the double-click only
; with the igzdefault task, leaving it to the official build otherwise.
Root: HKA; Subkey: "Software\Classes\.igz"; ValueType: string; \
    ValueData: "{#MyProgId}.Document"; Tasks: igzdefault; \
    Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKA; Subkey: "Software\Classes\.igz\OpenWithProgids"; \
    ValueType: string; ValueName: "{#MyProgId}.Document"; ValueData: ""; \
    Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKA; Subkey: "Software\Classes\{#MyProgId}.Document"; ValueType: string; \
    ValueData: "{cm:IgzDocument}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#MyProgId}.Document\DefaultIcon"; \
    ValueType: string; ValueData: "{app}\ingetrazo-igz.ico,0"
Root: HKA; Subkey: "Software\Classes\{#MyProgId}.Document\shell\open\command"; \
    ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

; ── .dae / .skp — standard interchange formats: "Open with" only ────────────
; We DO NOT take over the default program or the file icon (Windows ties the
; shown icon to the default handler, and stealing .dae/.skp from the
; programs the user already has would surprise them). Instead we register a ProgId and add it to
; each extension's OpenWithProgids list, so IngeTrazo appears in the right-click
; "Open with" menu. The ProgId carries the branded icon, which only takes visual
; effect if the user later chooses IngeTrazo as the default for these files.
; Uninstalling undoes the entries last to first, so each extension's own key is
; listed before what goes inside it, and goes only if nothing else is left there.
Root: HKA; Subkey: "Software\Classes\.dae"; Flags: uninsdeletekeyifempty
Root: HKA; Subkey: "Software\Classes\.skp"; Flags: uninsdeletekeyifempty
Root: HKA; Subkey: "Software\Classes\{#MyProgId}.dae"; ValueType: string; \
    ValueData: "{cm:DaeModel}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#MyProgId}.dae\DefaultIcon"; \
    ValueType: string; ValueData: "{app}\ingetrazo-dae.ico,0"
Root: HKA; Subkey: "Software\Classes\{#MyProgId}.dae\shell\open\command"; \
    ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\.dae\OpenWithProgids"; \
    ValueType: string; ValueName: "{#MyProgId}.dae"; ValueData: ""; \
    Flags: uninsdeletevalue uninsdeletekeyifempty

Root: HKA; Subkey: "Software\Classes\{#MyProgId}.skp"; ValueType: string; \
    ValueData: "{cm:SkpModel}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#MyProgId}.skp\DefaultIcon"; \
    ValueType: string; ValueData: "{app}\ingetrazo-skp.ico,0"
Root: HKA; Subkey: "Software\Classes\{#MyProgId}.skp\shell\open\command"; \
    ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\.skp\OpenWithProgids"; \
    ValueType: string; ValueName: "{#MyProgId}.skp"; ValueData: ""; \
    Flags: uninsdeletevalue uninsdeletekeyifempty
; Optional task: make this edition the DEFAULT opener for .skp (double-click).
; Unchecked in this edition, so the official build keeps it. Uninstall
; removes the value and Windows falls back to the previous handler.
; The [Code] section below copies the previous thumbnail handler to SkpProgId.
Root: HKA; Subkey: "Software\Classes\.skp"; ValueType: string; \
    ValueData: "{#MyProgId}.skp"; Tasks: skpdefault; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; \
    Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Flags: nowait postinstall skipifsilent

[Code]
// Explorer previews a file with the thumbnail handler of its ProgID, so making
// IngeTrazo.skp the default for .skp hides the previews of the program that
// owned the extension before (issue #804). IngeTrazo has no handler of its own,
// so IngeTrazo.skp borrows the one Windows used until now.
const
  SkpProgId = '{#MyProgId}.skp';
  ThumbnailHandlerKey = 'ShellEx\{E357FCCD-A995-4576-B01F-234630154E96}';

  // Where the ProgID that opened .skp before IngeTrazo is kept, to put it
  // back on uninstall (issue #1268): a value of IngeTrazo.skp's own key.
  SkpClassKey = 'Software\Classes\.skp';
  SkpOwnKey = 'Software\Classes\IngeTrazo.skp';
  PreviousValueName = 'PreviousSkpProgId';

var
  PreviousSkpProgId: String;
  // The default of .skp in the root this install writes to (HKA), which is
  // the one the uninstaller deletes: HKCR also shows the other root's.
  PreviousOwnRootProgId: String;
  // Read by the uninstaller before IngeTrazo.skp's key goes.
  ProgIdToRestore: String;

function ReadThumbnailHandler(const Owner: String; var Clsid: String): Boolean;
begin
  Result := False;
  if Owner <> '' then
    if RegQueryStringValue(HKCR, Owner + '\' + ThumbnailHandlerKey, '', Clsid) then
      Result := Clsid <> '';
end;

// The programs Windows offers for .skp: the one that owned it before may be
// only there once another install of IngeTrazo has the default — a copy for
// one user over the one for the whole machine found IngeTrazo.skp as the
// previous ProgID and skipped it, and the previews of the other program were
// not borrowed. Its ProgID is named in any of three places: the ProgIDs
// .skp's OpenWithProgids lists, the keys under .skp (a program's ShellNew
// goes there), and the list Explorer keeps of what opened .skp (seen on the
// minipc, 11-11: only the last two named it).
function HandlerOfAny(const Root: Integer; const Key: String; const Values: Boolean;
                      var Clsid: String): Boolean;
var
  Names: TArrayOfString;
  Listed: Boolean;
  I: Integer;
begin
  Result := False;
  if Values then
    Listed := RegGetValueNames(Root, Key, Names)
  else
    Listed := RegGetSubkeyNames(Root, Key, Names);
  if Listed then
    for I := 0 to GetArrayLength(Names) - 1 do
      if (not Result) and (Names[I] <> '') and (CompareText(Names[I], SkpProgId) <> 0)
         and (CompareText(Names[I], 'OpenWithProgids') <> 0)
         and (CompareText(Names[I], 'OpenWithList') <> 0)
         and (CompareText(Names[I], 'ShellEx') <> 0)
         and (CompareText(Names[I], 'ShellNew') <> 0) then
        Result := ReadThumbnailHandler(Names[I], Clsid);
end;

function ReadOpenWithHandler(var Clsid: String): Boolean;
begin
  Result := HandlerOfAny(HKCR, '.skp\OpenWithProgids', True, Clsid);
  if not Result then
    Result := HandlerOfAny(HKCR, '.skp', False, Clsid);
  if not Result then
    Result := HandlerOfAny(HKCU,
      'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.skp\OpenWithProgids',
      True, Clsid);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Clsid: String;
  Found: Boolean;
begin
  if CurStep = ssInstall then
  begin
    // Before [Registry] replaces the default of .skp.
    if not RegQueryStringValue(HKCR, '.skp', '', PreviousSkpProgId) then
      PreviousSkpProgId := '';
    if not RegQueryStringValue(HKA, SkpClassKey, '', PreviousOwnRootProgId) then
      PreviousOwnRootProgId := '';
  end
  else if CurStep = ssPostInstall then
  begin
    // An upgrade finds IngeTrazo.skp as the previous ProgID: it is not a source.
    if CompareText(PreviousSkpProgId, SkpProgId) = 0 then
      PreviousSkpProgId := '';
    Found := ReadThumbnailHandler(PreviousSkpProgId, Clsid);
    if not Found then
      Found := ReadThumbnailHandler('.skp', Clsid);
    if not Found then
      Found := ReadThumbnailHandler('SystemFileAssociations\.skp', Clsid);
    if not Found then
      Found := ReadOpenWithHandler(Clsid);
    // The key is deleted with IngeTrazo.skp (uninsdeletekey) on uninstall.
    if Found then
      RegWriteStringValue(HKA, 'Software\Classes\' + SkpProgId + '\' + ThumbnailHandlerKey, '', Clsid);
    // Remember who opened .skp in this root, to hand it back on uninstall.
    // An upgrade finds IngeTrazo.skp there and keeps what the first install
    // wrote down.
    if WizardIsTaskSelected('skpdefault') and (PreviousOwnRootProgId <> '')
       and (CompareText(PreviousOwnRootProgId, SkpProgId) <> 0) then
      RegWriteStringValue(HKA, SkpOwnKey, PreviousValueName, PreviousOwnRootProgId);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Current: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    // Before the uninstaller deletes IngeTrazo.skp's key.
    if not RegQueryStringValue(HKA, SkpOwnKey, PreviousValueName, ProgIdToRestore) then
      ProgIdToRestore := '';
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    // .skp left without a default, or still pointing at IngeTrazo: back to
    // the program that had it. Anything the user chose since is left alone.
    if ProgIdToRestore <> '' then
    begin
      if not RegQueryStringValue(HKA, SkpClassKey, '', Current) then
        Current := '';
      if (Current = '') or (CompareText(Current, SkpProgId) = 0) then
        RegWriteStringValue(HKA, SkpClassKey, '', ProgIdToRestore);
    end;
  end;
end;
