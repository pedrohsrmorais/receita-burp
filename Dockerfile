# Imagem oficial do Playwright para Python: ja traz o Chromium, as dependencias
# de sistema e o pacote `playwright` na versao casada (1.64.1). Nada a instalar
# alem do proprio script.
FROM mcr.microsoft.com/playwright/python:v1.64.1-jammy

WORKDIR /app

# A imagem traz os browsers em /ms-playwright (via PLAYWRIGHT_BROWSERS_PATH),
# mas nao o pacote pip. Instala so o pacote na versao casada; nenhum browser
# e baixado (ja estao na imagem).
RUN pip3 install --no-cache-dir playwright==1.64.1

# O browser ja esta instalado na imagem; o script acha sozinho (CHROME_PATH vazio).
# xvfb-run e ffmpeg ja vem na imagem oficial (headful em container + video).
COPY receita_cookies.py /app/receita_cookies.py
COPY fluxo_govbr.py /app/fluxo_govbr.py

# Saida vai para /out (montado pelo compose). Espera 3s por padrao.
ENV OUT_PATH=/out/receita_cookies.json \
    WAIT_SECONDS=3 \
    PYTHONUNBUFFERED=1

CMD ["python3", "receita_cookies.py"]
