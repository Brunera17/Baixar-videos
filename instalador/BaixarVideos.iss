; Instalador do Baixar Vídeos (Inno Setup 6).
; Gere com "build.bat instalador", que primeiro monta dist\BaixarVideos\ e passa /DVersao.

#ifndef Versao
  #define Versao "0.0.0"
#endif
#define Nome "Baixar Vídeos"
#define Exe "BaixarVideos.exe"

[Setup]
; Identifica o app no Windows: não mude, senão uma nova versão não substitui a antiga
AppId={{EA3298CF-6702-4286-AF3A-F9225B3A9648}
AppName={#Nome}
AppVersion={#Versao}
AppVerName={#Nome} {#Versao}
AppPublisher=Bruno David Martins
; Instala só para o usuário (em %LOCALAPPDATA%\Programs), sem pedir administrador
PrivilegesRequired=lowest
DefaultDirName={autopf}\BaixarVideos
DefaultGroupName={#Nome}
DisableProgramGroupPage=yes
OutputDir=saida
OutputBaseFilename=BaixarVideos-Instalador-{#Versao}
SetupIconFile=..\assets\icone.ico
UninstallDisplayIcon={app}\{#Exe}
UninstallDisplayName={#Nome}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Se o app estiver aberto, oferece fechá-lo para poder atualizar os arquivos
CloseApplications=yes

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
; Marcado por padrão; o usuário pode desmarcar na instalação
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Ao atualizar, apaga as bibliotecas da versão anterior antes de copiar as novas,
; para não sobrar arquivo velho misturado
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\BaixarVideos\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; AppUserModelID igual ao que o app declara: janela e atalho fixado ficam juntos na barra de tarefas
Name: "{autoprograms}\{#Nome}"; Filename: "{app}\{#Exe}"; AppUserModelID: "BaixarVideos.App"
Name: "{autodesktop}\{#Nome}"; Filename: "{app}\{#Exe}"; AppUserModelID: "BaixarVideos.App"; Tasks: desktopicon

[Run]
Filename: "{app}\{#Exe}"; Description: "{cm:LaunchProgram,{#Nome}}"; Flags: nowait postinstall skipifsilent

; As preferências e o yt-dlp atualizado ficam em %APPDATA%\BaixarVideos e são mantidos
; ao desinstalar, para não perder a pasta escolhida se o app for reinstalado.
