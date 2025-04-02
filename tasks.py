# -*- coding: utf-8 -*-
import os
import time
import json
import base64
import requests
from playwright.sync_api import sync_playwright
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import mimetypes
import traceback # Importar traceback para logs

# --- Função Auxiliar para Upload de Debug ---
def upload_debug_screenshot_to_drive(drive_service, local_file_path, folder_id, base_filename):
    """Faz upload de um arquivo de screenshot para uma subpasta 'Debug' no Drive."""
    if not drive_service or not os.path.exists(local_file_path):
        print(f"[DEBUG UPLOAD] Serviço Drive indisponível ou arquivo local não encontrado: {local_file_path}")
        return

    try:
        # 1. Encontrar/Criar a pasta de Debug
        debug_folder_name = "Debug Screenshots"
        query = f"name='{debug_folder_name}' and mimeType='application/vnd.google-apps.folder' and '{folder_id}' in parents and trashed=false"
        response = drive_service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        debug_folder_id = None
        if response.get('files'):
            debug_folder_id = response.get('files')[0].get('id')
            print(f"[DEBUG UPLOAD] Pasta debug encontrada: {debug_folder_id}")
        else:
            print(f"[DEBUG UPLOAD] Criando pasta debug '{debug_folder_name}'...")
            folder_metadata = {
                'name': debug_folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [folder_id]
            }
            folder = drive_service.files().create(body=folder_metadata, fields='id').execute()
            debug_folder_id = folder.get('id')
            print(f"[DEBUG UPLOAD] Pasta debug criada: {debug_folder_id}")

        if not debug_folder_id:
             raise Exception("Não foi possível encontrar ou criar a pasta de debug.")

        # 2. Fazer Upload do Screenshot
        filename_drive = f"{base_filename}_{os.path.basename(local_file_path)}"
        file_metadata = {
            'name': filename_drive,
            'parents': [debug_folder_id] # Upload para a pasta de debug
        }
        media = MediaFileUpload(local_file_path, mimetype='image/png', resumable=True)
        print(f"[DEBUG UPLOAD] Enviando screenshot '{filename_drive}' para Google Drive...")
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id' # Só precisamos do ID para confirmar
        ).execute()
        print(f"[DEBUG UPLOAD] Screenshot enviado com sucesso. ID: {file.get('id')}")

        # 3. Limpar arquivo local após upload (opcional)
        # try:
        #     os.remove(local_file_path)
        #     print(f"[DEBUG UPLOAD] Screenshot local removido: {local_file_path}")
        # except Exception as e_clean:
        #     print(f"[DEBUG UPLOAD WARNING] Erro ao remover screenshot local {local_file_path}: {e_clean}")

    except Exception as e:
        print(f"[DEBUG UPLOAD ERROR] Falha ao fazer upload do screenshot {local_file_path}: {e}")
        print(traceback.format_exc()) # Logar o erro detalhado do upload

# --- Funções Auxiliares (get_drive_service_from_credentials, solve_captcha - Inalteradas) ---
# (Coloque o código delas aqui)
def get_drive_service_from_credentials(credentials_base64_str):
    SCOPES = ['https://www.googleapis.com/auth/drive']
    # ... (código completo da função aqui) ...
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
    captcha_element = page.locator("iframe[src*='recaptcha']")
    # ... (código completo da função aqui) ...
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

# --- A Tarefa Principal do RQ ---
def perform_designi_download_task(
    designi_url,
    client_ip,
    folder_id, # ID da pasta principal no Drive
    email,
    senha,
    captcha_api_key,
    drive_credentials_base64,
    url_login='https://designi.com.br/login',
    saved_cookies=None
    ):
    print(f"[TASK LOG] Iniciando tarefa para URL: {designi_url} (IP: {client_ip})")
    temp_file_path = None
    browser = None
    context = None
    drive_service = None # Inicializa drive_service como None
    start_time = time.time()
    temp_dir = '/tmp/designi_downloads'
    os.makedirs(temp_dir, exist_ok=True)
    print(f"[TASK LOG] Usando diretório temp: {temp_dir}")

    # Tenta obter o serviço do Drive logo no início
    try:
        print("[TASK LOG] Tentando obter serviço Google Drive...")
        drive_service = get_drive_service_from_credentials(drive_credentials_base64)
        if not drive_service:
            print("[TASK WARNING] Não foi possível obter serviço Google Drive no início. Upload de debug pode falhar.")
        else:
            print("[TASK LOG] Serviço Google Drive obtido com sucesso.")
    except Exception as drive_init_err:
         print(f"[TASK WARNING] Erro ao obter serviço Google Drive no início: {drive_init_err}")

    # Restante do setup do Playwright...
    chrome_executable_path = "/ms-playwright/chromium-1161/chrome-linux/chrome"
    headless_shell_path = "/ms-playwright/chromium_headless_shell-1161/chrome-linux/headless_shell"
    # ... (resto do código de launch_options) ...
    launch_options = {'headless': True, 'args': ['--no-sandbox', '--disable-setuid-sandbox']}
    if os.path.exists(chrome_executable_path):
        print(f"[TASK INFO] Usando caminho explícito: {chrome_executable_path}")
        launch_options['executable_path'] = chrome_executable_path
    elif os.path.exists(headless_shell_path):
        print(f"[TASK WARNING] Usando fallback: {headless_shell_path}")
        launch_options['executable_path'] = headless_shell_path
    else:
        print(f"[TASK ERROR] CRÍTICO: Executável navegador NÃO encontrado.")
        # Tenta fazer upload de um log de erro se o drive service estiver disponível
        if drive_service:
            error_log_path = os.path.join(temp_dir, f'critical_browser_error_{int(time.time())}.log')
            with open(error_log_path, 'w') as f:
                f.write(f"Timestamp: {datetime.now()}\nURL: {designi_url}\nIP: {client_ip}\nError: CRITICAL BROWSER EXECUTABLE NOT FOUND\nPaths checked:\n{chrome_executable_path}\n{headless_shell_path}")
            upload_debug_screenshot_to_drive(drive_service, error_log_path, folder_id, "CRITICAL_ERROR")
            os.remove(error_log_path) # Limpa o log local após tentativa de upload
        return {'success': False, 'error': f"Executável navegador não encontrado.", 'duration_seconds': time.time() - start_time }


    try:
        with sync_playwright() as p:
            try:
                # ... (Código de inicialização do Playwright, login, navegação - igual ao anterior) ...
                print("[TASK LOG] Iniciando Chromium headless...")
                browser = p.chromium.launch(**launch_options)
                context = browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
                page = context.new_page()
                page.set_default_timeout(90000)

                login_necessario = True
                # ... (lógica de cookies e login igual) ...
                if saved_cookies:
                    try:
                        print("[TASK LOG] Tentando usar cookies salvos...")
                        context.add_cookies(saved_cookies)
                        print(f"[TASK LOG] Navegando diretamente para URL do arquivo: {designi_url}")
                        page.goto(designi_url, wait_until='networkidle')
                        if "/login" not in page.url:
                            print("[TASK LOG] Cookies válidos! Login automático bem-sucedido.")
                            login_necessario = False
                        else:
                            print("[TASK LOG] Cookies expirados ou inválidos. Realizando login manual.")
                    except Exception as cookie_err:
                        print(f"[TASK WARNING] Erro ao usar cookies salvos: {cookie_err}. Realizando login manual.")
                else:
                    print("[TASK LOG] Nenhum cookie salvo encontrado. Realizando login manual.")

                if login_necessario:
                    print(f"[TASK LOG] Acessando página de login: {url_login}")
                    page.goto(url_login, wait_until='networkidle')
                    # ... (lógica de preencher form, captcha, clicar login - igual) ...
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
                            login_fail_screenshot_path = os.path.join(temp_dir, f'login_fail_screenshot_{int(time.time())}.png')
                            try:
                                page.screenshot(path=login_fail_screenshot_path)
                                print(f"[TASK DEBUG] Screenshot de falha de login salvo em: {login_fail_screenshot_path}")
                                # Tentar upload do screenshot de falha de login
                                upload_debug_screenshot_to_drive(drive_service, login_fail_screenshot_path, folder_id, "LOGIN_FAIL")
                            except Exception as ss_login_err:
                                print(f"[TASK WARNING] Não foi possível tirar/upload screenshot de falha de login: {ss_login_err}")
                            raise Exception(f"Falha login (ainda em /login): {nav_err}")
                        else: print(f"[TASK WARNING] Espera pós-login falhou, mas URL mudou: {page.url}")
                    print(f"[TASK LOG] Login OK! URL: {page.url}")

                    # ... (lógica de salvar cookies igual) ...
                    try:
                        cookies = context.cookies()
                        from app import save_designi_cookies
                        save_designi_cookies(cookies, client_ip)
                        print("[TASK LOG] Cookies salvos (ou tentativa de salvar) após login.")
                    except ImportError:
                         print("[TASK WARNING] Não foi possível importar save_designi_cookies do app.py. Cookies não serão salvos.")
                    except Exception as save_err:
                        print(f"[TASK WARNING] Erro ao salvar cookies: {save_err}")

                    print(f"[TASK LOG] Navegando para URL do arquivo: {designi_url}")
                    page.goto(designi_url, wait_until='networkidle')


                print(f"[TASK LOG] Página arquivo carregada. URL: {page.url}")
                print("[TASK LOG] Aguardando botão download (usando #downButton)...")
                download_button_selector = "#downButton"
                download_button = page.locator(download_button_selector)

                try:
                    # Screenshot ANTES de esperar (sem upload, só se falhar)
                    screenshot_path_before = os.path.join(temp_dir, f'before_wait_downbutton_{int(time.time())}.png')
                    print(f"[TASK DEBUG] Tirando screenshot antes de esperar pelo botão: {screenshot_path_before}")
                    page.screenshot(path=screenshot_path_before)
                    print(f"[TASK DEBUG] Screenshot 'antes' salvo localmente. Esperando por {download_button_selector} ficar visível...")

                    download_button.wait_for(state="visible", timeout=180000) # Mantém timeout aumentado
                    print("[TASK LOG] Botão #downButton visível encontrado!")
                    # Se deu certo, pode deletar o screenshot 'antes' local
                    if os.path.exists(screenshot_path_before):
                         try: os.remove(screenshot_path_before)
                         except Exception: print(f"[TASK WARNING] Falha ao remover screenshot 'antes' local: {screenshot_path_before}")

                except Exception as btn_err:
                    # Erro ao esperar pelo botão! Tenta fazer upload do screenshot 'antes'
                    print(f"[TASK ERROR] Timeout ou erro ao esperar por #downButton: {btn_err}")
                    if os.path.exists(screenshot_path_before):
                         print(f"[TASK DEBUG] Tentando upload do screenshot 'antes' ({screenshot_path_before}) devido ao erro.")
                         upload_debug_screenshot_to_drive(drive_service, screenshot_path_before, folder_id, "BEFORE_WAIT_FAIL")
                         # Não removemos localmente aqui para garantir que exista se o upload falhar

                    # Tira screenshot 'depois' da falha
                    screenshot_path_after_fail = os.path.join(temp_dir, f'after_fail_wait_downbutton_{int(time.time())}.png')
                    try:
                        page.screenshot(path=screenshot_path_after_fail)
                        print(f"[TASK DEBUG] Screenshot após falha salvo em: {screenshot_path_after_fail}")
                        # Tenta fazer upload do screenshot 'depois'
                        upload_debug_screenshot_to_drive(drive_service, screenshot_path_after_fail, folder_id, "AFTER_WAIT_FAIL")
                    except Exception as ss_fail_err:
                         print(f"[TASK WARNING] Não foi possível tirar/upload screenshot após falha: {ss_fail_err}")

                    raise Exception(f"Botão de download com id='downButton' não encontrado ou visível após 180s.")

                # --- Lógica de Clique, Popup e Download ---
                print("[TASK LOG] Configurando espera download...")
                # ... (código igual: expect_download, click, popup handling, save_as) ...
                with page.expect_download(timeout=300000) as download_info:
                    print("[TASK LOG] Clicando botão #downButton...")
                    download_button.click()
                    print("[TASK LOG] Clique realizado, aguardando popup/download...")
                    time.sleep(3)
                    thank_you_popup = page.locator("div.modal-content:has-text('Obrigado por baixar meu arquivo!')")
                    # ... (lógica do popup igual) ...
                    if thank_you_popup.count() > 0:
                        print("[TASK LOG] Popup agradecimento detectado")
                        close_button = thank_you_popup.locator("button[aria-label='Close'], button:has-text('Fechar'), button.close, [data-bs-dismiss='modal']").first
                        try:
                            if close_button.is_visible(timeout=5000):
                                 print("[TASK LOG] Tentando fechar popup...")
                                 close_button.click()
                                 print("[TASK LOG] Popup fechado (ou tentativa).")
                                 time.sleep(1)
                            else: print("[TASK LOG] Botão fechar popup não visível.")
                        except Exception as close_err:
                            print(f"[TASK WARNING] Erro ao tentar fechar popup: {close_err}")
                    else: print("[TASK LOG] Nenhum popup agradecimento detectado.")

                    print("[TASK LOG] Aguardando download começar...")
                    download = download_info.value

                if not download: raise Exception("Evento download não ocorreu ou timeout excedido (300s).")

                suggested_filename = download.suggested_filename or f"designi_download_{int(time.time())}.file"
                suggested_filename = "".join(c for c in suggested_filename if c.isalnum() or c in ('.', '_', '-')).rstrip()
                temp_file_path = os.path.join(temp_dir, suggested_filename)

                print(f"[TASK LOG] Download iniciado: {suggested_filename}. Salvando em: {temp_file_path}")
                download.save_as(temp_file_path)
                print(f"[TASK LOG] Download concluído: {temp_file_path}")

                if not os.path.exists(temp_file_path) or os.path.getsize(temp_file_path) == 0:
                     failure_reason = download.failure() # Sem await
                     # Tira um screenshot aqui também, pode ser útil
                     download_fail_path = os.path.join(temp_dir, f'download_fail_{int(time.time())}.png')
                     try:
                          page.screenshot(path=download_fail_path)
                          upload_debug_screenshot_to_drive(drive_service, download_fail_path, folder_id, "DOWNLOAD_FAIL")
                     except Exception as ss_down_err:
                          print(f"[TASK WARNING] Falha ao tirar/upload screenshot de falha de download: {ss_down_err}")
                     raise Exception(f"Falha salvar download ou arquivo vazio: {temp_file_path}. Razão: {failure_reason}")

                file_size = os.path.getsize(temp_file_path)
                print(f"[TASK LOG] Arquivo salvo. Tamanho: {file_size} bytes")

            except Exception as pw_error:
                print(f"[TASK ERROR] Erro durante automação Playwright: {pw_error}")
                if 'page' in locals() and page and not page.is_closed():
                    screenshot_path_generic_error = os.path.join(temp_dir, f'playwright_error_{int(time.time())}.png')
                    try:
                        page.screenshot(path=screenshot_path_generic_error)
                        print(f"[TASK DEBUG] Screenshot de erro Playwright salvo: {screenshot_path_generic_error}")
                        # Tenta upload do screenshot de erro genérico
                        upload_debug_screenshot_to_drive(drive_service, screenshot_path_generic_error, folder_id, "PLAYWRIGHT_ERROR")
                    except Exception as ss_err: print(f"[TASK WARNING] Não foi possível tirar/upload screenshot erro Playwright: {ss_err}")
                raise

            finally:
                if browser: print("[TASK LOG] Fechando navegador Playwright..."); browser.close(); print("[TASK LOG] Navegador fechado.")

        # --- Upload Google Drive (Arquivo Principal) ---
        if temp_file_path and os.path.exists(temp_file_path):
             # Verifica se o drive_service foi obtido com sucesso antes de tentar usá-lo
             if not drive_service:
                 print("[TASK ERROR] Serviço Google Drive não disponível para upload do arquivo principal.")
                 raise Exception("Serviço Google Drive indisponível.")

             print("[TASK LOG] Iniciando upload Google Drive do arquivo principal...")
             # ... (Resto do código de upload do arquivo principal igual) ...
             filename = os.path.basename(temp_file_path)
             file_metadata = {'name': filename}
             if folder_id: file_metadata['parents'] = [folder_id] # Pasta principal
             mimetype, _ = mimetypes.guess_type(temp_file_path)
             mimetype = mimetype or 'application/octet-stream'
             media = MediaFileUpload(temp_file_path, mimetype=mimetype, resumable=True)
             print(f"[TASK LOG] Enviando '{filename}' ({mimetype}, {os.path.getsize(temp_file_path)} bytes) para Google Drive...")
             try:
                 file = drive_service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
                 print(f"[TASK LOG] Upload Drive concluído. ID: {file.get('id')}")
             except Exception as upload_err:
                 print(f"[TASK ERROR] Erro durante upload Google Drive: {upload_err}")
                 raise Exception(f"Erro upload Google Drive: {upload_err}")
             try:
                 print("[TASK LOG] Definindo permissão pública...")
                 drive_service.permissions().create(fileId=file.get('id'), body={'role': 'reader', 'type': 'anyone'}).execute()
                 print("[TASK LOG] Permissão pública definida.")
             except Exception as perm_err:
                 print(f"[TASK WARNING] Falha ao definir permissão pública para {file.get('id')}: {perm_err}")

             end_time = time.time(); duration = end_time - start_time
             print(f"[TASK SUCCESS] Tarefa concluída com sucesso em {duration:.2f} segundos.")
             # Limpa o arquivo local principal APÓS upload bem sucedido
             if temp_file_path and os.path.exists(temp_file_path):
                 try:
                     os.remove(temp_file_path)
                     print(f"[TASK LOG] Temp principal removido: {temp_file_path}")
                 except Exception as e_clean_main:
                     print(f"[TASK WARNING] Erro remover temp principal {temp_file_path}: {e_clean_main}")
             return {'success': True, 'file_id': file.get('id'), 'download_link': file.get('webViewLink'), 'filename': filename, 'duration_seconds': duration}
        elif not temp_file_path:
             raise Exception("Nenhum arquivo foi baixado (temp_file_path não definido).")
        else:
             raise Exception(f"Arquivo temporário não encontrado ou inválido pós-download: {temp_file_path}")

    except Exception as e:
        end_time = time.time(); duration = end_time - start_time
        error_message = f"Erro na tarefa download: {getattr(e, 'message', str(e))}"
        print(f"[TASK FAILED] {error_message} (Duração: {duration:.2f}s)")
        print(traceback.format_exc()) # Log completo do erro
        return {'success': False, 'error': error_message, 'duration_seconds': duration}

    # Não precisamos mais do finally para limpar temp_file_path aqui, pois a limpeza é feita
    # após o upload bem sucedido do arquivo principal ou na função de upload de debug (opcionalmente)