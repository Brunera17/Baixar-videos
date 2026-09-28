@echo off
REM Gera dist\BaixarVideos.exe com ffmpeg e Deno embutidos
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

python -m PyInstaller --noconfirm --onefile --windowed --name BaixarVideos ^
    --icon assets\icone.ico ^
    --add-data "assets;assets" ^
    --copy-metadata yt-dlp ^
    --collect-binaries imageio_ffmpeg ^
    --collect-data yt_dlp_ejs ^
    --add-binary "%DENO%;." ^
    app.py || exit /b 1
