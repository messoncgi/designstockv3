# Dockerfile (Opção 1 - Sem Link Simbólico)

# 1. Base Image: Usar a imagem oficial do Playwright
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy
# Verifique se v1.42.0 é a versão desejada ou use 'latest-jammy'

# 2. Set Environment Variables
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    # Caminho padrão dos browsers nesta imagem base (ESSENCIAL!)
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# 3. Set Working Directory
WORKDIR /app

# 4. Copy Requirements & Install Python Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Install/Verify Playwright Browsers
# Garante que o Chromium está instalado no local esperado por PLAYWRIGHT_BROWSERS_PATH
# O '--with-deps' é menos crucial aqui, mas não prejudica.
RUN echo "Installing/Verifying Playwright Chromium browser..." && \
    playwright install --with-deps chromium && \
    echo "Browser installation step completed." && \
    # Comando opcional para verificar onde o browser foi instalado (útil para debug no log de build):
    echo "Listing installed browsers directory:" && \
    ls -l $PLAYWRIGHT_BROWSERS_PATH/

# 6. *** REMOVIDO: Bloco do Link Simbólico ***

# 7. Copy Application Code
COPY . .

# 8. Expose Port (Documentação/Padrão)
EXPOSE 10000

# 9. Default CMD (Será sobrescrito pelo Start Command do Render)
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:$PORT", "--workers", "3", "--timeout", "120", "--log-level=info"]