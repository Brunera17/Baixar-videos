@echo off
REM Gera dist\BaixarVideos.exe com ffmpeg e Deno embutidos
pip install -r requirements.txt pyinstaller || exit /b 1

REM O pacote deno instala o binário fora da pasta do pacote, então é preciso apontar o caminho
for /f "delims=" %%i in ('python -c "import deno; print(deno.find_deno_bin())"') do set DENO=%%i

pyinstaller --noconfirm --onefile --windowed --name BaixarVideos ^
    --collect-binaries imageio_ffmpeg ^
    --collect-data yt_dlp_ejs ^
    --add-binary "%DENO%;." ^
    app.py
