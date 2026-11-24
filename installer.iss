; Установщик программы Drought для Inno Setup 6 (https://jrsoftware.org/isinfo.php).
; Обычно его собирает build_installer.bat — вручную запускать не нужно.
; Результат: installer_output\Drought_Setup.exe

#define AppName "Drought"
#define AppVersion "1.5"
#define AppExe "Drought.exe"

[Setup]
AppId={{6E4C2F1A-9B7D-4C3E-8F21-D80A5B3C7E19}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=ПочваМонитор
VersionInfoDescription=Установщик программы {#AppName}
VersionInfoProductName={#AppName}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Пользовательское соглашение: без «Я принимаю» установка не продолжится
LicenseFile=installer\license_ru.txt
; Права администратора не нужны: программа ставится для текущего пользователя
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer_output
OutputBaseFilename=Drought_Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"

[Files]
Source: "dist\Drought\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{group}\Пользовательское соглашение"; Filename: "{app}\LICENSE.txt"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Запустить {#AppName}"; Flags: nowait postinstall skipifsilent

; Данные пользователя (%LOCALAPPDATA%\Drought: база показаний, настройки) при удалении не трогаем,
; чтобы после переустановки история датчиков сохранилась.
