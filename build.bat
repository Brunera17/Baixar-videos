@echo off
REM Gera dist\BaixarVideos.exe
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --onefile --windowed --name BaixarVideos --collect-binaries imageio_ffmpeg app.py
