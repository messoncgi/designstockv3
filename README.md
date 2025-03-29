# DesignStock

Um aplicativo web Flask para baixar recursos do Freepik e fazer upload automático para o Google Drive.

## Funcionalidades

- Download de recursos do Freepik usando a API oficial
- Upload automático para o Google Drive
- Interface web amigável
- Limite de downloads por IP
- Suporte a Redis para gerenciamento de filas e limites de taxa

## Requisitos

- Python 3.8+
- Flask
- Playwright
- Redis (opcional, para limites de taxa e filas)
- Credenciais da API do Freepik
- Credenciais de Conta de Serviço do Google Drive

## Configuração

### Variáveis de Ambiente

Crie um arquivo `.env` na raiz do projeto com as seguintes variáveis:

```
# Freepik API
FREEPIK_API_KEY=sua_chave_api_freepik

# Google Drive
GOOGLE_CREDENTIALS_BASE64=suas_credenciais_base64_codificadas
GOOGLE_DRIVE_FOLDER_ID=id_da_pasta_no_drive

# Redis (opcional)
REDIS_URL=sua_url_redis

# Flask
SECRET_KEY=chave_secreta_para_flask
```

### Instalação

1. Clone o repositório
2. Instale as dependências: `pip install -r requirements.txt`
3. Instale os navegadores do Playwright: `playwright install chromium`

## Execução

### Desenvolvimento Local

```bash
# Iniciar o servidor web
python app.py

# Iniciar o worker (em outro terminal)
python worker.py
```

### Produção

O projeto inclui um Dockerfile para implantação em contêineres. Você pode implantá-lo em serviços como Render, Heroku ou qualquer plataforma que suporte Docker.

```bash
# Construir a imagem Docker
docker build -t designstock .

# Executar o contêiner
docker run -p 10000:10000 --env-file .env designstock
```

## Estrutura do Projeto

- `app.py` - Aplicativo Flask principal
- `tasks.py` - Funções para tarefas em segundo plano
- `worker.py` - Worker RQ para processamento de filas
- `templates/` - Templates HTML
- `Dockerfile` - Configuração para implantação Docker

## Licença

Este projeto é para uso privado e não está licenciado para distribuição pública.