import os
import time
import json
import base64
import requests
from playwright.sync_api import sync_playwright
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import mimetypes # Importado para mimetype no upload

# --- Funções Auxiliares (Inalteradas) ---

def get_drive_service_from_credentials(credentials_base64_str):
    # (Código desta função permanece o mesmo)
    SCOPES = ['https://www.googleapis.com/auth/drive']
    try:
        if not credentials_base64_str: print("[TASK ERROR] Variável GOOGLE_CREDENTIALS_BASE64 não configurada."); return None
        try:
            json_str = base64.b64decode(credentials_base64_str).decode('utf-8')
            service_account_info = json.loads(json_str)
        except Exception as e: print(f"[TASK ERROR] Erro ao decodificar/parsear credenciais Google: {e}"); return None
        required_fields = ['client_email', 'private_key', 'project_id']
        for field in required_fields:
            if field not in service_account_info: print(f"[TASK ERROR] Campo '{field}' não encontrado nas credenciais."); return None
        credentials = service_account.Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
        return build('drive', 'v3', credentials=credentials)
    except Exception as e: print(f"[TASK ERROR] Erro ao criar serviço Google Drive: {e}"); return None


def solve_captcha(page, captcha_api_key, url_login):
    # (Código desta função permanece o mesmo)
    captcha_element = page.locator("iframe[src*='recaptcha']")
    if captcha_element.count() > 0 and captcha_api_key:
        print("[TASK LOG] CAPTCHA detectado! Tentando resolver...")
        site_key = page.evaluate('''() => { const d = document.querySelector('.g-recaptcha'); return d ? d.getAttribute('data-sitekey') : null; }''')
        if not site_key: raise Exception('Não foi possível encontrar site key do CAPTCHA.')
        response = requests.post("http://2captcha.com/in.php", data={ "key": captcha_api_key, "method": "userrecaptcha", "googlekey": site_key, "pageurl": url_login, "json": 1 }, timeout=20)
        response.raise_for_status()
        request_result = response.json()
        if request_result.get("status") != 1: raise Exception(f'Falha ao enviar CAPTCHA: {request_result.get("request")}')
        captcha_id = request_result["request"]
        print(f"[TASK LOG] CAPTCHA enviado, ID: {captcha_id}. Aguardando solução...")
        token = None
        for _ in range(60):
            time.sleep(3)
            try:
                result_response = requests.get(f"http://2captcha.com/res.php?key={captcha_api_key}&action=get&id={captcha_id}&json=1", timeout=10)
                result_response.raise_for_status()
                result = result_response.json()
                if result.get("status") == 1: token = result["request"]; print("[TASK LOG] CAPTCHA resolvido!"); break
                elif result.get("request") == "CAPCHA_NOT_READY": continue
                else: raise Exception(f"Erro obter resultado CAPTCHA: {result.get('request')}")
            except requests.exceptions.RequestException as captcha_req_err: print(f"[TASK WARNING] Erro rede verificar CAPTCHA: {captcha_req_err}. Tentando novamente..."); time.sleep(5)
            except Exception as captcha_err: raise Exception(f"Erro inesperado verificar CAPTCHA: {captcha_err}")
        if not token: raise Exception('Timeout ou erro ao resolver CAPTCHA excedido.')
        page.evaluate(f"const ta = document.getElementById('g-recaptcha-response'); if (ta) ta.value = '{token}';")
        print("[TASK LOG] Token CAPTCHA inserido.")
        time.sleep(1)
        return True
    return False

# --- A Tarefa Principal do RQ (COM MODIFICAÇÃO) ---

def perform_designi_download_task(
    designi_url,
    client_ip,
    folder_id,
    email,
    senha,
    captcha_api_key,
    drive_credentials_base64,
    url_login='https://designi.com.br/login'
    ):
    print(f"[TASK LOG] Iniciando tarefa para URL: {designi_url} (IP: {client_ip})")
    temp_file_path = None
    browser = None
    context = None
    start_time = time.time()
    temp_dir = '/tmp/designi_downloads' # Usar /tmp no container
    os.makedirs(temp_dir, exist_ok=True)
    print(f"[TASK LOG] Usando diretório temp: {temp_dir}")

    # --- MODIFICAÇÃO: Definir caminho do executável ---
    # Baseado na imagem mcr.microsoft.com/playwright/python:v1.42.0-jammy
    # e na variável PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
    # O browser Chromium v1105 deve estar neste caminho.
    # Se você usar outra versão da imagem base, este número (1105) pode mudar!
    chrome_executable_path = "/ms-playwright/chromium-1105/chrome-linux/chrome"
    launch_options = {
        'headless': True,
        'args': ['--no-sandbox', '--disable-setuid-sandbox']
    }

    # Verifica se o caminho existe ANTES de tentar usá-lo
    if os.path.exists(chrome_executable_path):
        print(f"[TASK INFO] Usando caminho explícito para o executável: {chrome_executable_path}")
        launch_options['executable_path'] = chrome_executable_path
    else:
        # Se não existir, lança um erro claro ANTES de tentar iniciar o Playwright
        print(f"[TASK ERROR] CRÍTICO: Executável do navegador NÃO encontrado em {chrome_executable_path}. Verifique o Dockerfile e a instalação do Playwright.")
        # Retorna um dicionário de falha imediatamente
        return {
            'success': False,
            'error': f"Executável do navegador não encontrado em {chrome_executable_path}",
            'duration_seconds': time.time() - start_time
        }
        # Ou pode lançar uma exceção:
        # raise FileNotFoundError(f"Executável do navegador não encontrado em {chrome_executable_path}")

    try:
        with sync_playwright() as p:
            try:
                print("[TASK LOG] Iniciando Chromium headless...")
                # Passa as opções, incluindo executable_path se definido
                browser = p.chromium.launch(**launch_options)
                context = browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
                page = context.new_page()
                page.set_default_timeout(90000) # 90 segundos

                # --- RESTANTE DA LÓGICA (Login, Navegação, Download, Upload) ---
                # (O código daqui para baixo permanece o mesmo das versões anteriores)
                # Exemplo:
                print(f"[TASK LOG] Acessando página de login: {url_login}")
                page.goto(url_login, wait_until='networkidle')
                if not email or not senha: raise ValueError('Credenciais Designi não fornecidas.')
                print("[TASK LOG] Preenchendo credenciais...")
                page.fill("input[name=email]", email, timeout=30000)
                page.fill("input[name=password]", senha, timeout=30000)
                solve_captcha(page, captcha_api_key, url_login)
                print("[TASK LOG] Tentando clicar login...")
                login_button = page.locator('button:has-text("Fazer login"), input[type="submit"]:has-text("Login")').first
                login_button.wait_for(state="visible", timeout=30000)
                login_button.click()
                print("[TASK LOG] Aguardando navegação pós-login...")
                try:
                    page.wait_for_url(lambda url: "/login" not in url, timeout=60000)
                except Exception as nav_err:
                    if "/login" in page.url:
                        page.screenshot(path=os.path.join(temp_dir, 'login_fail_screenshot.png'))
                        raise Exception(f"Falha login (ainda em /login): {nav_err}")
                    else: print(f"[TASK WARNING] Espera pós-login falhou, mas URL mudou: {page.url}")
                print(f"[TASK LOG] Login OK! URL: {page.url}")
                print(f"[TASK LOG] Navegando para URL do arquivo: {designi_url}")
                page.goto(designi_url, wait_until='networkidle')
                print(f"[TASK LOG] Página arquivo carregada. URL: {page.url}")
                print("[TASK LOG] Aguardando botão download...")
                download_button_selector = "#downButton, a:has-text('Download'), button:has-text('Download')"
                download_button = page.locator(download_button_selector).first
                try:
                    download_button.wait_for(state="visible", timeout=60000)
                    print("[TASK LOG] Botão download visível.")
                except Exception as btn_err:
                    page.screenshot(path=os.path.join(temp_dir, 'download_button_fail.png'))
                    raise Exception(f"Botão download ({download_button_selector}) não encontrado: {btn_err}")
                print("[TASK LOG] Configurando espera download...")
                with page.expect_download(timeout=120000) as download_info:
                    print("[TASK LOG] Clicando botão download...")
                    download_button.click()
                    print("[TASK LOG] Clique realizado, aguardando início download...")
                download = download_info.value
                if not download: raise Exception("Evento download não ocorreu.")
                suggested_filename = download.suggested_filename or f"designi_download_{int(time.time())}.file"
                temp_file_path = os.path.join(temp_dir, suggested_filename)
                print(f"[TASK LOG] Download iniciado: {suggested_filename}. Salvando em: {temp_file_path}")
                download.save_as(temp_file_path)
                print(f"[TASK LOG] Download concluído: {temp_file_path}")
                if not os.path.exists(temp_file_path) or os.path.getsize(temp_file_path) == 0:
                     raise Exception(f"Falha salvar download ou arquivo vazio: {temp_file_path}")
                file_size = os.path.getsize(temp_file_path)
                print(f"[TASK LOG] Arquivo salvo. Tamanho: {file_size} bytes")

            except Exception as pw_error:
                print(f"[TASK ERROR] Erro durante automação Playwright: {pw_error}")
                if 'page' in locals() and page and not page.is_closed():
                    try: page.screenshot(path=os.path.join(temp_dir, 'playwright_error_screenshot.png'))
                    except Exception as ss_err: print(f"[TASK WARNING] Não foi possível tirar screenshot erro: {ss_err}")
                raise # Re-lança a exceção

            finally:
                if browser: print("[TASK LOG] Fechando navegador Playwright..."); browser.close(); print("[TASK LOG] Navegador fechado.")

        # --- Upload Google Drive ---
        if temp_file_path and os.path.exists(temp_file_path):
            print("[TASK LOG] Iniciando upload Google Drive...")
            drive_service = get_drive_service_from_credentials(drive_credentials_base64)
            if not drive_service: raise Exception("Não foi possível obter serviço Google Drive.")
            filename = os.path.basename(temp_file_path)
            file_metadata = {'name': filename}
            if folder_id: file_metadata['parents'] = [folder_id]
            mimetype, _ = mimetypes.guess_type(temp_file_path)
            mimetype = mimetype or 'application/octet-stream'
            media = MediaFileUpload(temp_file_path, mimetype=mimetype, resumable=True)
            print(f"[TASK LOG] Enviando '{filename}' ({mimetype}) para Google Drive...")
            file = drive_service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
            print(f"[TASK LOG] Upload Drive concluído. ID: {file.get('id')}")
            print("[TASK LOG] Definindo permissão pública...")
            drive_service.permissions().create(fileId=file.get('id'), body={'role': 'reader', 'type': 'anyone'}).execute()
            print("[TASK LOG] Permissão pública definida.")
            end_time = time.time(); duration = end_time - start_time
            print(f"[TASK SUCCESS] Tarefa concluída com sucesso em {duration:.2f} segundos.")
            return {'success': True, 'file_id': file.get('id'), 'download_link': file.get('webViewLink'), 'filename': filename, 'duration_seconds': duration}
        else:
             raise Exception("Arquivo temp não encontrado ou inválido pós-download.")

    except Exception as e:
        end_time = time.time(); duration = end_time - start_time
        error_message = f"Erro na tarefa download: {str(e)}"
        # Simplificar log de erro para evitar recursão se o erro for na formatação
        print(f"[TASK FAILED] {error_message} (Duração: {duration:.2f}s)")
        # Retorna dicionário de erro
        return {'success': False, 'error': error_message, 'duration_seconds': duration}

    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try: os.remove(temp_file_path); print(f"[TASK LOG] Temp removido: {temp_file_path}")
            except Exception as e_clean: print(f"[TASK WARNING] Erro remover temp {temp_file_path}: {e_clean}")
        # Limpeza de screenshots (opcional)
        # for f in os.listdir(temp_dir):
        #      if f.endswith('.png'): try: os.remove(os.path.join(temp_dir, f)) except: pass