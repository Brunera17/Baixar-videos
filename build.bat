@echo off
REM Uso:
REM   build.bat             gera dist\BaixarVideos.exe (arquivo unico, portatil)
REM   build.bat instalador  gera instalador\saida\BaixarVideos-Instalador-<versao>.exe
REM                         (precisa do Inno Setup 6: winget install JRSoftware.InnoSetup)
setlocal

REM Tudo passa pelo mesmo "python" para pip, deno e PyInstaller usarem o mesmo ambiente
python -m pip install -r requirements.txt pyinstaller || exit /b 1

REM O pacote deno instala o binário fora da pasta do pacote, então é preciso apontar o caminho
set "DENO="
for /f "delims=" %%i in ('python -c "import deno; print(deno.find_deno_bin())"') do set "DENO=%%i"
if not defined DENO (
    echo ERRO: nao foi possivel localizar o deno.exe do pacote pip "deno".
    exit /b 1
)
if not exist "%DENO%" (
    echo ERRO: deno.exe nao encontrado em "%DENO%".
    exit /b 1
)

REM Opções comuns ao .exe portátil e ao instalador
set OPCOES=--noconfirm --windowed --name BaixarVideos --icon assets\icone.ico --add-data "assets;assets" --copy-metadata yt-dlp --collect-binaries imageio_ffmpeg --collect-data yt_dlp_ejs --add-binary "%DENO%;."

if /i "%~1"=="instalador" goto instalador

python -m PyInstaller --onefile %OPCOES% app.py || exit /b 1
exit /b 0

:instalador
REM No instalador o app vai como pasta (--onedir): abre mais rápido, porque não precisa
REM descompactar ~100 MB numa pasta temporária a cada execução como o arquivo único
python -m PyInstaller --onedir %OPCOES% app.py || exit /b 1

set "ISCC="
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo ERRO: Inno Setup 6 nao encontrado. Instale com: winget install JRSoftware.InnoSetup
    exit /b 1
)

set "VERSAO="
for /f "delims=" %%v in ('python ferramentas\versao.py') do set "VERSAO=%%v"
if not defined VERSAO (
    echo ERRO: nao foi possivel ler VERSAO_APP do app.py.
    exit /b 1
)

"%ISCC%" /Qp /DVersao=%VERSAO% instalador\BaixarVideos.iss || exit /b 1
echo Instalador gerado: instalador\saida\BaixarVideos-Instalador-%VERSAO%.exe
