import os
import json
import re
import base64
import time
import mimetypes
from datetime import datetime
from flask import Flask, render_template, request, jsonify # Removido abort (não usado)
import requests
from redis import Redis
from rq import Queue
from rq.job import Job
from rq.exceptions import NoSuchJobError
from dotenv import load_dotenv
from urllib.parse import urlparse

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

# Conexão Redis
REDIS_URL = os.environ.get('REDIS_URL')
redis_conn = None
rq_queue = None

# --- Bloco de Conexão Redis com decode_responses=False ---
if REDIS_URL:
    try:
        print(f"[APP LOG] Tentando conectar ao Redis usando URL: {REDIS_URL}")
        parsed_url = urlparse(REDIS_URL)
        redis_host = parsed_url.hostname
        redis_port = parsed_url.port or 6379
        redis_password = parsed_url.password
        use_ssl = parsed_url.scheme == 'rediss' or (redis_host and 'upstash.io' in redis_host) # Verifica host existe
        if not redis_host or not redis_password: raise ValueError("Hostname/Senha não encontrados na REDIS_URL")
        print(f"[APP LOG] Conectando com: host={redis_host}, port={redis_port}, ssl={use_ssl}")

        redis_conn = Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            ssl=use_ssl,
            ssl_cert_reqs=None,
            decode_responses=False # <<-- MUDANÇA PRINCIPAL AQUI
        )
        redis_conn.ping()
        print(f"[APP LOG] Conexão Redis estabelecida e ping bem-sucedido!")
    except ValueError as ve: print(f"[APP ERROR] Erro ao parsear REDIS_URL: {ve}")
    except Exception as redis_err: import traceback; print(f"[APP ERROR] Falha detalhada ao conectar/pingar Redis:\n{traceback.format_exc()}"); redis_conn = None
else: print("[APP ERROR] Variável de ambiente REDIS_URL não definida.")

# Fila RQ
if redis_conn:
    try:
        rq_queue = Queue("default", connection=redis_conn)
        print("[APP LOG] Fila RQ inicializada com sucesso.")
    except Exception as rq_err: print(f"[APP ERROR] Falha ao inicializar fila RQ: {rq_err}"); rq_queue = None
else: print("[APP WARNING] RQ não pode ser inicializado.")

# Diretório temporário
APP_TEMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'arquivos_temporarios_app')
os.makedirs(APP_TEMP_DIR, exist_ok=True)

# --- Funções Auxiliares ---
def get_client_ip():
    if request.headers.getlist("X-Forwarded-For"): client_ip = request.headers.getlist("X-Forwarded-For")[0].split(',')[0].strip()
    elif request.headers.get("X-Real-IP"): client_ip = request.headers.get("X-Real-IP").strip()
    else: client_ip = request.remote_addr or '127.0.0.1'
    if not re.match(r"^[0-9a-fA-F.:]+$", client_ip): return '127.0.0.1'
    return client_ip

def get_drive_service():
    try:
        from tasks import get_drive_service_from_credentials; return get_drive_service_from_credentials(GOOGLE_CREDENTIALS_BASE64)
    except ImportError: print("[APP ERROR] Não foi possível importar get_drive_service_from_credentials"); return None

def limpar_arquivos_temporarios(directory, max_idade_horas=6):
    if not os.path.exists(directory): return
    try:
        print(f"[CLEANUP] Verificando diretório temporário: {directory}")
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

@app.route('/status')
def user_status():
    return '' # Desativado

@app.route('/upload', methods=['POST'])
def upload():
    # (Rota /upload inalterada - versão sem limite de taxa)
    filename = None; temp_file_path = None
    if not FREEPIK_API_KEY: return "<div class='alert alert-danger'>❌ Chave API Freepik não configurada.</div>", 400
    if not GOOGLE_CREDENTIALS_BASE64: return "<div class='alert alert-danger'>❌ Credenciais Google Drive não configuradas.</div>", 500
    try:
        freepik_link = request.form.get('freepik_link'); client_ip = get_client_ip()
        if not freepik_link: return "<div class='alert alert-danger'>❌ Link Freepik não fornecido.</div>", 400
        match = re.search(r'(_|\/)([1-9]\d+)\.(htm|jpg)', freepik_link)
        if not match: return "<div class='alert alert-danger'>❌ Link Freepik inválido.</div>", 400
        image_id = match.group(2)
        print(f"[APP LOG] /upload Freepik ID: {image_id} de IP: {client_ip}")
        headers = {"x-freepik-api-key": FREEPIK_API_KEY, "Accept": "application/json"}
        api_url = f"https://api.freepik.com/v1/resources/{image_id}/download"
        try:
            api_response = requests.get(api_url, headers=headers, timeout=15); api_response.raise_for_status(); response_data = api_response.json()
        except requests.exceptions.RequestException as api_err:
             print(f"[APP ERROR] Falha API Freepik ({api_url}): {api_err}")
             status_code = api_err.response.status_code if api_err.response else 500; error_detail = api_err.response.text if api_err.response else str(api_err)
             if status_code == 404: return f"<div class='alert alert-danger'>❌ Recurso Freepik (ID: {image_id}) não encontrado.</div>", 404
             elif status_code == 401 or status_code == 403: return f"<div class='alert alert-danger'>❌ Erro autenticação API Freepik.</div>", status_code
             else: return f"<div class='alert alert-danger'>❌ Erro API Freepik: {error_detail}</div>", status_code
        if 'data' not in response_data or 'url' not in response_data['data']: return "<div class='alert alert-danger'>❌ Erro URL download Freepik.</div>", 500
        download_url = response_data['data']['url']; file_format = response_data['data'].get('format', 'unknown')
        print(f"[APP LOG] Iniciando download stream: {download_url}")
        try:
            with requests.get(download_url, stream=True, timeout=600) as r:
                r.raise_for_status()
                content_disposition = r.headers.get('content-disposition'); filename = f"freepik_{image_id}.{file_format}"
                if content_disposition: fname_match = re.search('filename="?([^"]+)"?', content_disposition);
                if fname_match: filename = fname_match.group(1)
                if not filename or filename.endswith(".unknown"): content_type = r.headers.get('content-type', '').split(';')[0]; ext = mimetypes.guess_extension(content_type) or ".file"; filename = f"freepik_{image_id}{ext}"
                filename = re.sub(r'[\\/*?:"<>|]', "_", filename); temp_file_path = os.path.join(APP_TEMP_DIR, filename)
                print(f"[APP LOG] Salvando em: {temp_file_path}")
                bytes_written = 0
                with open(temp_file_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=81920):
                        if chunk: f.write(chunk); bytes_written += len(chunk)
                print(f"[APP LOG] Download stream concluído. {bytes_written} bytes.")
                if bytes_written == 0: raise IOError("Download vazio.")
        except requests.exceptions.Timeout: return f"<div class='alert alert-danger'>❌ Timeout download Freepik.</div>", 504
        except requests.exceptions.RequestException as dl_err: return f"<div class='alert alert-danger'>❌ Erro rede download Freepik: {dl_err}</div>", 502
        except IOError as io_err: return f"<div class='alert alert-danger'>❌ Erro ao salvar arquivo: {io_err}</div>", 500
        print("[APP LOG] Iniciando upload Google Drive...")
        drive_service = get_drive_service()
        if not drive_service: return "<div class='alert alert-danger'>❌ Erro conexão Google Drive.</div>", 500
        try:
            from googleapiclient.http import MediaFileUpload
            file_metadata = {'name': filename};
            if FOLDER_ID: file_metadata['parents'] = [FOLDER_ID]
            mimetype, _ = mimetypes.guess_type(temp_file_path); mimetype = mimetype or 'application/octet-stream'
            media = MediaFileUpload(temp_file_path, mimetype=mimetype, resumable=True)
            file = drive_service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
            drive_service.permissions().create(fileId=file.get('id'), body={'role': 'reader', 'type': 'anyone'}).execute()
            print(f"[APP LOG] Upload Drive concluído. ID: {file.get('id')}")
        except Exception as drive_err: print(f"[APP ERROR] Erro upload Google Drive: {drive_err}"); return f"<div class='alert alert-danger'>❌ Erro upload Google Drive: {str(drive_err)}</div>", 500
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

@app.route('/download-designi', methods=['POST'])
def download_designi():
    # (Rota /download-designi inalterada - versão sem limite de taxa)
    client_ip = get_client_ip()
    if not rq_queue: print("[APP ERROR]/download-designi: RQ não disponível."); return jsonify({'success': False, 'error': 'Serviço background indisponível.'}), 503
    if not EMAIL or not SENHA: return jsonify({'success': False, 'error': 'Credenciais Designi não configuradas.'}), 500
    if not GOOGLE_CREDENTIALS_BASE64: return jsonify({'success': False, 'error': 'Credenciais Google Drive não configuradas.'}), 500
    try:
        data = request.json; url = data.get('url')
        if not url or not url.startswith('http'): return jsonify({'success': False, 'error': 'URL Designi inválida.'}), 400
        print(f"[APP LOG] /download-designi: Recebido request para {url} de {client_ip}")
        try:
            job = rq_queue.enqueue(
                'tasks.perform_designi_download_task',
                args=(url, client_ip, FOLDER_ID, EMAIL, SENHA, CAPTCHA_API_KEY, GOOGLE_CREDENTIALS_BASE64, URL_LOGIN),
                job_timeout=1800, result_ttl=3600, failure_ttl=86400
            )
            print(f"[APP LOG] Tarefa Designi enfileirada: {job.id}")
            return jsonify({'success': True, 'message': 'Processo de download iniciado. Aguarde um momento...', 'job_id': job.id }), 202 # Mensagem inicial
        except Exception as enqueue_err:
            print(f"[APP ERROR] Falha ao enfileirar RQ: {enqueue_err}")
            if not rq_queue: return jsonify({'success': False, 'error': 'Falha: Serviço background não conectado.'}), 503
            else: return jsonify({'success': False, 'error': 'Falha ao iniciar processo background.'}), 500
    except Exception as e: print(f"[APP ERROR] Erro inesperado /download-designi: {e}"); import traceback; traceback.print_exc(); return jsonify({'success': False, 'error': f'Erro inesperado: {e}'}), 500

# --- Rota /check_job ATUALIZADA para lidar com bytes ---
@app.route('/check_job/<job_id>', methods=['GET'])
def check_job_status(job_id):
    if not redis_conn:
        print(f"[APP ERROR] /check_job/{job_id}: Redis não conectado.")
        return jsonify({'status': 'error', 'error': 'Serviço de verificação indisponível'}), 503
    try:
        # Fetch DEVE funcionar com decode_responses=False na conexão
        job = Job.fetch(job_id, connection=redis_conn)
        # Usar refresh=False aqui pode ser mais seguro se a conexão for instável,
        # mas refresh=True garante dados mais atualizados. Vamos manter True por enquanto.
        status = job.get_status(refresh=True)
        result_data = None
        error_info = None

        print(f"[APP DEBUG] /check_job/{job_id}: Status bruto = {status}")

        if status == 'finished':
            # job.result já deve ser o objeto Python desserializado pelo RQ (pickle)
            raw_result = job.result
            print(f"[APP DEBUG] /check_job/{job_id}: Resultado bruto (finished) = {raw_result} (Tipo: {type(raw_result)})")
            if isinstance(raw_result, dict):
                result_data = raw_result # Usar diretamente se já for dict
            elif raw_result is None:
                 result_data = {'success': False, 'error': 'Tarefa finalizada sem resultado.'}
            else:
                # Se não for dict (improvável com RQ padrão), tenta tratar como erro
                print(f"[APP WARNING] /check_job/{job_id}: Resultado final não é dict. Conteúdo: {raw_result}")
                result_data = {'success': False, 'error': 'Resultado inesperado da tarefa.'}

        elif status == 'failed':
            # job.exc_info geralmente é string, mas vamos garantir a decodificação segura
            raw_exc_info = job.exc_info
            print(f"[APP DEBUG] /check_job/{job_id}: Info de exceção bruta (failed) = {raw_exc_info} (Tipo: {type(raw_exc_info)})")
            if isinstance(raw_exc_info, bytes):
                try:
                    full_traceback = raw_exc_info.decode('utf-8', errors='replace')
                    error_info = full_traceback.strip().split('\n')[-1] # Última linha
                except Exception as dec_err:
                    print(f"[APP WARNING] /check_job/{job_id}: Falha decodificar exc_info bytes: {dec_err}")
                    error_info = "Falha na tarefa (erro decodificação traceback)."
            elif isinstance(raw_exc_info, str):
                 full_traceback = raw_exc_info
                 error_info = full_traceback.strip().split('\n')[-1] # Última linha
            else:
                 error_info = "Falha desconhecida na tarefa (sem traceback detalhado)."

            print(f"[APP INFO] /check_job/{job_id}: Tarefa falhou: {error_info}")
            result_data = {'success': False, 'error': error_info} # Define resultado como falha

        # Monta a resposta final
        response_data = {'status': status}
        if result_data:
            response_data['result'] = result_data # Inclui o dicionário de resultado (sucesso ou falha)

        return jsonify(response_data)

    except NoSuchJobError:
        print(f"[APP INFO] /check_job/{job_id}: Job não encontrado.")
        return jsonify({'status': 'not_found', 'error': 'Tarefa não encontrada ou expirada.'}), 404
    except Exception as e:
        print(f"[APP ERROR] Erro ao verificar job {job_id}: {e}")
        import traceback; traceback.print_exc()
        return jsonify({'status': 'error', 'error': 'Erro interno ao verificar status.'}), 500

# Execução Principal
if __name__ == '__main__':
    print("[APP STARTUP] Limpando diretório temporário do app web...")
    limpar_arquivos_temporarios(APP_TEMP_DIR)
    pass