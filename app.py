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

SO_AUDIO = "Só áudio (MP3)"
# Opção do menu -> altura máxima do vídeo (None = sem limite)
QUALIDADES = {
    "Melhor qualidade": None,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
    SO_AUDIO: None,
}

# No .exe sem console não existe stdout/stderr; o yt-dlp precisa de algum lugar para escrever
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")


def caminho_deno():
    """Retorna o deno.exe embutido no executável ou, rodando pelo Python, o do pacote pip."""
    if getattr(sys, "frozen", False):
        # O PyInstaller extrai os binários embutidos para esta pasta temporária
        return os.path.join(sys._MEIPASS, "deno.exe")
    import deno
    return deno.find_deno_bin()


def opcoes_formato(qualidade):
    """Opções do yt-dlp que dependem da qualidade escolhida no menu."""
    if qualidade == SO_AUDIO:
        return {
            "format": "bestaudio/best",
            # O ffmpeg converte o áudio baixado (m4a/webm) para MP3
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        }
    altura = QUALIDADES[qualidade]
    filtro = f"[height<={altura}]" if altura else ""
    # H.264 (avc1) primeiro porque abre em qualquer player. Na "Melhor qualidade" não,
    # porque acima de 1080p o YouTube só oferece VP9/AV1 e o 4K seria ignorado.
    preferir_h264 = f"bestvideo{filtro}[vcodec^=avc1]+bestaudio[ext=m4a]/" if altura else ""
    return {
        # O YouTube entrega vídeo e áudio separados; o ffmpeg junta os dois em um .mp4
        "format": (
            f"{preferir_h264}bestvideo{filtro}[ext=mp4]+bestaudio[ext=m4a]"
            f"/bestvideo{filtro}+bestaudio/best{filtro}"
        ),
        "merge_output_format": "mp4",
    }


def baixar(url, fila, qualidade="Melhor qualidade"):
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
                if qualidade == SO_AUDIO or partes["atual"] > 0:
                    parte = "áudio"
                else:
                    parte = "vídeo"
                fila.put(("progresso", pct, f"Baixando {parte}... {pct:.0f}%"))
        elif d["status"] == "finished":
            partes["atual"] += 1

    def pos_processamento(d):
        if d["status"] != "started":
            return
        if d["postprocessor"] == "Merger":
            fila.put(("progresso", 100, "Juntando vídeo e áudio..."))
        elif d["postprocessor"] == "ExtractAudio":
            fila.put(("progresso", 100, "Convertendo para MP3..."))

    try:
        fila.put(("progresso", 0, "Buscando informações do vídeo..."))
        ydl_opts = {
            **opcoes_formato(qualidade),
            "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
            # Deno embutido resolve o JavaScript do YouTube, sem exigir nada instalado no PC
            "js_runtimes": {"deno": {"path": caminho_deno()}},
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
        self.qualidade = ttk.Combobox(botoes, values=list(QUALIDADES), state="readonly", width=18)
        self.qualidade.current(0)
        self.qualidade.pack(side="left")
        self.botao = ttk.Button(botoes, text="Baixar", command=self.iniciar)
        self.botao.pack(side="left", padx=8)
        ttk.Button(botoes, text="Abrir pasta", command=self.abrir_pasta).pack(side="left")

        self.barra = ttk.Progressbar(quadro, maximum=100)
        self.barra.pack(fill="x", pady=(12, 4))
        self.status = ttk.Label(quadro, text=f"Os arquivos são salvos em {PASTA_DOWNLOADS}")
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
        self.qualidade.config(state="disabled")
        self.barra["value"] = 0
        args = (url, self.fila, self.qualidade.get())
        threading.Thread(target=baixar, args=args, daemon=True).start()
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
                    self.qualidade.config(state="readonly")
                    self.baixando = False
                    return
        except queue.Empty:
            pass
        self.after(100, self.atualizar)


if __name__ == "__main__":
    App().mainloop()
