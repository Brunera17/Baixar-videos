"""Imprime VERSAO_APP do app.py (usado pelo build.bat), sem importar o app."""
import re
from pathlib import Path

codigo = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
print(re.search(r'^VERSAO_APP = "([^"]+)"', codigo, re.MULTILINE).group(1))
