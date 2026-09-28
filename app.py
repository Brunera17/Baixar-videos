import yt_dlp
import imageio_ffmpeg

# URL do vídeo que você deseja baixar
url = "https://www.youtube.com/watch?v=xBzvIvzWScw"

ydl_opts = {
    # O YouTube entrega vídeo e áudio separados; o ffmpeg junta os dois em um .mp4
    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best',
    'merge_output_format': 'mp4',
    'ffmpeg_location': imageio_ffmpeg.get_ffmpeg_exe(),
    # Usa o Node.js instalado para resolver o JavaScript do YouTube
    'js_runtimes': {'node': {}},
    'outtmpl': 'downloads/%(title)s.%(ext)s'
}

try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    print("Download concluído!")
except Exception as e:
    print(f"Ocorreu um erro: {e}")
