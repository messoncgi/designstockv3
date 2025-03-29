# Dockerfile (Atualizado para usar entrypoint.sh)

# 1. Base Image: Usar a imagem oficial do Playwright
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

# 2. Set Environment Variables
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# 3. Set Working Directory
WORKDIR /app

# 4. Copy Requirements & Install Python Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Install/Verify Playwright Browsers
RUN echo "Installing/Verifying Playwright Chromium browser..." && \
    playwright install --with-deps chromium && \
    echo "Browser installation step completed." && \
    echo "Listing installed browsers directory:" && \
    ls -l $PLAYWRIGHT_BROWSERS_PATH/ && \
    echo "Listing specific expected browser folder:" && \
    ls -l $PLAYWRIGHT_BROWSERS_PATH/chromium-1105/

# 6. REMOVIDO: Bloco do Link Simbólico

# 7. Copy Application Code AND entrypoint script
COPY . .
# Garante que o entrypoint tem permissão de execução dentro do container também
RUN chmod +x /app/entrypoint.sh

# 8. Expose Port (Documentação/Padrão)
EXPOSE 10000 # O Render usa $PORT, mas expor é boa prática

# 9. Define o ENTRYPOINT para usar o script
ENTRYPOINT ["/app/entrypoint.sh"]

# 10. CMD removido ou comentado (ENTRYPOINT tem prioridade se ambos definidos)
# CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:$PORT", "--workers", "3", "--timeout", "120", "--log-level=info"]