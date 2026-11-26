"""Testa gravar_live sem internet: um vídeo local tocando em loop faz o papel da live, parada com "q".

Uso: python testes/testar_gravar_live.py
"""
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import imageio_ffmpeg  # noqa: E402

import app  # noqa: E402


class YdlFalso:
    params = {"ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe()}


def fonte_de_teste(pasta):
    """Arquivo curto H.264 + AAC (os codecs das lives do YouTube) para tocar em loop."""
    fonte = Path(pasta) / "fonte.mp4"
    subprocess.run([YdlFalso.params["ffmpeg_location"], "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30", "-f", "lavfi", "-i", "sine",
                    "-t", "2", "-c:v", "libx264", "-c:a", "aac", str(fonte)], check=True)
    return str(fonte)


def gravar(so_audio, pasta):
    # Em tempo real (-re) e repetindo sem fim (-stream_loop -1): igual a uma live
    opcoes = {"ffmpeg_args": ["-re", "-stream_loop", "-1"]}
    fonte = fonte_de_teste(pasta)
    info = {"requested_formats": [
        {"url": fonte, "downloader_options": opcoes, "manifest_stream_number": 0},
        {"url": fonte, "downloader_options": opcoes, "manifest_stream_number": 1},
    ]}
    fila, resultado = queue.Queue(), {}

    def rodar():
        resultado["arquivo"] = app.gravar_live(
            YdlFalso(), info, pasta, "live", so_audio, fila, threading.Event())

    thread = threading.Thread(target=rodar)
    thread.start()
    time.sleep(4)
    assert app.parar_gravacao(thread.ident), "o ffmpeg deveria estar gravando"
    thread.join(30)
    assert not thread.is_alive(), "a gravação não parou com o q"

    textos = [m[2] for m in list(fila.queue) if m[0] == "progresso"]
    assert any("Gravando ao vivo · 0:0" in t and "KB" in t for t in textos), textos
    arquivo = resultado["arquivo"]
    assert arquivo.name == ("live.mp3" if so_audio else "live.mp4"), arquivo
    assert arquivo.stat().st_size > 1000
    assert not (Path(pasta) / "live.ts.part").exists(), "o arquivo temporário deveria ter sido apagado"
    assert thread.ident not in app.PROCESSOS_FFMPEG or app.PROCESSOS_FFMPEG[thread.ident].poll() is not None
    print("ok:", arquivo.name, arquivo.stat().st_size, "bytes")


with tempfile.TemporaryDirectory() as pasta:
    gravar(False, pasta)
    gravar(True, pasta)
