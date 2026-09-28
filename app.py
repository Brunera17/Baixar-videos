import json
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

import imageio_ffmpeg
import yt_dlp

# Onde o app guarda as preferências (hoje, só a última pasta escolhida)
ARQUIVO_CONFIG = Path(os.environ.get("APPDATA", Path.home())) / "BaixarVideos" / "config.json"
# Erros técnicos completos, para diagnóstico
ARQUIVO_LOG = ARQUIVO_CONFIG.with_name("erros.log")

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


def pasta_padrao():
    """Pasta Downloads do Windows, mesmo se o usuário a moveu (ex.: para o OneDrive)."""
    try:
        import ctypes
        from ctypes import wintypes
        from uuid import UUID

        class GUID(ctypes.Structure):
            _fields_ = [("bytes", ctypes.c_ubyte * 16)]

        guid = GUID()
        guid.bytes[:] = UUID("{374DE290-123F-4565-9164-39C4925E467B}").bytes_le  # FOLDERID_Downloads
        caminho = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32
        shell32.SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(caminho)) == 0:
            resultado = Path(caminho.value)
            ctypes.windll.ole32.CoTaskMemFree(caminho)
            return resultado
    except (AttributeError, OSError, ValueError):
        pass
    return Path.home() / "Downloads"


def carregar_pasta():
    """Última pasta escolhida, ou a padrão se não houver uma salva ou ela não existir mais."""
    try:
        valor = json.loads(ARQUIVO_CONFIG.read_text(encoding="utf-8"))["pasta"]
        # Vazio ou relativo viraria a pasta atual do processo, que sempre existe
        if isinstance(valor, str) and valor and Path(valor).is_absolute() and Path(valor).is_dir():
            return Path(valor)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return pasta_padrao()


def salvar_pasta(pasta):
    """Guarda a pasta escolhida; se não der para gravar, o app só não lembra dela."""
    try:
        ARQUIVO_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        ARQUIVO_CONFIG.write_text(json.dumps({"pasta": str(pasta)}), encoding="utf-8")
    except OSError:
        pass


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
    if altura is None:
        # Sem filtro de codec/contêiner: acima de 1080p o YouTube costuma oferecer só
        # VP9 (webm) ou AV1, e qualquer preferência por mp4 faria o 4K ser ignorado
        formato = "bestvideo+bestaudio/best"
    else:
        # H.264 (avc1) primeiro porque abre em qualquer player
        filtro = f"[height<={altura}]"
        formato = (
            f"bestvideo{filtro}[vcodec^=avc1]+bestaudio[ext=m4a]"
            f"/bestvideo{filtro}[ext=mp4]+bestaudio[ext=m4a]"
            f"/bestvideo{filtro}+bestaudio/best{filtro}"
        )
    return {
        # O YouTube entrega vídeo e áudio separados; o ffmpeg junta os dois em um .mp4
        "format": formato,
        "merge_output_format": "mp4",
    }


# (trechos da mensagem do yt-dlp/YouTube em minúsculas, mensagem para o usuário).
# A ordem importa: a primeira regra que bater vence.
ERROS_CONHECIDOS = [
    (("is not a valid url",), "Link inválido. Confira se copiou o endereço completo do vídeo."),
    (("unsupported url",), "Esse link não é de um vídeo suportado."),
    (("private video",), "Esse vídeo é privado."),
    (("confirm your age", "age-restricted", "inappropriate for some users"),
     "Esse vídeo tem restrição de idade e exige login no YouTube."),
    (("not a bot",), "O YouTube pediu uma verificação anti-robô. Tente de novo mais tarde."),
    (("members-only", "channel's members", "join this channel"),
     "Esse vídeo é exclusivo para membros do canal."),
    (("not available in your country", "not made this video available in your country"),
     "Esse vídeo está bloqueado no seu país."),
    (("premieres in", "live event will begin", "is upcoming"), "Esse vídeo ainda não foi publicado (estreia agendada)."),
    (("http error 429", "too many requests"),
     "O YouTube bloqueou temporariamente por excesso de pedidos. Tente de novo mais tarde."),
    (("video unavailable", "video is unavailable", "has been removed", "no longer available"),
     "Vídeo indisponível: foi removido ou o link está errado."),
    (("requested format is not available",), "A qualidade escolhida não está disponível para esse vídeo."),
]


def mensagem_erro(erro):
    """Traduz um erro do download em uma mensagem simples para o usuário."""
    # O yt-dlp embrulha o erro original em DownloadError; o original diz mais sobre a causa
    original = getattr(erro, "exc_info", None) and erro.exc_info[1] or erro
    texto = str(erro).lower()

    if isinstance(original, yt_dlp.utils.GeoRestrictedError):
        return "Esse vídeo está bloqueado no seu país."
    for trechos, mensagem in ERROS_CONHECIDOS:
        if any(t in texto for t in trechos):
            return mensagem
    if isinstance(original, yt_dlp.networking.exceptions.TransportError):
        return "Não foi possível conectar. Verifique sua internet e se o link está certo."
    if isinstance(original, yt_dlp.utils.PostProcessingError) or isinstance(erro, yt_dlp.utils.PostProcessingError):
        return "O download terminou, mas falhou ao juntar/converter o arquivo."
    for e in (original, erro):
        if isinstance(e, PermissionError):
            return "Sem permissão para salvar na pasta escolhida. Escolha outra em \"Salvar em...\"."
        if isinstance(e, OSError) and e.errno == 28:  # ENOSPC
            return "Sem espaço no disco."
    return f"Não foi possível baixar esse vídeo. Detalhes técnicos em {ARQUIVO_LOG}"


def registrar_erro(url, erro):
    """Guarda o erro técnico completo para diagnóstico, já que a janela só mostra o resumo."""
    import traceback
    from datetime import datetime
    try:
        ARQUIVO_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ARQUIVO_LOG.open("a", encoding="utf-8") as log:
            log.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {url}\n")
            log.write("".join(traceback.format_exception(erro)))
    except OSError:
        pass


def baixar(url, fila, qualidade="Melhor qualidade", pasta=None):
    """Baixa o vídeo de forma bloqueante, mandando atualizações para a interface pela fila.

    O chamador deve rodar esta função em uma thread separada. Sempre termina com
    uma mensagem "fim" na fila, mesmo em caso de erro.
    """
    pasta = Path(pasta) if pasta else pasta_padrao()
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
            "outtmpl": str(pasta / "%(title)s.%(ext)s"),
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
        registrar_erro(url, e)
        fila.put(("fim", False, mensagem_erro(e)))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Baixar Vídeos")
        self.minsize(520, 0)
        self.resizable(False, False)
        self.fila = queue.Queue()
        self.baixando = False
        self.pasta = carregar_pasta()

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

        destino = ttk.Frame(quadro)
        destino.pack(fill="x", pady=(8, 0))
        self.botao_pasta = ttk.Button(destino, text="Salvar em...", command=self.escolher_pasta)
        self.botao_pasta.pack(side="left")
        self.rotulo_pasta = ttk.Label(destino, foreground="gray")
        self.rotulo_pasta.pack(side="left", padx=8)
        self.mostrar_pasta()

        self.barra = ttk.Progressbar(quadro, maximum=100)
        self.barra.pack(fill="x", pady=(12, 4))
        self.status = ttk.Label(quadro, text="Pronto.", wraplength=488)
        self.status.pack(anchor="w")

    def mostrar_pasta(self):
        texto = str(self.pasta)
        # Caminhos longos são cortados no meio para caber na janela
        if len(texto) > 55:
            texto = texto[:20] + "…" + texto[-34:]
        self.rotulo_pasta.config(text=texto)

    def escolher_pasta(self):
        escolhida = filedialog.askdirectory(
            parent=self, initialdir=self.pasta, title="Escolha onde salvar os downloads")
        if not escolhida:  # usuário cancelou
            return
        self.pasta = Path(escolhida)
        salvar_pasta(self.pasta)
        self.mostrar_pasta()

    def abrir_pasta(self):
        try:
            self.pasta.mkdir(parents=True, exist_ok=True)
            os.startfile(self.pasta)
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
        self.botao_pasta.config(state="disabled")
        self.barra["value"] = 0
        args = (url, self.fila, self.qualidade.get(), self.pasta)
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
                    self.botao_pasta.config(state="normal")
                    self.baixando = False
                    return
        except queue.Empty:
            pass
        self.after(100, self.atualizar)


if __name__ == "__main__":
    App().mainloop()
