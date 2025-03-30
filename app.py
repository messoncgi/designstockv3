import os
import json
import re
import base64
import time
import mimetypes
from datetime import datetime, timedelta
from collections import defaultdict # Re-adicionado para fallback
from flask import Flask, render_template, request, jsonify, session
import requests
from redis import Redis
from rq import Queue
from rq.job import Job
from rq.exceptions import NoSuchJobError
from dotenv import load_dotenv
from urllib.parse import urlparse
from flask_session import Session

load_dotenv()

# Configurações (Inalteradas)
URL_LOGIN = os.getenv('DESIGNI_LOGIN_URL', 'https://designi.com.br/login')
EMAIL = os.getenv('DESIGNI_EMAIL')
SENHA = os.getenv('DESIGNI_PASSWORD')
CAPTCHA_API_KEY = os.getenv('CAPTCHA_API_KEY')
FREEPIK_API_KEY = os.getenv("FREEPIK_API_KEY")
FOLDER_ID = os.getenv('GOOGLE_DRIVE_FOLDER_ID', '18JkCOexQ7NdzVgmK0WvKyf53AHWKQyyV')
GOOGLE_CREDENTIALS_BASE64 = os.getenv('GOOGLE_CREDENTIALS_BASE64')

# Configuração Flask
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-should-be-changed')

# Configuração da sessão Flask
app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)  # Sessão válida por 7 dias
app.config['SESSION_USE_SIGNER'] = True  # Assinar cookies de sessão
app.config['SESSION_KEY_PREFIX'] = 'designi_session:'  # Prefixo para chaves no Redis

# Conexão Redis (com decode_responses=False)
REDIS_URL = os.environ.get('REDIS_URL')
redis_conn = None
rq_queue = None
session_redis = None

if REDIS_URL:
    try:
        # (Lógica de conexão inalterada, usando parâmetros separados e decode_responses=False)
        print(f"[APP LOG] Tentando conectar ao Redis usando URL: {REDIS_URL}")
        parsed_url = urlparse(REDIS_URL)
        redis_host = parsed_url.hostname; redis_port = parsed_url.port or 6379; redis_password = parsed_url.password
        use_ssl = parsed_url.scheme == 'rediss' or (redis_host and 'upstash.io' in redis_host)
        if not redis_host or not redis_password: raise ValueError("Hostname/Senha não encontrados na REDIS_URL")
        print(f"[APP LOG] Conectando com: host={redis_host}, port={redis_port}, ssl={use_ssl}")
        
        # Conexão para RQ e outras operações (decode_responses=False)
        redis_conn = Redis(host=redis_host, port=redis_port, password=redis_password, ssl=use_ssl, ssl_cert_reqs=None, decode_responses=False)
        
        # Conexão para sessões Flask (decode_responses=True)
        session_redis = Redis(host=redis_host, port=redis_port, password=redis_password, ssl=use_ssl, ssl_cert_reqs=None, decode_responses=True)
        
        redis_conn.ping()
        print(f"[APP LOG] Conexão Redis estabelecida e ping bem-sucedido!")
        
        # Configurar Flask-Session para usar a conexão Redis
        app.config['SESSION_REDIS'] = session_redis
        Session(app)
        print("[APP LOG] Flask-Session configurado com Redis.")
        
    except ValueError as ve: print(f"[APP ERROR] Erro ao parsear REDIS_URL: {ve}")
    except Exception as redis_err: import traceback; print(f"[APP ERROR] Falha detalhada ao conectar/pingar Redis:\n{traceback.format_exc()}"); redis_conn = None; session_redis = None
else: print("[APP ERROR] Variável de ambiente REDIS_URL não definida.")

# Fila RQ
if redis_conn:
    try:
        rq_queue = Queue("default", connection=redis_conn)
        print("[APP LOG] Fila RQ inicializada com sucesso.")
    except Exception as rq_err: print(f"[APP ERROR] Falha ao inicializar fila RQ: {rq_err}"); rq_queue = None
else: print("[APP WARNING] RQ não pode ser inicializado.")

# --- REINTRODUZIDO: Fallback local para limite de taxa ---
class LocalStorageRateLimit:
    def __init__(self):
        self.data = defaultdict(lambda: {'count': 0, 'expiry': 0})
        print("[APP WARNING] Usando armazenamento local para limite de taxa (Redis indisponível?).")
    def get(self, key):
        item = self.data.get(key) # Usar .get() para evitar criar chave se não existe
        if item is None or (item['expiry'] != 0 and time.time() > item['expiry']):
             if item is not None: del self.data[key]
             return None
        return item['count'] # Retorna o número diretamente
    def set(self, key, value, ex=None):
        expiry_time = time.time() + ex if ex else 0
        self.data[key] = {'count': int(value), 'expiry': expiry_time}
    def incr(self, key):
        item = self.data.get(key)
        if item is None or (item['expiry'] != 0 and time.time() > item['expiry']):
             self.data[key] = {'count': 1, 'expiry': item['expiry'] if item else 0}
             return 1
        else:
             item['count'] += 1
             return item['count']
    # Adicionar método ping simulado para consistência na verificação
    def ping(self):
         return True

# --- REINTRODUZIDO: Variável rate_limiter ---
rate_limiter = redis_conn if redis_conn else LocalStorageRateLimit()
# Pequena verificação se o rate_limiter é utilizável
try:
     rate_limiter.ping() # Testa se Redis está conectado ou se é o fallback
     print("[APP INFO] Rate limiter está pronto (Redis ou Fallback).")
except Exception as rl_err:
     print(f"[APP WARNING] Rate limiter encontrou um erro no ping inicial: {rl_err}. Tentará operar mesmo assim.")
     # Não define como None, pois o fallback não deve falhar no ping

# Diretório temporário
APP_TEMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'arquivos_temporarios_app')
os.makedirs(APP_TEMP_DIR, exist_ok=True)

# --- Funções Auxiliares (Inalteradas) ---
def get_client_ip():
    # (código igual)
    if request.headers.getlist("X-Forwarded-For"): client_ip = request.headers.getlist("X-Forwarded-For")[0].split(',')[0].strip()
    elif request.headers.get("X-Real-IP"): client_ip = request.headers.get("X-Real-IP").strip()
    else: client_ip = request.remote_addr or '127.0.0.1'
    if not re.match(r"^[0-9a-fA-F.:]+$", client_ip): return '127.0.0.1'
    return client_ip

def get_drive_service():
    # (código igual)
    try: from tasks import get_drive_service_from_credentials; return get_drive_service_from_credentials(GOOGLE_CREDENTIALS_BASE64)
    except ImportError: print("[APP ERROR] Não importou get_drive_service_from_credentials"); return None

def limpar_arquivos_temporarios(directory, max_idade_horas=6):
    # (código igual)
    if not os.path.exists(directory): return
    try:
        print(f"[CLEANUP] Verificando {directory}")
        tempo_atual = time.time(); arquivos_removidos = 0; limite_tempo = tempo_atual - (max_idade_horas * 3600)
        for nome_arquivo in os.listdir(directory):
            caminho_arquivo = os.path.join(directory, nome_arquivo)
            try:
                if os.path.isfile(caminho_arquivo):
                    if os.path.getmtime(caminho_arquivo) < limite_tempo: os.remove(caminho_arquivo); arquivos_removidos += 1; print(f"[CLEANUP] Removido: {nome_arquivo}")
            except Exception as e_inner: print(f"[CLEANUP ERROR] Falha {caminho_arquivo}: {e_inner}")
        if arquivos_removidos > 0: print(f"[CLEANUP] Total removidos: {arquivos_removidos}.")
    except Exception as e: print(f"[CLEANUP ERROR] Erro limpar {directory}: {str(e)}")


# --- Rotas Flask ---
@app.route('/')
def home():
    limpar_arquivos_temporarios(APP_TEMP_DIR)
    return render_template('index.html')

# --- REATIVADA: Rota /status ---
@app.route('/status')
def user_status():
    if not rate_limiter:
         return '<div class="alert alert-warning">Serviço de limite de taxa indisponível.</div>'

    try:
        client_ip = get_client_ip()
        downloads_key = f"downloads:{client_ip}"
        # Usar getattr para segurança, mas rate_limiter deve existir
        downloads_hoje_raw = getattr(rate_limiter, 'get')(downloads_key)
        # 'get' agora retorna int ou None no fallback, ou bytes no Redis (precisa decode)
        if isinstance(downloads_hoje_raw, bytes):
             downloads_hoje = int(downloads_hoje_raw.decode('utf-8'))
        elif isinstance(downloads_hoje_raw, int): # Do fallback
             downloads_hoje = downloads_hoje_raw
        else: # None
             downloads_hoje = 0

        limite_diario = 2
        downloads_restantes = max(0, limite_diario - downloads_hoje)

        if downloads_restantes > 0:
            plural = "s" if downloads_restantes > 1 else ""
            return f'<div class="alert alert-info">Você tem {downloads_restantes} download{plural} restante{plural} hoje.</div>'
        else:
            # Mensagem de limite atingido (será sobreposta pelo JS com msg divertida)
            return f'<div class="alert alert-warning">Você atingiu o limite de {limite_diario} downloads hoje. Tente novamente amanhã!</div>'
    except Exception as e:
        print(f"[APP ERROR] Erro ao verificar status para IP {get_client_ip()}: {e}")
        # Não mostrar o erro detalhado para o usuário
        return '<div class="alert alert-danger">Erro ao verificar status de download.</div>'

@app.route('/upload', methods=['POST'])
def upload():
    filename = None; temp_file_path = None; limite_diario = 2
    client_ip = get_client_ip() # Pega IP no início

    # --- REINTRODUZIDO: Verificação de Limite ---
    if not rate_limiter:
        print(f"[APP WARNING] /upload de {client_ip}: Rate limiter indisponível.")
        # Permitir continuar sem limite se o serviço falhou? Ou retornar erro?
        # Por segurança, vamos retornar erro se o rate limiter não estiver pronto.
        return "<div class='alert alert-danger'>❌ Serviço de verificação de limite indisponível. Tente mais tarde.</div>", 503

    try:
        downloads_key = f"downloads:{client_ip}"
        downloads_hoje_raw = getattr(rate_limiter, 'get')(downloads_key)
        if isinstance(downloads_hoje_raw, bytes): downloads_hoje = int(downloads_hoje_raw.decode('utf-8'))
        elif isinstance(downloads_hoje_raw, int): downloads_hoje = downloads_hoje_raw
        else: downloads_hoje = 0

        print(f"[APP INFO] /upload: IP {client_ip} tem {downloads_hoje} downloads.")

        if downloads_hoje >= limite_diario:
            print(f"[APP INFO] Limite atingido para {client_ip} em /upload.")
            # Retornar HTML de erro 429
            return f"<div class='alert alert-warning'>❌ Você já atingiu o limite de {limite_diario} downloads hoje. Volte amanhã!</div>", 429
    except Exception as e:
         print(f"[APP ERROR] Erro ao verificar limite para {client_ip} em /upload: {e}")
         return "<div class='alert alert-danger'>❌ Erro ao verificar limite de downloads.</div>", 500
    # Fim da verificação de limite

    # --- Resto da lógica (Verificar chaves, API Freepik, Download, Upload Drive) ---
    if not FREEPIK_API_KEY: return "<div class='alert alert-danger'>❌ Chave API Freepik não configurada.</div>", 400
    if not GOOGLE_CREDENTIALS_BASE64: return "<div class='alert alert-danger'>❌ Credenciais Google Drive não configuradas.</div>", 500
    try:
        freepik_link = request.form.get('freepik_link')
        # ... (validação do link, chamada API Freepik, download streaming, upload Drive - código inalterado)...
        # ... (linhas omitidas para brevidade, usar código da versão anterior) ...
        if not freepik_link: return "<div class='alert alert-danger'>❌ Link Freepik não fornecido.</div>", 400
        match = re.search(r'(_|\/)([1-9]\d+)\.(htm|jpg)', freepik_link)
        if not match: return "<div class='alert alert-danger'>❌ Link Freepik inválido.</div>", 400
        image_id = match.group(2)
        print(f"[APP LOG] /upload Freepik ID: {image_id} de IP: {client_ip}")
        headers = {"x-freepik-api-key": FREEPIK_API_KEY, "Accept": "application/json"}
        api_url = f"https://api.freepik.com/v1/resources/{image_id}/download"
        try: api_response = requests.get(api_url, headers=headers, timeout=15); api_response.raise_for_status(); response_data = api_response.json()
        except requests.exceptions.RequestException as api_err: print(f"[APP ERROR] Falha API Freepik ({api_url}): {api_err}"); status_code = api_err.response.status_code if api_err.response else 500; error_detail = api_err.response.text if api_err.response else str(api_err); #... (tratamento status code) ...
        if 'data' not in response_data or 'url' not in response_data['data']: return "<div class='alert alert-danger'>❌ Erro URL download Freepik.</div>", 500
        download_url = response_data['data']['url']; file_format = response_data['data'].get('format', 'unknown')
        print(f"[APP LOG] Iniciando download stream: {download_url}")
        try:
            with requests.get(download_url, stream=True, timeout=600) as r:
                r.raise_for_status(); content_disposition = r.headers.get('content-disposition'); filename = f"freepik_{image_id}.{file_format}"; #... (lógica filename) ...
                filename = re.sub(r'[\\/*?:"<>|]', "_", filename); temp_file_path = os.path.join(APP_TEMP_DIR, filename); print(f"[APP LOG] Salvando em: {temp_file_path}"); bytes_written = 0
                with open(temp_file_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=81920):
                        if chunk: f.write(chunk); bytes_written += len(chunk)
                print(f"[APP LOG] Download stream concluído. {bytes_written} bytes.");
                if bytes_written == 0: raise IOError("Download vazio.")
        except requests.exceptions.Timeout: return f"<div class='alert alert-danger'>❌ Timeout download Freepik.</div>", 504
        except requests.exceptions.RequestException as dl_err: return f"<div class='alert alert-danger'>❌ Erro rede download Freepik: {dl_err}</div>", 502
        except IOError as io_err: return f"<div class='alert alert-danger'>❌ Erro ao salvar arquivo: {io_err}</div>", 500
        print("[APP LOG] Iniciando upload Google Drive..."); drive_service = get_drive_service()
        if not drive_service: return "<div class='alert alert-danger'>❌ Erro conexão Google Drive.</div>", 500
        try:
            from googleapiclient.http import MediaFileUpload; file_metadata = {'name': filename};
            if FOLDER_ID: file_metadata['parents'] = [FOLDER_ID]
            mimetype, _ = mimetypes.guess_type(temp_file_path); mimetype = mimetype or 'application/octet-stream'
            media = MediaFileUpload(temp_file_path, mimetype=mimetype, resumable=True)
            file = drive_service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
            drive_service.permissions().create(fileId=file.get('id'), body={'role': 'reader', 'type': 'anyone'}).execute()
            print(f"[APP LOG] Upload Drive concluído. ID: {file.get('id')}")
        except Exception as drive_err: print(f"[APP ERROR] Erro upload Google Drive: {drive_err}"); return f"<div class='alert alert-danger'>❌ Erro upload Google Drive: {str(drive_err)}</div>", 500

        # --- REINTRODUZIDO: Incremento do Limite APÓS SUCESSO ---
        try:
            # Incrementa e define expiração apenas na primeira vez
            if downloads_hoje == 0:
                # Usa SET com EX para garantir atomicidade e expiração
                getattr(rate_limiter, 'set')(downloads_key, 1, ex=86400) # 86400 segundos = 24 horas
                print(f"[APP INFO] /upload: Primeiro download registrado para {client_ip}.")
            else:
                # Apenas incrementa se já existe (TTL é mantido)
                new_count = getattr(rate_limiter, 'incr')(downloads_key)
                print(f"[APP INFO] /upload: Download incrementado para {client_ip} (Total: {new_count}).")
        except Exception as incr_err:
            # Não falha a requisição se o incremento der erro, mas loga
            print(f"[APP WARNING] Falha ao incrementar limite para {client_ip} em /upload: {incr_err}")
        # Fim do incremento

        # Retornar sucesso
        success_html_body = f"""
            <div class="alert alert-success mb-3">✅ Upload concluído!</div>
            <div class="mb-2"><strong>ID:</strong> {file.get('id')}</div>
            <div class="mb-3"><strong>Link:</strong><br><a href="{file.get('webViewLink')}" target="_blank" class="btn btn-sm btn-outline-primary mt-2"><i class="bi bi-download"></i> Baixar</a></div>
            <div class="text-muted small">{filename} • {datetime.now().strftime('%d/%m %H:%M')}</div>"""
        return f'<div class="card mb-3"><div class="card-body">{success_html_body}</div></div>', 200

    except Exception as e: print(f"[APP ERROR] Erro inesperado /upload: {e}"); import traceback; traceback.print_exc(); return f'<div class="alert alert-danger">⚠️ Erro inesperado: {e}</div>', 500
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try: os.remove(temp_file_path); print(f"[APP LOG] Temp removido: {temp_file_path}")
            except Exception as e_clean: print(f"[APP WARNING] Erro ao remover temp {temp_file_path}: {e_clean}")


# Função para salvar cookies de sessão do Designi no Redis
def save_designi_cookies(cookies_data):
    if not session_redis:
        print("[APP WARNING] Não foi possível salvar cookies: Redis para sessões não disponível.")
        return False
    
    try:
        client_ip = get_client_ip()
        cookie_key = f"designi_cookies:{client_ip}"
        
        # Salvar cookies no Redis com expiração de 7 dias
        session_redis.set(cookie_key, json.dumps(cookies_data), ex=604800)  # 7 dias em segundos
        print(f"[APP LOG] Cookies do Designi salvos para IP {client_ip}")
        return True
    except Exception as e:
        print(f"[APP ERROR] Erro ao salvar cookies do Designi: {e}")
        return False

# Função para recuperar cookies de sessão do Designi do Redis
def get_designi_cookies():
    if not session_redis:
        print("[APP WARNING] Não foi possível recuperar cookies: Redis para sessões não disponível.")
        return None
    
    try:
        client_ip = get_client_ip()
        cookie_key = f"designi_cookies:{client_ip}"
        
        # Recuperar cookies do Redis
        cookies_json = session_redis.get(cookie_key)
        if not cookies_json:
            print(f"[APP LOG] Nenhum cookie encontrado para IP {client_ip}")
            return None
        
        cookies_data = json.loads(cookies_json)
        print(f"[APP LOG] Cookies do Designi recuperados para IP {client_ip}")
        return cookies_data
    except Exception as e:
        print(f"[APP ERROR] Erro ao recuperar cookies do Designi: {e}")
        return None

@app.route('/download-designi', methods=['POST'])
def download_designi():
    limite_diario = 2
    client_ip = get_client_ip()

    # --- REINTRODUZIDO: Verificação de Limite ---
    if not rate_limiter:
        print(f"[APP WARNING] /download-designi de {client_ip}: Rate limiter indisponível.")
        return jsonify({'success': False, 'error': 'Serviço de verificação de limite indisponível.'}), 503
    try:
        downloads_key = f"downloads:{client_ip}"
        downloads_hoje_raw = getattr(rate_limiter, 'get')(downloads_key)
        if isinstance(downloads_hoje_raw, bytes): downloads_hoje = int(downloads_hoje_raw.decode('utf-8'))
        elif isinstance(downloads_hoje_raw, int): downloads_hoje = downloads_hoje_raw
        else: downloads_hoje = 0

        print(f"[APP INFO] /download-designi: IP {client_ip} tem {downloads_hoje} downloads.")

        if downloads_hoje >= limite_diario:
            print(f"[APP INFO] Limite atingido para {client_ip} em /download-designi.")
            return jsonify({'success': False, 'error': f'Você já atingiu o limite de {limite_diario} downloads hoje. Volte amanhã!'}), 429 # Retorna JSON
    except Exception as e:
         print(f"[APP ERROR] Erro ao verificar limite para {client_ip} em /download-designi: {e}")
         return jsonify({'success': False, 'error': 'Erro ao verificar limite de downloads.'}), 500
    # Fim da verificação de limite

    # --- Resto da lógica (Verificar deps, enfileirar job) ---
    if not rq_queue: print("[APP ERROR]/download-designi: RQ não disponível."); return jsonify({'success': False, 'error': 'Serviço background indisponível.'}), 503
    if not EMAIL or not SENHA: return jsonify({'success': False, 'error': 'Credenciais Designi não configuradas.'}), 500
    if not GOOGLE_CREDENTIALS_BASE64: return jsonify({'success': False, 'error': 'Credenciais Google Drive não configuradas.'}), 500
    try:
        data = request.json; url = data.get('url')
        if not url or not url.startswith('http'): return jsonify({'success': False, 'error': 'URL Designi inválida.'}), 400
        print(f"[APP LOG] /download-designi: Recebido request para {url} de {client_ip}")
        
        # Verificar se temos cookies salvos para este IP
        cookies = get_designi_cookies()
        
        try:
            job = rq_queue.enqueue(
                'tasks.perform_designi_download_task',
                args=(url, client_ip, FOLDER_ID, EMAIL, SENHA, CAPTCHA_API_KEY, GOOGLE_CREDENTIALS_BASE64, URL_LOGIN, cookies),
                job_timeout=1800, result_ttl=3600, failure_ttl=86400
            )
            print(f"[APP LOG] Tarefa Designi enfileirada: {job.id}")

            # --- REINTRODUZIDO: Incremento do Limite APÓS ENFILEIRAR ---
            try:
                if downloads_hoje == 0:
                    getattr(rate_limiter, 'set')(downloads_key, 1, ex=86400)
                    print(f"[APP INFO] /download-designi: Primeiro download registrado para {client_ip}.")
                else:
                    new_count = getattr(rate_limiter, 'incr')(downloads_key)
                    print(f"[APP INFO] /download-designi: Download incrementado para {client_ip} (Total: {new_count}).")
            except Exception as incr_err:
                print(f"[APP WARNING] Falha ao incrementar limite para {client_ip} em /download-designi: {incr_err}")
            # Fim do incremento

            return jsonify({'success': True, 'message': 'Processo de download iniciado. Aguarde um momento...', 'job_id': job.id }), 202
        except Exception as enqueue_err:
            print(f"[APP ERROR] Falha ao enfileirar RQ: {enqueue_err}")
            if not rq_queue: return jsonify({'success': False, 'error': 'Falha: Serviço background não conectado.'}), 503
            else: return jsonify({'success': False, 'error': 'Falha ao iniciar processo background.'}), 500
    except Exception as e: print(f"[APP ERROR] Erro inesperado /download-designi: {e}"); import traceback; traceback.print_exc(); return jsonify({'success': False, 'error': f'Erro inesperado: {e}'}), 500

# --- Rota /check_job Inalterada ---
@app.route('/check_job/<job_id>', methods=['GET'])
def check_job_status(job_id):
    # (Código desta rota permanece o mesmo da versão anterior)
    if not redis_conn: print(f"[APP ERROR] /check_job/{job_id}: Redis não conectado."); return jsonify({'status': 'error', 'error': 'Serviço indisponível'}), 503
    try:
        job = Job.fetch(job_id, connection=redis_conn); status = job.get_status(refresh=True); result_data = None; error_info = None
        print(f"[APP DEBUG] /check_job/{job_id}: Status bruto = {status}")
        if status == 'finished':
            raw_result = job.result; print(f"[APP DEBUG] /check_job/{job_id}: Resultado (finished) = {raw_result} (Tipo: {type(raw_result)})")
            if isinstance(raw_result, dict): result_data = raw_result
            elif raw_result is None: result_data = {'success': False, 'error': 'Tarefa finalizada sem resultado.'}
            else: print(f"[APP WARNING] /check_job/{job_id}: Resultado final não é dict."); result_data = {'success': False, 'error': 'Resultado inesperado.'}
        elif status == 'failed':
            raw_exc_info = job.exc_info; print(f"[APP DEBUG] /check_job/{job_id}: Info exceção (failed) = {raw_exc_info} (Tipo: {type(raw_exc_info)})")
            if isinstance(raw_exc_info, bytes):
                try: full_traceback = raw_exc_info.decode('utf-8', errors='replace'); error_info = full_traceback.strip().split('\n')[-1]
                except Exception as dec_err: print(f"[APP WARNING] /check_job/{job_id}: Falha decodificar exc_info: {dec_err}"); error_info = "Falha tarefa (erro decodificação)."
            elif isinstance(raw_exc_info, str): full_traceback = raw_exc_info; error_info = full_traceback.strip().split('\n')[-1]
            else: error_info = "Falha desconhecida (sem traceback)."
            print(f"[APP INFO] /check_job/{job_id}: Tarefa falhou: {error_info}"); result_data = {'success': False, 'error': error_info}
        response_data = {'status': status}
        if result_data: response_data['result'] = result_data
        return jsonify(response_data)
    except NoSuchJobError: print(f"[APP INFO] /check_job/{job_id}: Job não encontrado."); return jsonify({'status': 'not_found', 'error': 'Tarefa não encontrada.'}), 404
    except Exception as e: print(f"[APP ERROR] Erro verificar job {job_id}: {e}"); import traceback; traceback.print_exc(); return jsonify({'status': 'error', 'error': 'Erro interno.'}), 500

# Execução Principal
if __name__ == '__main__':
    print("[APP STARTUP] Limpando diretório temporário...")
    limpar_arquivos_temporarios(APP_TEMP_DIR)
    pass