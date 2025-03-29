import os
import json
import re
import base64
import time
from datetime import datetime
from collections import defaultdict
from flask import Flask, render_template, request, jsonify
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from redis import Redis
from rq import Queue
from dotenv import load_dotenv # Opcional, para .env

# Carregar variáveis de ambiente do arquivo .env (se existir)
load_dotenv()

# Configurações do Designi
URL_LOGIN = os.getenv('DESIGNI_LOGIN_URL', 'https://designi.com.br/login')
EMAIL = os.getenv('DESIGNI_EMAIL') # Removido valor padrão, deve ser configurado
SENHA = os.getenv('DESIGNI_PASSWORD') # Removido valor padrão, deve ser configurado
CAPTCHA_API_KEY = os.getenv('CAPTCHA_API_KEY') # Removido valor padrão

# Configurações Freepik e Google Drive
FREEPIK_API_KEY = os.getenv("FREEPIK_API_KEY") # Removido valor padrão
FOLDER_ID = os.getenv('GOOGLE_DRIVE_FOLDER_ID', '18JkCOexQ7NdzVgmK0WvKyf53AHWKQyyV') # Mantido padrão se não definido
GOOGLE_CREDENTIALS_BASE64 = os.getenv('GOOGLE_CREDENTIALS_BASE64') # NOVA variável

# Configuração Flask e Redis
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-should-be-changed') # Mude isso em produção!

# Conexão Redis (para RQ e limites de taxa)
# Render injeta REDIS_URL automaticamente se um serviço Redis estiver linkado
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379') # Fallback para local
try:
    # Configuração para compatibilidade com Upstash/Redis Cloud (TLS)
    if 'rediss://' in REDIS_URL or 'upstash.io' in REDIS_URL or 'redis.com' in REDIS_URL:
         redis_conn = Redis.from_url(REDIS_URL, ssl_cert_reqs=None)
    else:
         redis_conn = Redis.from_url(REDIS_URL)
    redis_conn.ping() # Testa a conexão
    print("[APP LOG] Conectado ao Redis com sucesso.")
except Exception as redis_err:
     print(f"[APP ERROR] Falha ao conectar ao Redis em {REDIS_URL}: {redis_err}")
     # Em um app real, você pode querer parar aqui ou usar um fallback
     redis_conn = None # Define como None para checagens posteriores

# Fila RQ (usará a mesma conexão Redis)
if redis_conn:
    rq_queue = Queue("default", connection=redis_conn) # Usando a fila 'default'
else:
    rq_queue = None
    print("[APP WARNING] RQ não pode ser inicializado devido à falha na conexão Redis.")


# Sistema de armazenamento em memória para limite de taxa (fallback se Redis falhar)
class LocalStorageRateLimit:
    def __init__(self):
        self.data = defaultdict(lambda: {'count': 0, 'expiry': 0})
        print("[APP WARNING] Usando armazenamento local para limite de taxa (Redis indisponível).")

    def get(self, key):
        item = self.data[key]
        if time.time() > item['expiry']:
            del self.data[key]
            return None
        return str(item['count']).encode()

    def set(self, key, value, ex=None):
        expiry_time = time.time() + ex if ex else float('inf')
        self.data[key] = {'count': int(value), 'expiry': expiry_time}

    def incr(self, key):
        item = self.data[key]
        # Se expirou, reseta antes de incrementar
        if time.time() > item['expiry']:
             item['count'] = 0
             # Se não havia TTL, não define um novo aqui. set() define o TTL inicial.
             # Se havia TTL, ele será reiniciado na próxima chamada a set() com ex.
        item['count'] += 1
        return item['count']

# Usa Redis se disponível, senão usa fallback local para limite de taxa
rate_limiter = redis_conn if redis_conn else LocalStorageRateLimit()

# Diretório temporário para uploads do Freepik (o worker usará /tmp)
APP_TEMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'arquivos_temporarios_app')
os.makedirs(APP_TEMP_DIR, exist_ok=True)

# --- Funções Auxiliares (Apenas as necessárias para o app web) ---

def get_client_ip():
    """Obtém o IP real do cliente, considerando proxies."""
    if request.headers.getlist("X-Forwarded-For"):
        # Pega o primeiro IP da lista X-Forwarded-For
        client_ip = request.headers.getlist("X-Forwarded-For")[0].split(',')[0].strip()
    elif request.headers.get("X-Real-IP"):
        client_ip = request.headers.get("X-Real-IP").strip()
    else:
        client_ip = request.remote_addr or '127.0.0.1'
    # Simples validação de formato IPv4/IPv6 (não muito rigorosa)
    if not re.match(r"^[0-9a-fA-F.:]+$", client_ip):
         print(f"[APP WARNING] IP detectado '{client_ip}' parece inválido, usando fallback 127.0.0.1")
         return '127.0.0.1' # Fallback para IP inválido
    return client_ip

def get_drive_service():
    """Cria o serviço do Drive usando a variável de ambiente (usado APENAS pelo /upload)."""
    # A tarefa do worker usa get_drive_service_from_credentials
    return get_drive_service_from_credentials(GOOGLE_CREDENTIALS_BASE64)

def limpar_arquivos_temporarios(directory, max_idade_horas=6):
    """Limpa arquivos temporários antigos no diretório especificado."""
    if not os.path.exists(directory):
        return
    try:
        print(f"[CLEANUP] Verificando diretório temporário: {directory}")
        tempo_atual = time.time()
        arquivos_removidos = 0
        limite_tempo = tempo_atual - (max_idade_horas * 3600)

        for nome_arquivo in os.listdir(directory):
            caminho_arquivo = os.path.join(directory, nome_arquivo)
            try:
                if os.path.isfile(caminho_arquivo):
                    tempo_modificacao = os.path.getmtime(caminho_arquivo)
                    if tempo_modificacao < limite_tempo:
                        os.remove(caminho_arquivo)
                        arquivos_removidos += 1
                        print(f"[CLEANUP] Removido arquivo antigo: {nome_arquivo}")
            except Exception as e_inner:
                print(f"[CLEANUP ERROR] Falha ao processar/remover {caminho_arquivo}: {e_inner}")

        if arquivos_removidos > 0:
            print(f"[CLEANUP] Total de {arquivos_removidos} arquivos temporários antigos removidos de {directory}.")
    except Exception as e:
        print(f"[CLEANUP ERROR] Erro ao limpar diretório {directory}: {str(e)}")

# --- Rotas Flask ---

@app.route('/')
def home():
    # Limpa arquivos temporários do app web (não do worker) na home page
    limpar_arquivos_temporarios(APP_TEMP_DIR)
    return render_template('index.html')

@app.route('/status')
def user_status():
    """Verifica o limite de downloads do usuário."""
    if not rate_limiter:
         return '<div class="alert alert-warning">Serviço de limite de taxa indisponível.</div>'
    try:
        client_ip = get_client_ip()
        downloads_key = f"downloads:{client_ip}"
        downloads_hoje_raw = rate_limiter.get(downloads_key)
        downloads_hoje = int(downloads_hoje_raw) if downloads_hoje_raw else 0

        limite_diario = 2 # Definir limite aqui
        downloads_restantes = max(0, limite_diario - downloads_hoje)

        if downloads_restantes > 0:
            return f'<div class="alert alert-info">Você tem {downloads_restantes} downloads restantes hoje (de {limite_diario}).</div>'
        else:
            return f'<div class="alert alert-warning">Você atingiu o limite de {limite_diario} downloads hoje. Tente novamente amanhã!</div>'
    except Exception as e:
        print(f"[APP ERROR] Erro ao verificar status para IP {get_client_ip()}: {str(e)}")
        return '<div class="alert alert-danger">Erro ao verificar status de download.</div>'

@app.route('/upload', methods=['POST'])
def upload():
    """Faz download do Freepik (STREAMING) e upload para o Google Drive."""
    filename = None
    temp_file_path = None
    limite_diario = 2

    if not FREEPIK_API_KEY:
        return "<div class='alert alert-danger'>❌ Chave da API do Freepik não configurada no servidor.</div>", 400
    if not GOOGLE_CREDENTIALS_BASE64:
         return "<div class='alert alert-danger'>❌ Credenciais do Google Drive não configuradas no servidor.</div>", 500
    if not rate_limiter:
         return "<div class='alert alert-danger'>❌ Serviço de limite de taxa indisponível.</div>", 503

    try:
        # 1. Verificar limite de downloads
        client_ip = get_client_ip()
        downloads_key = f"downloads:{client_ip}"
        downloads_hoje_raw = rate_limiter.get(downloads_key)
        downloads_hoje = int(downloads_hoje_raw) if downloads_hoje_raw else 0

        if downloads_hoje >= limite_diario:
            print(f"[APP INFO] Limite de download atingido para IP: {client_ip}")
            return f"<div class='alert alert-danger'>❌ Você atingiu o limite de {limite_diario} downloads hoje. Tente novamente amanhã!</div>", 429

        # 2. Obter e validar link do Freepik
        freepik_link = request.form.get('freepik_link')
        if not freepik_link:
             return "<div class='alert alert-danger'>❌ Link do Freepik não fornecido.</div>", 400
        match = re.search(r'(_|\/)([1-9]\d+)\.(htm|jpg)', freepik_link) # Regex mais flexível
        if not match:
            return "<div class='alert alert-danger'>❌ Link do Freepik inválido ou não reconhecido. Use o link da página do recurso.</div>", 400
        image_id = match.group(2)
        print(f"[APP LOG] Processando Freepik ID: {image_id} para IP: {client_ip}")

        # 3. Obter URL de download da API Freepik
        headers = {"x-freepik-api-key": FREEPIK_API_KEY, "Accept": "application/json"}
        api_url = f"https://api.freepik.com/v1/resources/{image_id}/download"
        try:
            api_response = requests.get(api_url, headers=headers, timeout=15)
            api_response.raise_for_status() # Verifica erros 4xx/5xx
            response_data = api_response.json()
        except requests.exceptions.RequestException as api_err:
             print(f"[APP ERROR] Falha ao chamar API Freepik ({api_url}): {api_err}")
             status_code = api_err.response.status_code if api_err.response else 500
             error_detail = api_err.response.text if api_err.response else str(api_err)
             if status_code == 404:
                  return f"<div class='alert alert-danger'>❌ Recurso Freepik (ID: {image_id}) não encontrado. Verifique o link.</div>", 404
             elif status_code == 401 or status_code == 403:
                  return f"<div class='alert alert-danger'>❌ Erro de autenticação com API Freepik. Verifique a API Key.</div>", status_code
             else:
                  return f"<div class='alert alert-danger'>❌ Erro ao comunicar com API Freepik: {error_detail}</div>", status_code


        if 'data' not in response_data or 'url' not in response_data['data']:
            print(f"[APP ERROR] Resposta inesperada da API Freepik: {response_data}")
            return "<div class='alert alert-danger'>❌ Erro ao obter URL de download do Freepik. Resposta inesperada da API.</div>", 500

        download_url = response_data['data']['url']
        file_format = response_data['data'].get('format', 'unknown') # ex: 'jpg', 'eps', 'psd'

        # 4. Download do arquivo via STREAMING
        print(f"[APP LOG] Iniciando download por streaming de: {download_url}")
        try:
            with requests.get(download_url, stream=True, timeout=600) as r: # Timeout generoso (10 min)
                r.raise_for_status()

                # Determinar nome e extensão do arquivo
                content_disposition = r.headers.get('content-disposition')
                if content_disposition:
                    fname_match = re.search('filename="?([^"]+)"?', content_disposition)
                    if fname_match:
                        filename = fname_match.group(1)
                if not filename:
                     # Fallback usando ID e formato da API, ou extensão do Content-Type
                     content_type = r.headers.get('content-type', '').split(';')[0]
                     ext = mimetypes.guess_extension(content_type) or f".{file_format}" if file_format != 'unknown' else ".file"
                     filename = f"freepik_{image_id}{ext}"

                # Sanitizar filename (básico)
                filename = re.sub(r'[\\/*?:"<>|]', "_", filename) # Remove caracteres inválidos
                temp_file_path = os.path.join(APP_TEMP_DIR, filename)

                print(f"[APP LOG] Salvando em: {temp_file_path}")
                bytes_written = 0
                with open(temp_file_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=81920): # Chunk maior (80KB)
                        if chunk:
                            f.write(chunk)
                            bytes_written += len(chunk)
                print(f"[APP LOG] Download por streaming concluído. {bytes_written} bytes escritos.")
                if bytes_written == 0:
                    raise IOError("Download concluído, mas nenhum byte foi escrito.")

        except requests.exceptions.Timeout:
            print(f"[APP ERROR] Timeout ao baixar arquivo do Freepik: {download_url}")
            return f"<div class='alert alert-danger'>❌ Tempo limite excedido ao baixar o arquivo do Freepik. Tente novamente.</div>", 504
        except requests.exceptions.RequestException as dl_err:
            print(f"[APP ERROR] Erro de rede ao baixar arquivo do Freepik: {dl_err}")
            return f"<div class='alert alert-danger'>❌ Erro de rede ao baixar o arquivo do Freepik: {str(dl_err)}</div>", 502
        except IOError as io_err:
             print(f"[APP ERROR] Erro ao salvar arquivo baixado: {io_err}")
             return f"<div class='alert alert-danger'>❌ Erro ao salvar o arquivo baixado: {str(io_err)}</div>", 500


        # 5. Upload para o Google Drive
        print("[APP LOG] Iniciando upload para Google Drive...")
        drive_service = get_drive_service()
        if not drive_service:
            return "<div class='alert alert-danger'>❌ Erro ao conectar com o Google Drive (serviço não obtido).</div>", 500

        try:
            file_metadata = {'name': filename}
            if FOLDER_ID:
                file_metadata['parents'] = [FOLDER_ID]

            mimetype, _ = mimetypes.guess_type(temp_file_path)
            mimetype = mimetype or 'application/octet-stream'

            media = MediaFileUpload(temp_file_path, mimetype=mimetype, resumable=True)
            file = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink' # Corrigido
            ).execute()

            drive_service.permissions().create(
                fileId=file.get('id'),
                body={'role': 'reader', 'type': 'anyone'}
            ).execute()
            print(f"[APP LOG] Upload para Drive concluído. File ID: {file.get('id')}")

        except Exception as drive_err:
            print(f"[APP ERROR] Erro durante upload para o Google Drive: {drive_err}")
            return f"<div class='alert alert-danger'>❌ Erro ao fazer upload para o Google Drive: {str(drive_err)}</div>", 500

        # 6. Incrementar contador de downloads (SOMENTE SE TUDO DEU CERTO)
        try:
            if downloads_hoje == 0:
                # Define com expiração de 24h (em segundos)
                ttl_seconds = 86400
                rate_limiter.set(downloads_key, 1, ex=ttl_seconds)
                print(f"[APP INFO] Primeiro download registrado para IP: {client_ip} (TTL: {ttl_seconds}s)")
            else:
                # Apenas incrementa, o TTL original (se houver) é mantido pelo Redis
                # Para o fallback local, o incr não renova o TTL, o que é ok.
                new_count = rate_limiter.incr(downloads_key)
                print(f"[APP INFO] Download incrementado para IP: {client_ip} (Novo total: {new_count})")
        except Exception as redis_inc_err:
             # Falha ao incrementar não deve impedir o sucesso do download, mas logar
             print(f"[APP WARNING] Falha ao incrementar contador de download para {client_ip}: {redis_inc_err}")


        # 7. Retornar sucesso
        success_html = f"""
        <div class="card-body">
            <div class="alert alert-success mb-3">✅ Upload concluído com sucesso!</div>
            <div class="mb-2"><strong>ID do arquivo:</strong> {file.get('id')}</div>
            <div class="mb-3">
                <strong>Link para download:</strong><br>
                <a href="{file.get('webViewLink')}" target="_blank" class="btn btn-sm btn-outline-primary mt-2">
                    <i class="bi bi-download"></i> Baixar do Google Drive
                </a>
            </div>
            <div class="text-muted small">{filename} • {datetime.now().strftime('%d/%m/%Y %H:%M')}</div>
        </div>
        """
        return success_html, 200 # Status 200 OK

    except Exception as e:
        # Captura qualquer outra exceção não tratada
        print(f"[APP ERROR] Erro inesperado na rota /upload: {str(e)}")
        import traceback
        traceback.print_exc() # Imprime stack trace no log do servidor
        return f'<div class="alert alert-danger">⚠️ Erro inesperado no servidor: {str(e)}</div>', 500

    finally:
        # Limpeza do arquivo temporário do upload, se existir
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                print(f"[APP LOG] Arquivo temporário de upload removido: {temp_file_path}")
            except Exception as e_clean:
                print(f"[APP WARNING] Erro ao remover arquivo temporário de upload {temp_file_path}: {str(e_clean)}")
        # Limpeza geral periódica pode ser feita em outro lugar ou na home
        # limpar_arquivos_temporarios(APP_TEMP_DIR)


@app.route('/download-designi', methods=['POST'])
def download_designi():
    """Enfileira a tarefa de download do Designi para o worker RQ."""
    limite_diario = 2

    # Verificar dependências críticas
    if not rq_queue:
        return jsonify({'success': False, 'error': 'Serviço de background indisponível (RQ não conectado).'}), 503
    if not rate_limiter:
        return jsonify({'success': False, 'error': 'Serviço de limite de taxa indisponível.'}), 503
    if not EMAIL or not SENHA:
        return jsonify({'success': False, 'error': 'Credenciais do Designi não configuradas no servidor.'}), 500
    if not GOOGLE_CREDENTIALS_BASE64:
         return jsonify({'success': False, 'error': 'Credenciais do Google Drive não configuradas no servidor.'}), 500

    try:
        # 1. Verificar limite de downloads
        client_ip = get_client_ip()
        downloads_key = f"downloads:{client_ip}"
        downloads_hoje_raw = rate_limiter.get(downloads_key)
        downloads_hoje = int(downloads_hoje_raw) if downloads_hoje_raw else 0

        if downloads_hoje >= limite_diario:
            print(f"[APP INFO] Limite de download atingido para IP: {client_ip} (Rota Designi)")
            return jsonify({'success': False, 'error': f'Você atingiu o limite de {limite_diario} downloads hoje. Tente novamente amanhã!'}), 429

        # 2. Obter e validar URL do Designi
        data = request.json
        url = data.get('url')
        if not url or not url.startswith('http'): # Validação básica
            return jsonify({'success': False, 'error': 'URL do Designi inválida ou não fornecida.'}), 400
        print(f"[APP LOG] Requisição de download Designi para URL: {url} (IP: {client_ip})")

        # 3. Enfileirar a tarefa no RQ
        try:
            job = rq_queue.enqueue(
                'tasks.perform_designi_download_task', # Caminho para a função no tasks.py
                args=(
                    url,
                    client_ip,
                    FOLDER_ID,
                    EMAIL,
                    SENHA,
                    CAPTCHA_API_KEY,
                    GOOGLE_CREDENTIALS_BASE64,
                    URL_LOGIN
                ),
                job_timeout=1800 # Timeout para a tarefa em si (30 minutos) - ajuste conforme necessário
                # result_ttl=3600 # Quanto tempo manter o resultado no Redis (1h)
                # failure_ttl=86400 # Quanto tempo manter falhas no Redis (24h)
            )
            print(f"[APP LOG] Tarefa Designi enfileirada com ID: {job.id}")

            # 4. Incrementar contador (APÓS enfileirar com sucesso)
            # Tratamento de erro para incremento é feito separadamente
            try:
                 if downloads_hoje == 0:
                     ttl_seconds = 86400
                     rate_limiter.set(downloads_key, 1, ex=ttl_seconds)
                     print(f"[APP INFO] Primeiro download Designi registrado para IP: {client_ip} (TTL: {ttl_seconds}s)")
                 else:
                     new_count = rate_limiter.incr(downloads_key)
                     print(f"[APP INFO] Download Designi incrementado para IP: {client_ip} (Novo total: {new_count})")
            except Exception as redis_inc_err:
                 print(f"[APP WARNING] Falha ao incrementar contador (Designi) para {client_ip}: {redis_inc_err}")


            # 5. Retornar sucesso (indicando que a tarefa foi iniciada)
            return jsonify({
                'success': True,
                'message': 'Seu download foi iniciado em segundo plano. O link estará disponível no Google Drive em breve.',
                'job_id': job.id # Opcional: pode ser usado para consultar status depois (não implementado aqui)
            }), 202 # HTTP 202 Accepted

        except Exception as enqueue_err:
            print(f"[APP ERROR] Falha ao enfileirar tarefa RQ: {enqueue_err}")
            return jsonify({'success': False, 'error': 'Falha ao iniciar o processo de download em segundo plano.'}), 500

    except Exception as e:
        # Captura qualquer outra exceção não tratada
        print(f"[APP ERROR] Erro inesperado na rota /download-designi: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'Erro inesperado no servidor: {str(e)}'}), 500

# Execução Principal (para Gunicorn)
if __name__ == '__main__':
    # Limpeza inicial (opcional, worker também pode fazer)
    print("[APP STARTUP] Limpando diretório temporário do app web...")
    limpar_arquivos_temporarios(APP_TEMP_DIR)
    # A execução via 'flask run' é apenas para desenvolvimento local
    # Gunicorn usará 'app:app' diretamente
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False) # DEBUG FALSE em produção!