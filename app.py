import yt_dlp

# URL do vídeo que você deseja baixar
url = "https://www.youtube.com/watch?v=xBzvIvzWScw"

ydl_opts = {
    'format': 'best',
    'outtmpl': 'downloads/%(title)s.%(ext)s'
}

try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    print("Download concluído!")
except Exception as e:
    print(f"Ocorreu um erro: {e}")
