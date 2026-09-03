; Inno Setup script for the Griphook Windows installer.
;
; Build (see .github/workflows/windows-installer.yml):
;   pyinstaller packaging/griphook.spec --noconfirm
;   iscc packaging\griphook.iss /DAppVersion=1.0.0
;
; Design notes:
;   * Per-user install (PrivilegesRequired=lowest) so no administrator rights
;     are needed. The only step that may ask for elevation is the Microsoft
;     ODBC driver, which is a machine-wide component.
;   * The whole setup is a wizard in Portuguese; the person installing never
;     opens a terminal, never edits a .env, never touches the PATH.

#define AppName "Griphook"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppPublisher "Turim"
#define AppExeName "griphook.exe"

; The ODBC driver MSI is downloaded by CI into packaging\. When it is absent
; (local builds), the installer just detects and explains instead of fixing.
#define OdbcMsi "msodbcsql18.msi"
#if FileExists(AddBackslash(SourcePath) + OdbcMsi)
  #define HaveOdbcMsi
#endif

[Setup]
AppId={{9E5C1E2A-7C1F-4C0E-9B4C-4B0F1F3D9A21}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=Griphook-Setup-{#AppVersion}
OutputDir=..\dist\installer
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesEnvironment=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Files]
Source: "..\dist\griphook\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Skill do Claude Code — instalada automaticamente no perfil do usuário.
Source: "..\docs\skill.md"; DestDir: "{%USERPROFILE}\.claude\skills\griphook"; DestName: "SKILL.md"; Flags: ignoreversion
#ifdef HaveOdbcMsi
Source: "{#OdbcMsi}"; DestDir: "{tmp}"; Flags: dontcopy
#endif

[Registry]
; Coloca o programa no PATH do usuário (sem exigir administrador).
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
    ValueData: "{olddata};{app}"; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
Filename: "{app}\{#AppExeName}"; Parameters: "{code:GetConfigureParams}"; \
    StatusMsg: "Salvando a configuração de acesso ao banco..."; Flags: runhidden

[Code]
var
  ConnPage: TWizardPage;
  EdServer, EdDatabase, EdUser, EdPassword: TEdit;
  LblUser, LblPassword: TLabel;
  RbWindows, RbSql: TRadioButton;
  BtnTest: TButton;
  LblStatus: TLabel;
  OdbcDriverName: string;

const
  DriverKey = 'SOFTWARE\ODBC\ODBCINST.INI\ODBC Drivers';

// ---------------------------------------------------------------------------
// PATH do usuário
// ---------------------------------------------------------------------------

function NeedsAddPath(Dir: string): Boolean;
var
  OldPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OldPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(OldPath) + ';') = 0;
end;

procedure RemoveFromPath(Dir: string);
var
  OldPath, NewPath: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OldPath) then
    exit;
  NewPath := ';' + OldPath + ';';
  P := Pos(';' + Uppercase(Dir) + ';', Uppercase(NewPath));
  if P = 0 then
    exit;
  Delete(NewPath, P, Length(Dir) + 1);
  // tira os ';' de borda que adicionamos acima
  if (Length(NewPath) > 0) and (NewPath[1] = ';') then
    Delete(NewPath, 1, 1);
  if (Length(NewPath) > 0) and (NewPath[Length(NewPath)] = ';') then
    Delete(NewPath, Length(NewPath), 1);
  RegWriteExpandStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', NewPath);
end;

// ---------------------------------------------------------------------------
// Driver ODBC
// ---------------------------------------------------------------------------

function FindOdbcDriver(): string;
var
  Value: string;
begin
  Result := '';
  if RegQueryStringValue(HKEY_LOCAL_MACHINE, DriverKey, 'ODBC Driver 18 for SQL Server', Value) then
    Result := 'ODBC Driver 18 for SQL Server'
  else if RegQueryStringValue(HKEY_LOCAL_MACHINE, DriverKey, 'ODBC Driver 17 for SQL Server', Value) then
    Result := 'ODBC Driver 17 for SQL Server';
end;

procedure EnsureOdbcDriver();
#ifdef HaveOdbcMsi
var
  ResultCode: Integer;
#endif
begin
  OdbcDriverName := FindOdbcDriver();
  if OdbcDriverName <> '' then
    exit;

#ifdef HaveOdbcMsi
  if MsgBox('Falta um componente da Microsoft para conversar com o banco de dados' + #13#10 +
            '(Driver ODBC para SQL Server).' + #13#10#13#10 +
            'Podemos instalá-lo agora. O Windows vai pedir uma confirmação de' + #13#10 +
            'administrador na próxima janela.' + #13#10#13#10 +
            'Deseja instalar agora?', mbConfirmation, MB_YESNO) = IDYES then
  begin
    ExtractTemporaryFile('{#OdbcMsi}');
    Exec('msiexec.exe',
         '/i "' + ExpandConstant('{tmp}\{#OdbcMsi}') + '" /passive IACCEPTMSODBCSQLLICENSETERMS=YES',
         '', SW_SHOW, ewWaitUntilTerminated, ResultCode);
    OdbcDriverName := FindOdbcDriver();
  end;
#endif

  if OdbcDriverName = '' then
    MsgBox('O componente "Microsoft ODBC Driver 18 for SQL Server" não está' + #13#10 +
           'instalado neste computador e é necessário para consultar o banco.' + #13#10#13#10 +
           'A instalação vai continuar, mas as consultas só funcionarão depois' + #13#10 +
           'que esse componente for instalado.' + #13#10#13#10 +
           'Peça ao suporte de TI para instalar o "Microsoft ODBC Driver 18' + #13#10 +
           'for SQL Server" nesta máquina.', mbInformation, MB_OK);
end;

// ---------------------------------------------------------------------------
// Página de conexão
// ---------------------------------------------------------------------------

function UsesWindowsAuth(): Boolean;
begin
  Result := RbWindows.Checked;
end;

procedure UpdateCredentialFields();
begin
  LblUser.Enabled := not UsesWindowsAuth();
  EdUser.Enabled := not UsesWindowsAuth();
  LblPassword.Enabled := not UsesWindowsAuth();
  EdPassword.Enabled := not UsesWindowsAuth();
end;

procedure AuthChanged(Sender: TObject);
begin
  UpdateCredentialFields();
end;

function BuildConnectionString(): string;
begin
  Result := 'Driver={' + OdbcDriverName + '};' +
            'Server=' + Trim(EdServer.Text) + ';' +
            'Database=' + Trim(EdDatabase.Text) + ';' +
            'TrustServerCertificate=yes;';
  if UsesWindowsAuth() then
    Result := Result + 'Trusted_Connection=yes;'
  else
    Result := Result + 'Uid=' + Trim(EdUser.Text) + ';Pwd=' + EdPassword.Text + ';';
end;

procedure TestConnectionClick(Sender: TObject);
var
  Conn: Variant;
begin
  if (Trim(EdServer.Text) = '') or (Trim(EdDatabase.Text) = '') then
  begin
    LblStatus.Font.Color := clRed;
    LblStatus.Caption := 'Preencha o servidor e o banco de dados antes de testar.';
    exit;
  end;

  if OdbcDriverName = '' then
  begin
    LblStatus.Font.Color := clRed;
    LblStatus.Caption := 'Não é possível testar: o driver ODBC não está instalado.';
    exit;
  end;

  LblStatus.Font.Color := clBlack;
  LblStatus.Caption := 'Testando...';
  WizardForm.Update();
  try
    Conn := CreateOleObject('ADODB.Connection');
    Conn.ConnectionTimeout := 10;
    Conn.Open(BuildConnectionString());
    Conn.Close();
    LblStatus.Font.Color := clGreen;
    LblStatus.Caption := 'Conexão bem-sucedida.';
  except
    LblStatus.Font.Color := clRed;
    LblStatus.Caption := 'Não foi possível conectar. Confira os dados informados.';
  end;
end;

// Pascal Script (Inno) has no `with`, so every control is built through a local.
function NewLabel(Page: TWizardPage; ALeft, ATop: Integer; ACaption: string): TLabel;
begin
  Result := TLabel.Create(Page);
  Result.Parent := Page.Surface;
  Result.Left := ALeft;
  Result.Top := ATop;
  Result.Caption := ACaption;
end;

function NewEdit(Page: TWizardPage; ATop: Integer): TEdit;
begin
  Result := TEdit.Create(Page);
  Result.Parent := Page.Surface;
  Result.Left := ScaleX(110);
  Result.Top := ATop - ScaleY(3);
  Result.Width := Page.SurfaceWidth - ScaleX(110);
end;

procedure CreateConnectionPage();
var
  Y: Integer;
begin
  ConnPage := CreateCustomPage(wpSelectDir,
    'Conexão com o banco de dados',
    'Informe onde estão os dados que você quer consultar.');

  Y := 0;

  RbWindows := TRadioButton.Create(ConnPage);
  RbWindows.Parent := ConnPage.Surface;
  RbWindows.Left := 0;
  RbWindows.Top := Y;
  RbWindows.Width := ConnPage.SurfaceWidth;
  RbWindows.Caption := 'Entrar com minha conta do Windows (recomendado)';
  RbWindows.Checked := True;
  RbWindows.OnClick := @AuthChanged;
  Y := Y + ScaleY(22);

  RbSql := TRadioButton.Create(ConnPage);
  RbSql.Parent := ConnPage.Surface;
  RbSql.Left := 0;
  RbSql.Top := Y;
  RbSql.Width := ConnPage.SurfaceWidth;
  RbSql.Caption := 'Entrar com usuário e senha do banco de dados';
  RbSql.OnClick := @AuthChanged;
  Y := Y + ScaleY(32);

  NewLabel(ConnPage, 0, Y, 'Servidor:');
  EdServer := NewEdit(ConnPage, Y);
  Y := Y + ScaleY(30);

  NewLabel(ConnPage, 0, Y, 'Banco de dados:');
  EdDatabase := NewEdit(ConnPage, Y);
  Y := Y + ScaleY(30);

  LblUser := NewLabel(ConnPage, 0, Y, 'Usuário:');
  EdUser := NewEdit(ConnPage, Y);
  Y := Y + ScaleY(30);

  LblPassword := NewLabel(ConnPage, 0, Y, 'Senha:');
  EdPassword := NewEdit(ConnPage, Y);
  EdPassword.PasswordChar := '*';
  Y := Y + ScaleY(38);

  BtnTest := TButton.Create(ConnPage);
  BtnTest.Parent := ConnPage.Surface;
  BtnTest.Left := 0;
  BtnTest.Top := Y;
  BtnTest.Width := ScaleX(120);
  BtnTest.Height := ScaleY(25);
  BtnTest.Caption := 'Testar conexão';
  BtnTest.OnClick := @TestConnectionClick;

  LblStatus := TLabel.Create(ConnPage);
  LblStatus.Parent := ConnPage.Surface;
  LblStatus.Left := ScaleX(130);
  LblStatus.Top := Y + ScaleY(5);
  LblStatus.Width := ConnPage.SurfaceWidth - ScaleX(130);
  LblStatus.Caption := '';

  UpdateCredentialFields();
end;

procedure InitializeWizard();
begin
  // Runs before the first page: the connection test on the next page needs the
  // driver to exist, and wpWelcome is hidden in the modern wizard style, so
  // there is no earlier page event to hang this off.
  EnsureOdbcDriver();
  CreateConnectionPage();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;

  if (ConnPage <> nil) and (CurPageID = ConnPage.ID) then
  begin
    if Trim(EdServer.Text) = '' then
    begin
      MsgBox('Informe o nome do servidor do banco de dados.', mbError, MB_OK);
      Result := False;
      exit;
    end;
    if Trim(EdDatabase.Text) = '' then
    begin
      MsgBox('Informe o nome do banco de dados.', mbError, MB_OK);
      Result := False;
      exit;
    end;
    if (not UsesWindowsAuth()) and ((Trim(EdUser.Text) = '') or (EdPassword.Text = '')) then
    begin
      MsgBox('Informe o usuário e a senha do banco de dados.', mbError, MB_OK);
      Result := False;
      exit;
    end;
  end;
end;

// Parâmetros do `griphook configure`, executado ao final da instalação.
function GetConfigureParams(Param: string): string;
begin
  Result := 'configure --non-interactive' +
            ' --server "' + Trim(EdServer.Text) + '"' +
            ' --database "' + Trim(EdDatabase.Text) + '"';
  if UsesWindowsAuth() then
    Result := Result + ' --auth windows'
  else
    Result := Result + ' --auth sql' +
              ' --user "' + Trim(EdUser.Text) + '"' +
              ' --password "' + EdPassword.Text + '"';
  if OdbcDriverName <> '' then
    Result := Result + ' --driver "' + OdbcDriverName + '"';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
    WizardForm.FinishedLabel.Caption :=
      'O Griphook foi instalado com sucesso.' + #13#10#13#10 +
      'Abra o Claude e peça o que você precisa em português, por exemplo:' + #13#10 +
      '"quantos clientes foram cadastrados este mês?".' + #13#10#13#10 +
      'Se o Claude já estava aberto, feche e abra novamente.';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveFromPath(ExpandConstant('{app}'));
end;

[UninstallDelete]
Type: filesandordirs; Name: "{%USERPROFILE}\.claude\skills\griphook"
