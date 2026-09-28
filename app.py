import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import imageio_ffmpeg
import yt_dlp

PASTA_DOWNLOADS = Path.home() / "Downloads"

# No .exe sem console não existe stdout/stderr; o yt-dlp precisa de algum lugar para escrever
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")


def baixar(url, fila):
    """Baixa o vídeo de forma bloqueante, mandando atualizações para a interface pela fila.

    O chamador deve rodar esta função em uma thread separada. Sempre termina com
    uma mensagem "fim" na fila, mesmo em caso de erro.
    """
    partes = {"atual": 0}

    def progresso(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                pct = d["downloaded_bytes"] / total * 100
                parte = "vídeo" if partes["atual"] == 0 else "áudio"
                fila.put(("progresso", pct, f"Baixando {parte}... {pct:.0f}%"))
        elif d["status"] == "finished":
            partes["atual"] += 1

    def pos_processamento(d):
        if d["status"] == "started" and d["postprocessor"] == "Merger":
            fila.put(("progresso", 100, "Juntando vídeo e áudio..."))

    try:
        fila.put(("progresso", 0, "Buscando informações do vídeo..."))
        ydl_opts = {
            # O YouTube entrega vídeo e áudio separados; o ffmpeg junta os dois em um .mp4
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
            # Usa o Node.js instalado para resolver o JavaScript do YouTube
            "js_runtimes": {"node": {}},
            "outtmpl": str(PASTA_DOWNLOADS / "%(title)s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "progress_hooks": [progresso],
            "postprocessor_hooks": [pos_processamento],
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        fila.put(("fim", True, f"Concluído: {info.get('title', 'vídeo')}"))
    except Exception as e:
        fila.put(("fim", False, f"Erro: {e}"))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Baixar Vídeos")
        self.geometry("520x200")
        self.resizable(False, False)
        self.fila = queue.Queue()
        self.baixando = False

        quadro = ttk.Frame(self, padding=16)
        quadro.pack(fill="both", expand=True)

        ttk.Label(quadro, text="Cole o link do vídeo:").pack(anchor="w")
        self.url = ttk.Entry(quadro)
        self.url.pack(fill="x", pady=(4, 8))
        self.url.focus()
        self.url.bind("<Return>", lambda _: self.iniciar())

        botoes = ttk.Frame(quadro)
        botoes.pack(fill="x")
        self.botao = ttk.Button(botoes, text="Baixar", command=self.iniciar)
        self.botao.pack(side="left")
        ttk.Button(botoes, text="Abrir pasta", command=self.abrir_pasta).pack(side="left", padx=8)

        self.barra = ttk.Progressbar(quadro, maximum=100)
        self.barra.pack(fill="x", pady=(12, 4))
        self.status = ttk.Label(quadro, text=f"Os vídeos são salvos em {PASTA_DOWNLOADS}")
        self.status.pack(anchor="w")

    def abrir_pasta(self):
        try:
            PASTA_DOWNLOADS.mkdir(parents=True, exist_ok=True)
            os.startfile(PASTA_DOWNLOADS)
        except OSError as e:
            self.status.config(text=f"Não foi possível abrir a pasta: {e}")

    def iniciar(self):
        # O Enter no campo também chama iniciar, então o bloqueio não pode depender só do botão
        if self.baixando:
            return
        url = self.url.get().strip()
        if not url:
            self.status.config(text="Cole um link primeiro.")
            return
        self.baixando = True
        self.botao.config(state="disabled")
        self.url.config(state="disabled")
        self.barra["value"] = 0
        threading.Thread(target=baixar, args=(url, self.fila), daemon=True).start()
        self.after(100, self.atualizar)

    def atualizar(self):
        # Só a thread principal pode mexer na interface do Tkinter
        try:
            while True:
                tipo, valor, texto = self.fila.get_nowait()
                self.status.config(text=texto)
                if tipo == "progresso":
                    self.barra["value"] = valor
                else:
                    self.barra["value"] = 100 if valor else 0
                    self.botao.config(state="normal")
                    self.url.config(state="normal")
                    self.baixando = False
                    return
        except queue.Empty:
            pass
        self.after(100, self.atualizar)


if __name__ == "__main__":
    App().mainloop()
