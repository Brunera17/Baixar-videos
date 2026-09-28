"""Gera assets/icone.ico (ícone do .exe) e assets/icone.png (ícone da janela).

Rode com: python ferramentas/gerar_icone.py
O desenho é feito em 1024 px e reduzido, para as bordas ficarem suaves em todos os tamanhos.
"""
from pathlib import Path

from PIL import Image, ImageDraw

TAMANHO = 1024
PASTA = Path(__file__).resolve().parent.parent / "assets"

# Vermelho em degradê (de cima para baixo), lembrando o botão de play do YouTube
COR_TOPO = (239, 68, 68)
COR_BASE = (185, 28, 28)
BRANCO = (255, 255, 255, 255)


def degrade(tamanho, topo, base):
    faixa = Image.new("RGB", (1, tamanho))
    for y in range(tamanho):
        t = y / (tamanho - 1)
        faixa.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(topo, base)))
    return faixa.resize((tamanho, tamanho))


def desenhar():
    s = TAMANHO
    # Fundo: quadrado de cantos arredondados com degradê
    mascara = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mascara).rounded_rectangle((0, 0, s - 1, s - 1), radius=s * 0.22, fill=255)
    icone = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    icone.paste(degrade(s, COR_TOPO, COR_BASE), (0, 0), mascara)

    d = ImageDraw.Draw(icone)
    # Seta para baixo: haste + ponta
    haste = s * 0.13
    cx = s / 2
    d.rounded_rectangle((cx - haste / 2, s * 0.18, cx + haste / 2, s * 0.56), radius=haste / 2, fill=BRANCO)
    d.polygon([(s * 0.25, s * 0.47), (s * 0.75, s * 0.47), (cx, s * 0.73)], fill=BRANCO)
    # Base (a "bandeja" onde o download cai)
    d.rounded_rectangle((s * 0.22, s * 0.78, s * 0.78, s * 0.78 + haste * 0.85), radius=haste * 0.42, fill=BRANCO)
    return icone


def main():
    PASTA.mkdir(exist_ok=True)
    icone = desenhar()
    icone.resize((256, 256), Image.LANCZOS).save(PASTA / "icone.png")
    # O .ico guarda vários tamanhos; o Windows escolhe o certo para cada lugar
    icone.save(PASTA / "icone.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Gerado:", PASTA / "icone.ico", "e", PASTA / "icone.png")


if __name__ == "__main__":
    main()
