import hashlib
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.request
import zipfile
from pathlib import Path
from tkinter import filedialog

import imageio_ffmpeg
import ttkbootstrap as tb

# Versão do app; o build.bat lê daqui para dar nome ao instalador
VERSAO_APP = "1.0.0"

# Onde o app guarda as preferências (hoje, só a última pasta escolhida)
ARQUIVO_CONFIG = Path(os.environ.get("APPDATA", Path.home())) / "BaixarVideos" / "config.json"
# Erros técnicos completos, para diagnóstico
ARQUIVO_LOG = ARQUIVO_CONFIG.with_name("erros.log")
# yt-dlp baixado pelo botão Atualizar; tem prioridade sobre o que veio embutido no app
PASTA_ATUALIZACOES = ARQUIVO_CONFIG.with_name("atualizacoes")


def numero_versao(texto):
    """'2026.08.19' e '2026.8.19' viram (2026, 8, 19), para comparar versões."""
    return tuple(int(n) for n in re.findall(r"\d+", texto or ""))


def versao_embutida():
    """Versão do yt-dlp que veio com o app, lida sem importar o pacote."""
    try:
        from importlib.metadata import version
        return version("yt-dlp")
    except Exception:
        return "0"


def ativar_atualizacao():
    """Põe na frente do sys.path o yt-dlp atualizado, se for mais novo que o embutido.

    Devolve a pasta usada, ou None se o app vai usar a versão embutida.
    """
    try:
        atual = json.loads((PASTA_ATUALIZACOES / "atual.json").read_text(encoding="utf-8"))
        pasta = PASTA_ATUALIZACOES / atual["pasta"]
        # Se o app foi reconstruído com um yt-dlp mais novo, a atualização antiga é ignorada
        if numero_versao(atual["versao"]) > numero_versao(versao_embutida()) and (pasta / "yt_dlp").is_dir():
            sys.path.insert(0, str(pasta))
            return pasta
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def importar_yt_dlp():
    """Importa o yt-dlp atualizado, ou o embutido se a atualização não carregar."""
    pasta = ativar_atualizacao()
    try:
        import yt_dlp
        return yt_dlp, pasta, None
    except Exception:
        if pasta is None:
            raise
        # Atualização quebrada (ou que precisa de algo que o .exe não tem): volta para a embutida
        sys.path.remove(str(pasta))
        for nome in [m for m in sys.modules if m.split(".")[0] in ("yt_dlp", "yt_dlp_ejs")]:
            del sys.modules[nome]
        import yt_dlp
        return yt_dlp, None, "A versão atualizada do yt-dlp não carregou; usando a que veio com o app."


yt_dlp, PASTA_EM_USO, AVISO_YT_DLP = importar_yt_dlp()

# Vídeos comuns baixados ao mesmo tempo; lives não contam (começam na hora)
LIMITE_VIDEOS = 1

# ffmpeg que está gravando uma live, por thread de download
PROCESSOS_FFMPEG = {}


def parar_gravacao(thread_id):
    """Pede ao ffmpeg para encerrar a live gravando o que já baixou. False se não está gravando."""
    proc = PROCESSOS_FFMPEG.get(thread_id)
    if proc is None or proc.poll() is not None or proc.stdin is None:
        return False
    try:
        # "q" é o jeito do ffmpeg de terminar limpo; matar o processo deixaria o arquivo quebrado
        proc.stdin.write(b"q")
        proc.stdin.flush()
        return True
    except OSError:
        return False


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


def caminho_recurso(*partes):
    """Arquivo que vai junto com o app (ícone etc.), dentro ou fora do .exe."""
    base = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return str(base.joinpath(*partes))


def tema_do_windows():
    """Tema escuro se o Windows estiver no modo escuro para apps; senão, claro."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as chave:
            claro = winreg.QueryValueEx(chave, "AppsUseLightTheme")[0]
        return "bootstrap-light" if claro else "bootstrap-dark"
    except (ImportError, OSError):
        return "bootstrap-light"


def barra_de_titulo_escura(janela):
    """Pede ao Windows 10/11 a barra de título escura (senão ela fica branca no tema escuro)."""
    try:
        import ctypes
        janela.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(janela.winfo_id())
        ligado = ctypes.c_int(1)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE (19 em versões antigas do Windows 10)
        for atributo in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, atributo, ctypes.byref(ligado), ctypes.sizeof(ligado)) == 0:
                break
    except (AttributeError, OSError):
        pass


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


def carregar_config():
    """Preferências salvas; {} se não houver arquivo ou ele estiver corrompido."""
    try:
        dados = json.loads(ARQUIVO_CONFIG.read_text(encoding="utf-8"))
        return dados if isinstance(dados, dict) else {}
    except (OSError, ValueError):
        return {}


def salvar_config(**valores):
    """Atualiza só as chaves passadas, mantendo as outras preferências.

    Se não der para gravar, o app só não lembra da preferência.
    """
    try:
        ARQUIVO_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        ARQUIVO_CONFIG.write_text(json.dumps({**carregar_config(), **valores}), encoding="utf-8")
    except OSError:
        pass


def carregar_pasta():
    """Última pasta escolhida, ou a padrão se não houver uma salva ou ela não existir mais."""
    valor = carregar_config().get("pasta")
    # Vazio ou relativo viraria a pasta atual do processo, que sempre existe
    if isinstance(valor, str) and valor and Path(valor).is_absolute() and Path(valor).is_dir():
        return Path(valor)
    return pasta_padrao()


def salvar_pasta(pasta):
    salvar_config(pasta=str(pasta))


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
    (("http error 403",), "O YouTube recusou o download agora. Tente de novo em alguns minutos."),
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
            # Forma com 3 argumentos: a de 1 argumento só existe a partir do Python 3.11
            log.write("".join(traceback.format_exception(type(erro), erro, erro.__traceback__)))
    except Exception:
        # O log é só diagnóstico: nenhuma falha aqui pode impedir a janela de receber o "fim"
        pass


def formatar_bytes(n):
    for unidade in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unidade}" if unidade != "MB" else f"{n:.1f} {unidade}"
        n /= 1024
    return f"{n:.2f} GB"


def formatar_tempo(segundos):
    minutos, segundos = divmod(int(segundos), 60)
    horas, minutos = divmod(minutos, 60)
    return f"{horas}:{minutos:02d}:{segundos:02d}" if horas else f"{minutos}:{segundos:02d}"


def texto_progresso(parte, d):
    """Ex.: 'Baixando vídeo... 45% · 36.1 MB de 79.9 MB · 5.2 MB/s · faltam 0:08'."""
    baixado = d.get("downloaded_bytes") or 0
    total = d.get("total_bytes") or d.get("total_bytes_estimate")
    trechos = [f"Baixando {parte}..."]
    if total:
        trechos[0] += f" {baixado / total * 100:.0f}%"
        # Sem total_bytes o yt-dlp só tem uma estimativa
        aprox = "" if d.get("total_bytes") else "~"
        trechos.append(f"{formatar_bytes(baixado)} de {aprox}{formatar_bytes(total)}")
    else:
        trechos.append(formatar_bytes(baixado))
    if d.get("speed"):
        trechos.append(f"{formatar_bytes(d['speed'])}/s")
    if d.get("eta") is not None:
        trechos.append(f"faltam {formatar_tempo(d['eta'])}")
    return " · ".join(trechos)


def baixar_miniatura(ydl, info):
    """Baixa a miniatura já reduzida para a janela. Devolve uma imagem do Pillow ou None."""
    try:
        from io import BytesIO
        from PIL import Image

        candidatas = [t for t in info.get("thumbnails") or []
                      if t.get("url") and (t.get("width") or 0) >= 160]
        # A menor que ainda fica nítida em 160 px de largura, para baixar rápido
        url = min(candidatas, key=lambda t: t["width"])["url"] if candidatas else info.get("thumbnail")
        if not url:
            return None
        imagem = Image.open(BytesIO(ydl.urlopen(url).read())).convert("RGB")
        imagem.thumbnail((160, 90))
        return imagem
    except Exception:
        # Miniatura é só enfeite: sem ela o download segue normalmente
        return None


def arquivos_na_pasta(pasta):
    try:
        return {p.name for p in Path(pasta).iterdir()}
    except OSError:
        return set()


def apagar_parciais(pasta, nome_base, existentes):
    """Remove o que um download cancelado criou (.part, .ytdl, fragmentos, partes prontas).

    Só apaga arquivos que não estavam na pasta antes do download (`existentes`) e cujo
    nome começa com o nome deste vídeo; arquivos antigos, mesmo parciais, ficam.
    """
    for nome in arquivos_na_pasta(pasta) - existentes:
        if nome.startswith(nome_base + "."):
            try:
                os.remove(Path(pasta) / nome)
            except OSError:
                pass


def ler_pypi(pacote, versao=None):
    caminho = f"{pacote}/{versao}/json" if versao else f"{pacote}/json"
    with urllib.request.urlopen(f"https://pypi.org/pypi/{caminho}", timeout=15) as resposta:
        return json.load(resposta)


def versao_nova_disponivel():
    """Última versão estável do yt-dlp no PyPI, se for mais nova que a em uso; senão None."""
    ultima = ler_pypi("yt-dlp")["info"]["version"]  # o PyPI já ignora as versões de teste aqui
    if numero_versao(ultima) > numero_versao(yt_dlp.version.__version__):
        return ultima
    return None


def baixar_wheel(dados_pypi):
    """Baixa o pacote .whl e confere o SHA-256 publicado pelo PyPI antes de aceitar."""
    wheel = next(u for u in dados_pypi["urls"] if u["packagetype"] == "bdist_wheel")
    with urllib.request.urlopen(wheel["url"], timeout=60) as resposta:
        conteudo = resposta.read()
    if hashlib.sha256(conteudo).hexdigest() != wheel["digests"]["sha256"]:
        raise ValueError(f"{wheel['filename']} veio corrompido (SHA-256 não confere)")
    return conteudo


def instalar_yt_dlp(versao):
    """Baixa o yt-dlp `versao` e o yt-dlp-ejs que ele exige para PASTA_ATUALIZACOES.

    A nova versão só passa a valer na próxima vez que o app abrir.
    """
    dados = ler_pypi("yt-dlp", versao)
    pacotes = [dados]
    # O yt-dlp exige uma versão exata do yt-dlp-ejs (scripts que resolvem o JavaScript do YouTube)
    for requisito in dados["info"].get("requires_dist") or []:
        ejs = re.match(r"yt-dlp-ejs==([\w.]+)", requisito)
        if ejs:
            pacotes.append(ler_pypi("yt-dlp-ejs", ejs.group(1)))
            break

    # Nome único: a versão que está valendo agora nunca é apagada antes de a nova estar pronta
    sufixo = os.urandom(4).hex()
    destino = PASTA_ATUALIZACOES / f"yt-dlp-{versao}-{sufixo}"
    temporaria = destino.with_name(destino.name + ".baixando")
    for pacote in pacotes:
        # .whl é um zip; os pacotes são Python puro, então basta extrair
        with zipfile.ZipFile(io.BytesIO(baixar_wheel(pacote))) as whl:
            extrair_com_seguranca(whl, temporaria)
    temporaria.rename(destino)

    # Troca atômica: quem abrir o app vê o atual.json antigo ou o novo, nunca um pela metade
    provisorio = PASTA_ATUALIZACOES / f"atual.json.{sufixo}.tmp"
    provisorio.write_text(json.dumps({"versao": versao, "pasta": destino.name}), encoding="utf-8")
    os.replace(provisorio, PASTA_ATUALIZACOES / "atual.json")

    # Só agora limpa o resto (versões antigas, sobras de tentativas interrompidas),
    # menos a nova e a que este processo está usando
    for pasta in PASTA_ATUALIZACOES.iterdir():
        if pasta.is_dir() and pasta not in (destino, PASTA_EM_USO):
            shutil.rmtree(pasta, ignore_errors=True)


def extrair_com_seguranca(whl, destino):
    """Extrai o .whl recusando entradas que escapariam de `destino` (../, caminho absoluto).

    O extractall do Python já neutraliza esses caminhos; a checagem explícita deixa isso
    garantido aqui, sem depender de detalhe de implementação.
    """
    raiz = Path(destino).resolve()
    for membro in whl.infolist():
        alvo = (raiz / membro.filename).resolve()
        if alvo != raiz and raiz not in alvo.parents:
            raise ValueError(f"Entrada insegura no pacote: {membro.filename}")
    whl.extractall(raiz)


def reiniciar_app():
    """Abre uma nova cópia do app (que vai carregar o yt-dlp atualizado)."""
    if getattr(sys, "frozen", False):
        comando = [sys.executable]
        # Sem isso, a nova cópia do .exe tentaria reaproveitar a pasta temporária desta,
        # que é apagada quando esta fecha
        ambiente = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    else:
        comando = [sys.executable, os.path.abspath(sys.argv[0])]
        ambiente = None
    # Lista de argumentos sem shell=True: nada é interpretado por um shell, e os valores vêm do
    # próprio processo (caminho do .exe / do Python e deste script), não do usuário nem da internet
    subprocess.Popen(comando, env=ambiente, shell=False)


def links_do_youtube(texto):
    """Só os links do YouTube (youtube.com, youtu.be e subdomínios) encontrados no texto."""
    # O domínio precisa vir logo depois do "https://": "evil.com/youtube.com/..." não passa
    dominio = re.compile(r"(?:https?://)?(?:[\w-]+\.)*(?:youtube\.com|youtu\.be)/", re.IGNORECASE)
    return [link for link in extrair_links(texto) if dominio.match(link)]


def extrair_links(texto):
    """Links encontrados no texto colado, na ordem, sem repetir.

    Aceita um por linha, vários na mesma linha ou links no meio de outro texto.
    """
    links = re.findall(r"(?:https?://|www\.)[^\s<>\"']+", texto)
    # Pontuação colada no fim ("veja https://...,") não faz parte do link
    links = [link.rstrip(".,;:!?)]}") for link in links]
    return list(dict.fromkeys(links))


def texto_gravacao(estado):
    """Ex.: 'Gravando ao vivo · 2:10 · 35.0 MB', a partir das linhas do -progress do ffmpeg."""
    trechos = ["Gravando ao vivo"]
    # Antes do primeiro pedaço o ffmpeg manda "N/A"
    if estado.get("out_time_us", "").isdigit():
        trechos.append(formatar_tempo(int(estado["out_time_us"]) / 1_000_000))
    if estado.get("total_size", "").isdigit():
        trechos.append(formatar_bytes(int(estado["total_size"])))
    return " · ".join(trechos) + " · \"Parar e salvar\" encerra e guarda o que já foi gravado"


def gravar_live(ydl, info, pasta, nome_base, so_audio, fila, cancelar):
    """Grava a live com um ffmpeg do próprio app, até ela acabar ou o usuário clicar em Parar.

    O yt-dlp só escolhe os formatos e descobre os endereços. O processo do ffmpeg fica com
    o app, que lê o progresso e pode encerrá-lo limpo (parar_gravacao). Os argumentos de
    entrada seguem os que o FFmpegFD do yt-dlp usa para lives.
    """
    ffmpeg = ydl.params["ffmpeg_location"]
    gravacao = Path(pasta) / f"{nome_base}.ts.part"
    final = Path(pasta) / f"{nome_base}.{'mp3' if so_audio else 'mp4'}"
    sem_janela = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # senão o .exe abre um console preto

    formatos = info.get("requested_formats") or [info]
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1"]
    for fmt in formatos:
        if re.match(r"https?://", fmt["url"]):
            cookies = ydl.cookiejar.get_cookies_for_url(fmt["url"])
            if cookies:
                args += ["-cookies", "".join(
                    f"{c.name}={c.value}; path={c.path}; domain={c.domain};\r\n" for c in cookies)]
            cabecalhos = fmt.get("http_headers") or info.get("http_headers")
            if cabecalhos:
                args += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in cabecalhos.items())]
        args += (fmt.get("downloader_options") or {}).get("ffmpeg_args") or []
        args += ["-i", fmt["url"]]
    if info.get("requested_formats"):
        # Vídeo e áudio vêm em endereços separados; o ffmpeg grava os dois juntos
        for i, fmt in enumerate(formatos):
            args += ["-map", f"{i}:{fmt.get('manifest_stream_number', 0)}"]
    # MPEG-TS continua tocável mesmo se a gravação for interrompida de repente
    args += ["-c", "copy", "-f", "mpegts", str(gravacao)]

    with tempfile.TemporaryFile() as erros:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=erros,
                                creationflags=sem_janela)
        PROCESSOS_FFMPEG[threading.get_ident()] = proc
        estado = {}
        for linha in proc.stdout:
            chave, _, valor = linha.decode("utf-8", "replace").strip().partition("=")
            estado[chave] = valor
            if chave == "progress":  # última linha de cada bloco de progresso
                if cancelar.is_set():
                    proc.kill()
                    break
                fila.put(("progresso", 0, texto_gravacao(estado)))
        codigo = proc.wait()
        if cancelar.is_set():
            raise yt_dlp.utils.DownloadCancelled()
        if not gravacao.exists() or gravacao.stat().st_size == 0:
            erros.seek(0)
            raise RuntimeError(f"ffmpeg saiu com código {codigo} sem gravar nada: "
                               + erros.read().decode("utf-8", "replace")[-2000:])
        # Com algo gravado, guarda mesmo se a conexão caiu no meio: é melhor que perder tudo

    fila.put(("sem_cancelar", None, None))
    if so_audio:
        fila.put(("progresso", 100, "Convertendo para MP3..."))
        saida = ["-vn", "-c:a", "libmp3lame", "-b:a", "192k"]
    else:
        fila.put(("progresso", 100, "Convertendo a gravação para MP4..."))
        saida = ["-map", "0", "-c", "copy", "-bsf:a", "aac_adtstoasc"]
    resultado = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(gravacao), *saida, str(final)],
        capture_output=True, creationflags=sem_janela)
    if resultado.returncode != 0:
        # Não perde a gravação: fica como .ts, que abre no VLC e na maioria dos players
        final.unlink(missing_ok=True)
        final = gravacao.replace(final.with_suffix(".ts"))
    else:
        gravacao.unlink()
    return final


def baixar(url, fila, qualidade="Melhor qualidade", pasta=None, cancelar=None,
           desde_inicio=False, parar=None):
    """Baixa o vídeo de forma bloqueante, mandando atualizações para a interface pela fila.

    O chamador deve rodar esta função em uma thread separada. Sempre termina com
    uma mensagem ("fim", resultado, texto), com resultado "Concluído", "Cancelado" ou
    "Erro". Para cancelar, o chamador liga o threading.Event `cancelar`.

    Com `desde_inicio`, uma live é baixada a partir do começo da transmissão; ligar o
    Event `parar` encerra essa gravação guardando o que já foi baixado.
    """
    pasta = Path(pasta) if pasta else pasta_padrao()
    cancelar = cancelar or threading.Event()
    parar = parar or threading.Event()
    # Bytes já baixados de cada formato (vídeo e áudio) da live gravada desde o início
    baixado_live = {}
    partes = {"atual": 0}
    # Preenchido logo antes do download: nome do vídeo e o que já estava na pasta
    limpeza = {"nome_base": None, "existentes": set()}

    def conferir_cancelamento():
        if cancelar.is_set():
            raise yt_dlp.utils.DownloadCancelled()

    def progresso(d):
        conferir_cancelamento()
        if d.get("info_dict", {}).get("is_live"):
            # Só chega aqui a live desde o início: a gravada pelo ffmpeg do app não usa ganchos.
            # É o KeyboardInterrupt (não o DownloadCancelled) que faz o yt-dlp encerrar
            # a live e juntar vídeo e áudio do que já baixou
            if parar.is_set():
                raise KeyboardInterrupt
            if d["status"] == "downloading":
                baixado_live[d["info_dict"].get("format_id")] = d.get("downloaded_bytes") or 0
                fila.put(("progresso", 0, f"Gravando ao vivo desde o início · "
                                          f"{formatar_bytes(sum(baixado_live.values()))} · "
                                          f"\"Parar e salvar\" encerra e guarda o que já foi baixado"))
            return
        if d["status"] == "downloading":
            if qualidade == SO_AUDIO or partes["atual"] > 0:
                parte = "áudio"
            else:
                parte = "vídeo"
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            pct = d["downloaded_bytes"] / total * 100 if total else 0
            fila.put(("progresso", pct, texto_progresso(parte, d)))
        elif d["status"] == "finished":
            partes["atual"] += 1

    def pos_processamento(d):
        if d["status"] != "started":
            return
        conferir_cancelamento()
        if d["postprocessor"] in ("Merger", "ExtractAudio"):
            # O ffmpeg não pode ser interrompido no meio sem deixar arquivo quebrado
            fila.put(("sem_cancelar", None, None))
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
            # Para lives do YouTube: baixa desde o começo da transmissão em vez de a partir de agora
            "live_from_start": desde_inicio,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "progress_hooks": [progresso],
            "postprocessor_hooks": [pos_processamento],
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Duas etapas para mostrar título e miniatura antes de o download começar
            info = ydl.extract_info(url, download=False)
            conferir_cancelamento()
            detalhes = {
                "titulo": info.get("title") or "Sem título",
                "canal": info.get("uploader") or info.get("channel") or "",
                "duracao": formatar_tempo(info["duration"]) if info.get("duration") else "",
                "ao_vivo": bool(info.get("is_live")),
                "imagem": None,
            }
            fila.put(("info", detalhes, "Baixando miniatura..."))
            detalhes["imagem"] = baixar_miniatura(ydl, info)
            fila.put(("info", detalhes, "Começando o download..."))
            conferir_cancelamento()
            # Foto da pasta antes de baixar, para o cancelamento só apagar o que for novo
            limpeza["nome_base"] = Path(ydl.prepare_filename(info)).stem
            limpeza["existentes"] = arquivos_na_pasta(pasta)
            if detalhes["ao_vivo"] and not desde_inicio:
                fila.put(("progresso", 0, "Começando a gravar a live..."))
                gravar_live(ydl, info, pasta, limpeza["nome_base"], qualidade == SO_AUDIO, fila, cancelar)
            else:
                info = ydl.process_ie_result(info, download=True)
                if detalhes["ao_vivo"]:
                    # Live desde o início: o pedaço que baixava na hora do Parar fica pela metade
                    for nome in arquivos_na_pasta(pasta) - limpeza["existentes"]:
                        if nome.startswith(limpeza["nome_base"] + ".") and nome.endswith(".part"):
                            (pasta / nome).unlink(missing_ok=True)
        # O clique em Cancelar pode chegar enquanto o ffmpeg roda, antes de o botão ser
        # desabilitado; nesse caso o cancelamento vale e o arquivo final também é apagado
        conferir_cancelamento()
        fila.put(("fim", "Concluído", f"Concluído: {info.get('title', 'vídeo')}"))
    except Exception as e:
        # Se o usuário pediu para cancelar, qualquer erro que veio junto é consequência disso
        if cancelar.is_set():
            if limpeza["nome_base"]:
                apagar_parciais(pasta, limpeza["nome_base"], limpeza["existentes"])
            fila.put(("fim", "Cancelado", "Download cancelado."))
            return
        registrar_erro(url, e)
        fila.put(("fim", "Erro", mensagem_erro(e)))
    finally:
        PROCESSOS_FFMPEG.pop(threading.get_ident(), None)


class App(tb.App):
    def __init__(self):
        super().__init__(
            title="Baixar Vídeos", theme=tema_do_windows(), iconphoto=None,
            minsize=(540, 0), resizable=(False, False))
        # .ico tem vários tamanhos; o Windows escolhe o certo para a barra de título e a de tarefas
        try:
            self.iconbitmap(default=caminho_recurso("assets", "icone.ico"))
        except tk.TclError:
            pass
        if "dark" in self.style.theme_use():
            barra_de_titulo_escura(self)
        self.itens = {}  # id da linha na lista -> url, qualidade, pasta e mensagem final
        self.pendentes = []  # ids na ordem em que vão ser baixados
        # id -> fila, evento de cancelar, thread, se é live e o último progresso de cada download em andamento
        self.ativos = {}
        self.foco = None  # download mostrado na miniatura, na barra e no status
        self.resumo = {"Concluído": 0, "Erro": 0, "Cancelado": 0}
        self.pasta = carregar_pasta()

        quadro = tb.Frame(self, padding=20)
        quadro.pack(fill="both", expand=True)

        tb.Label(quadro, text="Baixar Vídeos", font=("Segoe UI Semibold", 15)).pack(anchor="w")
        tb.Label(quadro, text="Cole um ou mais links (um por linha) e clique em Baixar.",
                 bootstyle="secondary").pack(anchor="w", pady=(0, 10))
        self.links = tb.Text(quadro, height=3, wrap="none", font=("Segoe UI", 10), undo=True)
        self.links.pack(fill="x", pady=(0, 10))
        self.links.focus()
        # Enter sozinho pula linha (para colar vários links); Ctrl+Enter adiciona à fila
        self.links.bind("<Control-Return>", lambda _: (self.adicionar(), "break")[1])

        botoes = tb.Frame(quadro)
        botoes.pack(fill="x")
        self.qualidade = tb.Combobox(botoes, values=list(QUALIDADES), state="readonly", width=17)
        self.qualidade.current(0)
        self.qualidade.pack(side="left")
        self.botao = tb.Button(botoes, text="Baixar", command=self.adicionar, bootstyle="primary", width=9)
        self.botao.pack(side="left", padx=8)
        self.botao_cancelar = tb.Button(botoes, text="Cancelar", command=self.cancelar, state="disabled",
                                        bootstyle="danger-outline")
        self.botao_cancelar.pack(side="left")
        tb.Button(botoes, text="Abrir pasta", command=self.abrir_pasta,
                  bootstyle="secondary-outline").pack(side="right")

        destino = tb.Frame(quadro)
        destino.pack(fill="x", pady=(10, 0))
        self.botao_pasta = tb.Button(destino, text="Salvar em...", command=self.escolher_pasta,
                                     bootstyle="secondary-outline")
        self.botao_pasta.pack(side="left")
        self.rotulo_pasta = tb.Label(destino, bootstyle="secondary")
        self.rotulo_pasta.pack(side="left", padx=10)
        self.mostrar_pasta()

        # Colar sozinho os links do YouTube copiados (ligado por padrão; a escolha fica salva)
        self.colar_automatico = tk.BooleanVar(value=carregar_config().get("colar_automatico", True) is not False)
        tb.Checkbutton(quadro, text="Colar automaticamente os links do YouTube que eu copiar",
                       variable=self.colar_automatico, bootstyle="round-toggle",
                       command=lambda: salvar_config(colar_automatico=self.colar_automatico.get())
                       ).pack(anchor="w", pady=(10, 0))
        # Desligado por padrão: alcançar o ao vivo a partir do começo pode demorar bastante
        self.desde_inicio = tk.BooleanVar(value=carregar_config().get("live_desde_inicio") is True)
        tb.Checkbutton(quadro, text="Gravar lives desde o começo (inclui o que já passou)",
                       variable=self.desde_inicio, bootstyle="round-toggle",
                       command=lambda: salvar_config(live_desde_inicio=self.desde_inicio.get())
                       ).pack(anchor="w", pady=(6, 0))
        self.ultima_area_de_transferencia = None
        # Ao voltar para o app (ex.: depois de copiar o link no navegador) e ao abrir
        self.bind("<FocusIn>", self.colar_da_area_de_transferencia, add="+")
        self.after(300, self.colar_da_area_de_transferencia)

        # Fila de downloads
        area_lista = tb.Frame(quadro)
        area_lista.pack(fill="x", pady=(16, 0))
        self.lista = tb.Treeview(
            area_lista, columns=("video", "qualidade", "estado"), show="headings", height=5)
        for coluna, titulo, largura in (("video", "Vídeo", 250), ("qualidade", "Qualidade", 117),
                                        ("estado", "Situação", 100)):
            self.lista.heading(coluna, text=titulo, anchor="w")
            self.lista.column(coluna, width=largura, minwidth=largura, stretch=coluna == "video")
        rolagem = tb.Scrollbar(area_lista, orient="vertical", command=self.lista.yview)
        self.lista.configure(yscrollcommand=rolagem.set)
        self.lista.pack(side="left", fill="x", expand=True)
        rolagem.pack(side="left", fill="y")
        self.lista.bind("<<TreeviewSelect>>", self.mostrar_mensagem_item)
        cores = self.style.colors
        self.lista.tag_configure("Concluído", foreground=cores.success)
        self.lista.tag_configure("Erro", foreground=cores.danger)
        self.lista.tag_configure("Cancelado", foreground=cores.secondary)

        botoes_lista = tb.Frame(quadro)
        botoes_lista.pack(fill="x", pady=(6, 0))
        tb.Button(botoes_lista, text="Remover selecionados", command=self.remover_selecionados,
                  bootstyle="link").pack(side="left")
        tb.Button(botoes_lista, text="Limpar concluídos", command=self.limpar_concluidos,
                  bootstyle="link").pack(side="left")

        # Miniatura e dados do vídeo atual; só aparece depois que o link é lido
        self.quadro_info = tb.Frame(quadro)
        self.miniatura = tb.Label(self.quadro_info)
        self.miniatura.pack(side="left", padx=(0, 12))
        textos = tb.Frame(self.quadro_info)
        textos.pack(side="left", fill="x", expand=True)
        self.rotulo_titulo = tb.Label(textos, font=("Segoe UI Semibold", 11), wraplength=320)
        self.rotulo_titulo.pack(anchor="w")
        self.rotulo_detalhes = tb.Label(textos, bootstyle="secondary")
        self.rotulo_detalhes.pack(anchor="w", pady=(2, 0))
        self._imagem_tk = None  # referência precisa ficar viva, senão o Tk apaga a imagem

        self.barra = tb.Progressbar(quadro, maximum=100, bootstyle="success")
        self.barra.pack(fill="x", pady=(16, 6))
        self.status = tb.Label(quadro, text="Pronto.", wraplength=500)
        self.status.pack(anchor="w")

        # Rodapé: versão do yt-dlp em uso e aviso quando há uma mais nova
        tb.Separator(quadro).pack(fill="x", pady=(14, 8))
        rodape = tb.Frame(quadro)
        rodape.pack(fill="x")
        tb.Label(rodape, text=f"v{VERSAO_APP} · yt-dlp {yt_dlp.version.__version__}", bootstyle="secondary",
                 font=("Segoe UI", 8)).pack(side="left")
        self.aviso = tb.Label(rodape, text=AVISO_YT_DLP or "", wraplength=300)
        self.aviso.pack(side="left", padx=8)
        self.botao_atualizar = tb.Button(rodape, text="Atualizar", command=self.instalar_atualizacao,
                                         bootstyle="warning")
        self.versao_nova = None
        self.fila_atualizacao = queue.Queue()
        self.em_segundo_plano(versao_nova_disponivel)

    @property
    def baixando(self):
        return bool(self.ativos)

    def colar_da_area_de_transferencia(self, _evento=None):
        """Coloca na caixa os links do YouTube copiados, se forem novos.

        Só age quando o conteúdo copiado muda: voltar para o app várias vezes com o mesmo
        link copiado não cola de novo, e um link apagado da caixa não volta sozinho.
        """
        if not self.colar_automatico.get():
            return
        try:
            texto = self.clipboard_get()
        except tk.TclError:  # área de transferência vazia ou com imagem/arquivo
            return
        if texto == self.ultima_area_de_transferencia:
            return
        self.ultima_area_de_transferencia = texto

        # Não repete o que já está na caixa ou na fila (inclusive os já baixados)
        conhecidos = set(extrair_links(self.links.get("1.0", "end"))) | {i["url"] for i in self.itens.values()}
        novos = [link for link in links_do_youtube(texto) if link not in conhecidos]
        if not novos:
            return
        atual = self.links.get("1.0", "end-1c")
        separador = "\n" if atual.strip() and not atual.endswith("\n") else ""
        self.links.insert("end", separador + "\n".join(novos) + "\n")
        self.links.see("end")
        # Durante um download a linha de status mostra o progresso, que é mais útil
        if not self.baixando:
            self.status.config(text=f"{len(novos)} link(s) do YouTube colado(s) automaticamente. "
                                    "Clique em Baixar.")

    def em_segundo_plano(self, tarefa, *args):
        """Roda `tarefa` numa thread; o resultado (ou o erro) chega em ouvir_atualizacao."""
        def rodar():
            try:
                self.fila_atualizacao.put((tarefa.__name__, tarefa(*args), None))
            except Exception as e:
                self.fila_atualizacao.put((tarefa.__name__, None, e))
        threading.Thread(target=rodar, daemon=True).start()
        self.after(200, self.ouvir_atualizacao)

    def ouvir_atualizacao(self):
        try:
            tarefa, resultado, erro = self.fila_atualizacao.get_nowait()
        except queue.Empty:
            self.after(200, self.ouvir_atualizacao)
            return
        if tarefa == "versao_nova_disponivel":
            # Sem internet ou PyPI fora do ar: não incomoda, só não avisa
            if resultado:
                self.versao_nova = resultado
                self.aviso.config(text=f"Nova versão do yt-dlp: {resultado}")
                self.botao_atualizar.pack(side="right")
        elif tarefa == "instalar_yt_dlp":
            if erro:
                registrar_erro("atualização do yt-dlp", erro)
                self.aviso.config(text="Não foi possível atualizar. Tente de novo mais tarde.")
                self.botao_atualizar.config(text="Tentar de novo", state="normal")
            else:
                self.aviso.config(text=f"yt-dlp {self.versao_nova} instalado. Reinicie para usar.")
                self.botao_atualizar.config(text="Reiniciar", command=self.reiniciar, state="normal")

    def instalar_atualizacao(self):
        self.botao_atualizar.config(state="disabled")
        self.aviso.config(text=f"Baixando yt-dlp {self.versao_nova}...")
        self.em_segundo_plano(instalar_yt_dlp, self.versao_nova)

    def reiniciar(self):
        if self.baixando:
            self.status.config(text="Espere a fila terminar (ou cancele) para reiniciar.")
            return
        reiniciar_app()
        self.destroy()

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

    def definir_estado(self, iid, estado):
        self.lista.set(iid, "estado", estado)
        # Cor da linha pela situação final (as tags são configuradas em __init__)
        self.lista.item(iid, tags=(estado,) if estado in ("Concluído", "Erro", "Cancelado") else ())

    def adicionar(self):
        """Coloca na fila os links da caixa de texto; começa a baixar se estiver parado."""
        links = extrair_links(self.links.get("1.0", "end"))
        if not links:
            self.status.config(text="Nenhum link encontrado. Cole o endereço completo do vídeo.")
            return
        na_fila = {self.itens[i]["url"] for i in self.pendentes + list(self.ativos)}
        novos = [link for link in links if link not in na_fila]
        # Qualidade e pasta valem a partir de agora; itens já na fila mantêm as deles
        for url in novos:
            iid = self.lista.insert("", "end", values=(url, self.qualidade.get(), "Na fila"))
            self.itens[iid] = {"url": url, "qualidade": self.qualidade.get(), "pasta": self.pasta,
                               "desde_inicio": self.desde_inicio.get(), "mensagem": "Na fila."}
            self.pendentes.append(iid)
        self.links.delete("1.0", "end")

        texto = f"{len(novos)} link(s) adicionado(s) à fila."
        if len(novos) < len(links):
            texto += f" {len(links) - len(novos)} já estava(m) na fila."
        self.status.config(text=texto)
        if novos:
            if not self.baixando:
                self.resumo = dict.fromkeys(self.resumo, 0)
            self.iniciar_pendentes()

    def iniciar_pendentes(self):
        """Começa os próximos da fila enquanto houver vaga; lives gravando não ocupam vaga."""
        while self.pendentes and sum(not a["ao_vivo"] for a in self.ativos.values()) < LIMITE_VIDEOS:
            self.comecar(self.pendentes.pop(0))
        if not self.ativos and not self.pendentes:
            self.botao_cancelar.config(state="disabled", text="Cancelar")
            partes = [f"{self.resumo['Concluído']} baixado(s)"]
            if self.resumo["Erro"]:
                partes.append(f"{self.resumo['Erro']} com erro (clique no item para ver o motivo)")
            if self.resumo["Cancelado"]:
                partes.append(f"{self.resumo['Cancelado']} cancelado(s)")
            self.status.config(text="Fila concluída: " + ", ".join(partes) + ".")

    def comecar(self, iid):
        item = self.itens[iid]
        self.definir_estado(iid, "Começando...")
        self.lista.see(iid)
        fila, cancelar, parar = queue.Queue(), threading.Event(), threading.Event()
        args = (item["url"], fila, item["qualidade"], item["pasta"], cancelar, item["desde_inicio"], parar)
        thread = threading.Thread(target=baixar, args=args, daemon=True)
        thread.start()
        self.ativos[iid] = {"fila": fila, "cancelar": cancelar, "parar": parar, "thread": thread.ident,
                            "ao_vivo": False, "gravando": False, "cancelavel": True, "parando": False, "detalhes": None, "valor": 0, "texto": "Começando..."}
        self.focar(iid)
        self.after(100, self.atualizar, iid)

    def focar(self, iid):
        """Mostra na parte de baixo da janela (miniatura, barra, status, Cancelar) este download."""
        self.foco = iid
        ativo = self.ativos[iid]
        self.barra["value"] = ativo["valor"]
        self.status.config(text=ativo["texto"])
        if ativo["detalhes"]:
            self.mostrar_info(ativo["detalhes"])
        else:
            self.quadro_info.pack_forget()
        self.atualizar_botao_cancelar()

    def atualizar_botao_cancelar(self):
        ativo = self.ativos.get(self.foco)
        if ativo is None:
            self.botao_cancelar.config(state="disabled", text="Cancelar")
            return
        self.botao_cancelar.config(text="Parar e salvar" if ativo["ao_vivo"] else "Cancelar",
                                   state="normal" if ativo["cancelavel"] else "disabled")

    def cancelar(self):
        """Cancela o download em foco (a live é encerrada e salva); os outros continuam."""
        ativo = self.ativos.get(self.foco)
        if ativo is None:
            return
        if ativo["ao_vivo"] and ativo["gravando"]:
            ativo["parar"].set()  # live desde o início: o yt-dlp para no próximo pedaço
            parar_gravacao(ativo["thread"])  # live gravada pelo ffmpeg do app
            ativo["parando"] = True
            ativo["texto"] = "Encerrando a gravação e salvando..."
            self.definir_estado(self.foco, "Salvando...")
        else:
            # Live que ainda não começou a gravar é cancelada como um vídeo comum
            ativo["cancelar"].set()
            ativo["texto"] = "Cancelando..."
        ativo["cancelavel"] = False
        self.status.config(text=ativo["texto"])
        self.atualizar_botao_cancelar()

    def fechar(self):
        # O ffmpeg é outro processo e continuaria gravando a live depois de a janela fechar
        for proc in list(PROCESSOS_FFMPEG.values()):
            try:
                proc.kill()
            except OSError:
                pass
        self.destroy()

    def remover_selecionados(self):
        for iid in self.lista.selection():
            if iid in self.ativos:
                continue  # o que está baixando se interrompe pelo Cancelar
            if iid in self.pendentes:
                self.pendentes.remove(iid)
            self.itens.pop(iid, None)
            self.lista.delete(iid)

    def limpar_concluidos(self):
        for iid in list(self.itens):
            if iid not in self.ativos and iid not in self.pendentes:
                self.itens.pop(iid)
                self.lista.delete(iid)

    def mostrar_mensagem_item(self, _evento=None):
        selecao = self.lista.selection()
        if len(selecao) != 1:
            return
        if selecao[0] in self.ativos:
            # Clicar num download em andamento mostra o progresso dele e liga o Cancelar a ele
            self.focar(selecao[0])
        elif selecao[0] in self.itens:
            self.status.config(text=self.itens[selecao[0]]["mensagem"])

    def mostrar_info(self, detalhes):
        self.rotulo_titulo.config(text=detalhes["titulo"])
        self.rotulo_detalhes.config(text=" · ".join(t for t in (detalhes["canal"], detalhes["duracao"]) if t))
        if detalhes["imagem"] is not None:
            from PIL import ImageTk
            self._imagem_tk = ImageTk.PhotoImage(detalhes["imagem"])
            self.miniatura.config(image=self._imagem_tk)
        else:
            self._imagem_tk = None
            self.miniatura.config(image="")
        # Fica logo acima da barra de progresso
        self.quadro_info.pack(fill="x", pady=(12, 0), before=self.barra)

    def atualizar(self, iid):
        # Só a thread principal pode mexer na interface do Tkinter
        ativo = self.ativos[iid]
        try:
            while True:
                tipo, valor, texto = ativo["fila"].get_nowait()
                if tipo == "sem_cancelar":
                    ativo["cancelavel"] = False
                    if iid == self.foco:
                        self.atualizar_botao_cancelar()
                    continue
                cancelando = ativo["cancelar"].is_set() or ativo["parando"]
                if tipo == "info":
                    ativo["detalhes"] = valor
                    self.lista.set(iid, "video", valor["titulo"])
                    if iid == self.foco:
                        self.mostrar_info(valor)
                    if valor["ao_vivo"] and not ativo["ao_vivo"]:
                        ativo["ao_vivo"] = True
                        if iid == self.foco:
                            self.atualizar_botao_cancelar()
                        self.iniciar_pendentes()  # a live grava por horas; o próximo já pode começar
                elif tipo == "progresso":
                    ativo["valor"] = valor
                    if ativo["ao_vivo"] and texto.startswith("Gravando"):
                        ativo["gravando"] = True
                    if texto.startswith(("Juntando", "Convertendo")):
                        etapa = "Finalizando..."
                    else:
                        etapa = "Ao vivo" if ativo["ao_vivo"] else f"{valor:.0f}%"
                    # Depois de pedir para cancelar/parar, não deixa o progresso apagar o aviso
                    if not cancelando:
                        self.definir_estado(iid, etapa)
                if tipo == "fim" or not cancelando:
                    ativo["texto"] = texto
                    if iid == self.foco:
                        self.status.config(text=texto)
                        self.barra["value"] = ativo["valor"]
                if tipo == "fim":
                    # O resultado vem do próprio download: um clique em Cancelar depois que
                    # ele já terminou (antes de a janela ler esta mensagem) não muda nada
                    estado = valor
                    self.resumo[estado] += 1
                    self.itens[iid]["mensagem"] = texto
                    self.definir_estado(iid, estado)
                    del self.ativos[iid]
                    if iid == self.foco:
                        self.barra["value"] = 100 if estado == "Concluído" else 0
                        self.foco = None
                        self.atualizar_botao_cancelar()
                    self.iniciar_pendentes()
                    # Sem um novo começando, passa a mostrar outro que ainda está baixando
                    if self.foco is None and self.ativos:
                        self.focar(next(reversed(self.ativos)))
                    return
        except queue.Empty:
            pass
        self.after(100, self.atualizar, iid)


if __name__ == "__main__":
    # Faz o Windows agrupar a janela com o ícone do app na barra de tarefas
    # (rodando pelo Python, senão apareceria o ícone do python.exe)
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BaixarVideos.App")
    except (AttributeError, OSError):
        pass
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.fechar)
    app.mainloop()
