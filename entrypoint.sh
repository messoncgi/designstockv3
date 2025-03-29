#!/bin/sh

# Sair imediatamente se um comando falhar
set -e

# Verifica se a variável PORT está definida, senão usa um padrão (ex: 8000)
# Embora o Render deva SEMPRE definir PORT para Web Services.
PORT=${PORT:-8000}

echo "Starting Gunicorn..."
echo "Listening on: 0.0.0.0:${PORT}"
echo "Worker Temp Dir: /dev/shm" # Adicionado para log

# Executa o Gunicorn usando a variável PORT
# O 'exec' substitui o processo do shell pelo processo do gunicorn,
# o que é bom para o gerenciamento de sinais do Docker/Render.
exec gunicorn app:app \
    --worker-tmp-dir /dev/shm \
    --workers=3 \
    --timeout=120 \
    --bind=0.0.0.0:${PORT} \
    --log-level=info