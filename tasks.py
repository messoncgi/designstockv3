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
import traceback
from datetime import datetime

# --- Função Auxiliar para Upload de Debug (Inalterada) ---
def upload_debug_screenshot_to_drive(drive_service, local_file_path, folder_id, base_filename):
    """Faz upload de um arquivo de screenshot/log para uma subpasta 'Debug' no Drive."""
    if not drive_service or not os.path.exists(local_file_path):
        print(f"[DEBUG UPLOAD] Serviço Drive indisponível ou arquivo local não encontrado: {local_file_path}")
        return
    try:
        debug_folder_name = "Debug Screenshots"
        query = f"name='{debug_folder_name}' and mimeType='application/vnd.google-apps.folder' and '{folder_id}' in parents and trashed=false"
        response = drive_service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        debug_folder_id = None
        if response.get('files'):
            debug_folder_id = response.get('files')[0].get('id')
        else:
            print(f"[DEBUG UPLOAD] Criando pasta debug '{debug_folder_name}'...")
            folder_metadata = {'name': debug_folder_name,'mimeType': 'application/vnd.google-apps.folder','parents': [folder_id]}
            folder = drive_service.files().create(body=folder_metadata, fields='id').execute()
            debug_folder_id = folder.get('id')
            print(f"[DEBUG UPLOAD] Pasta debug criada: {debug_folder_id}")
        if not debug_folder_id: raise Exception("Não foi possível encontrar ou criar a pasta de debug.")

        mimetype, _ = mimetypes.guess_type(local_file_path)
        mimetype = mimetype or 'application/octet-stream'

        filename_drive = f"{base_filename}_{os.path.basename(local_file_path)}"
        file_metadata = {'name': filename_drive,'parents': [debug_folder_id]}
        media = MediaFileUpload(local_file_path, mimetype=mimetype, resumable=True)
        print(f"[DEBUG UPLOAD] Enviando '{filename_drive}' ({mimetype}) para Google Drive...")
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        print(f"[DEBUG UPLOAD] Arquivo de debug enviado com sucesso. ID: {file.get('id')}")
    except Exception as e:
        print(f"[DEBUG UPLOAD ERROR] Falha ao fazer upload do arquivo de debug {local_file_path}: {e}")
        print(traceback.format_exc())


# --- Funções Auxiliares (get_drive_service_from_credentials, solve_captcha - Inalteradas) ---
def get_drive_service_from_credentials(credentials_base64_str):
    # ... (código completo igual ao anterior) ...
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
    # ... (código completo igual ao anterior) ...
    captcha_element = page.locator("iframe[src*='recaptcha']")
    if captcha_element.count() > 0 and captcha_api_key:
        print("[TASK LOG] CAPTCHA detectado! Tentando resolver...")
        site_key = page.evaluate('''() => { const d = document.querySelector('.g-recaptcha'); return d ? d.getAttribute('data-sitekey') : null; }''')
        if not site_key: raise Exception('Não foi possível encontrar site key do CAPTCHA.')
        # ... (resto da lógica 2captcha igual) ...
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
        time.sleep(1) # Pausa após inserir token
        return True
    return False

# --- Função de Verificação de Login (VERSÃO BASEADA NAS SUGESTÕES) ---
def check_login_status(page):
    """Verifica status do login checando msg de sucesso ou ausência de botão 'Entrar'."""
    print("[TASK DEBUG] Verificando login:")

    # 1. Procurar pela mensagem de sucesso explícita
    success_message_locator = page.locator("*:has-text('Login efetuado com sucesso!')")
    try:
        success_message_locator.wait_for(state="visible", timeout=5000) # Timeout curto (5s)
        print("[TASK DEBUG] Mensagem 'Login efetuado com sucesso!' encontrada. Login OK.")
        return True
    except Exception:
        print("[TASK DEBUG] Mensagem 'Login efetuado com sucesso!' NÃO encontrada.")

    # 2. Se a mensagem não foi encontrada, verificar a AUSÊNCIA do botão/link "Entrar"
    print("[TASK DEBUG] Verificando ausência do botão/link 'Entrar'...")
    login_prompt_locator = page.locator("a:has-text('Entrar'), button:has-text('Entrar'), a:has-text('Login'), button:has-text('Login')").first
    try:
        # Verifica se o botão Entrar NÃO está visível (ou não existe)
        # Usamos um timeout curto aqui também. Se ele aparecer rápido, consideramos não logado.
        login_prompt_locator.wait_for(state="hidden", timeout=5000)
        print("[TASK DEBUG] Botão/link 'Entrar' NÃO está visível (ou oculto rapidamente). Login provavelmente OK.")
        return True
    except Exception:
        # Se wait_for(state="hidden") der timeout, significa que o botão 'Entrar' ESTÁ visível
        print("[TASK DEBUG] Botão/link 'Entrar' ESTÁ visível. Login provavelmente FALHOU.")
        # Tira um screenshot para análise se a verificação falhar neste ponto
        try:
             verify_fail_path = os.path.join(temp_dir, f'verify_login_fail_entrar_visible_{int(time.time())}.png')
             print(f"[TASK DEBUG] Tirando screenshot da falha (botão Entrar visível): {verify_fail_path}")
             page.screenshot(path=verify_fail_path)
             # Tentativa de upload silenciosa (sem esperar drive_service aqui, pode falhar)
             # upload_debug_screenshot_to_drive(drive_service, verify_fail_path, folder_id, "VERIFY_FAIL_ENTRAR_VISIBLE")
        except Exception as ss_err:
             print(f"[TASK WARNING] Falha ao tirar screenshot da verificação (Entrar visível): {ss_err}")
        return False

# --- A Tarefa Principal do RQ ---
def perform_designi_download_task(
    designi_url,
    client_ip,
    folder_id,
    email,
    senha,
    captcha_api_key,
    drive_credentials_base64,
    url_login='https://designi.com.br/login',
    saved_cookies=None
    ):
    print(f"[TASK LOG] Iniciando tarefa para URL: {designi_url} (IP: {client_ip})")
    # Variáveis Globais da Função
    global temp_dir # Tornar temp_dir acessível na função de verificação para screenshots
    temp_file_path = None
    browser = None
    context = None
    drive_service = None
    start_time = time.time()
    temp_dir = '/tmp/designi_downloads' # Definido globalmente para a função
    os.makedirs(temp_dir, exist_ok=True)
    print(f"[TASK LOG] Usando diretório temp: {temp_dir}")

    # Tenta obter o serviço do Drive logo no início
    try:
        print("[TASK LOG] Tentando obter serviço Google Drive...")
        drive_service = get_drive_service_from_credentials(drive_credentials_base64)
        if not drive_service: print("[TASK WARNING] Não foi possível obter serviço Google Drive no início...")
        else: print("[TASK LOG] Serviço Google Drive obtido com sucesso.")
    except Exception as drive_init_err: print(f"[TASK WARNING] Erro ao obter serviço Google Drive no início: {drive_init_err}")

    # Configuração Playwright
    # ... (launch_options, verificação de path, erro crítico - igual) ...
    chrome_executable_path = "/ms-playwright/chromium-1161/chrome-linux/chrome"
    headless_shell_path = "/ms-playwright/chromium_headless_shell-1161/chrome-linux/headless_shell"
    launch_options = {'headless': True, 'args': ['--no-sandbox', '--disable-setuid-sandbox']}
    if os.path.exists(chrome_executable_path): launch_options['executable_path'] = chrome_executable_path
    elif os.path.exists(headless_shell_path): launch_options['executable_path'] = headless_shell_path
    else:
        print(f"[TASK ERROR] CRÍTICO: Executável navegador NÃO encontrado.")
        if drive_service:
            # ... (lógica para logar erro crítico no drive) ...
            error_log_path = os.path.join(temp_dir, f'critical_browser_error_{int(time.time())}.log')
            with open(error_log_path, 'w') as f: f.write(f"Timestamp: {datetime.now()}\nURL: {designi_url}\nIP: {client_ip}\nError: CRITICAL BROWSER EXECUTABLE NOT FOUND...")
            upload_debug_screenshot_to_drive(drive_service, error_log_path, folder_id, "CRITICAL_ERROR")
            try: os.remove(error_log_path)
            except Exception: pass
        return {'success': False, 'error': f"Executável navegador não encontrado.", 'duration_seconds': time.time() - start_time }


    login_bem_sucedido = False

    try:
        with sync_playwright() as p:
            try:
                print("[TASK LOG] Iniciando Chromium headless...")
                browser = p.chromium.launch(**launch_options)
                context = browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
                page = context.new_page()
                page.set_default_timeout(90000)

                # === TENTATIVA DE LOGIN COM COOKIES ===
                if saved_cookies:
                    print("[TASK LOG] Tentando usar cookies salvos...")
                    try:
                        context.add_cookies(saved_cookies)
                        print(f"[TASK LOG] Cookies adicionados. Navegando para URL do arquivo para verificar login: {designi_url}")
                        page.goto(designi_url, wait_until='networkidle', timeout=60000)
                        print(f"[TASK LOG] Página carregada ({page.url}). Verificando status do login...")

                        # >>> VERIFICAÇÃO DE LOGIN (Nova Função) <<<
                        if check_login_status(page):
                             print("[TASK LOG] Verificação de login com cookies BEM-SUCEDIDA.")
                             login_bem_sucedido = True
                        else:
                             print("[TASK WARNING] Verificação de login com cookies FALHOU. Tentará login manual.")
                             # Upload do screenshot já é feito dentro de check_login_status se falhar

                    except Exception as cookie_err:
                        print(f"[TASK WARNING] Erro ao tentar usar cookies/navegar: {cookie_err}. Tentará login manual.")
                else:
                    print("[TASK LOG] Nenhum cookie salvo encontrado. Iniciando login manual.")

                # === TENTATIVA DE LOGIN MANUAL (se necessário) ===
                if not login_bem_sucedido:
                    print("[TASK LOG] Iniciando processo de login manual...")
                    # ... (Navegar para login, preencher email/senha, solve_captcha - igual) ...
                    print(f"[TASK LOG] Acessando página de login: {url_login}")
                    page.goto(url_login, wait_until='networkidle', timeout=60000)
                    if not email or not senha: raise ValueError('Credenciais Designi não fornecidas.')

                    print("[TASK LOG] Preenchendo credenciais...")
                    page.fill("input[name=email]", email, timeout=30000)
                    page.fill("input[name=password]", senha, timeout=30000)
                    solve_captcha(page, captcha_api_key, url_login)

                    print("[TASK LOG] Tentando clicar botão login...")
                    login_button_locator = page.locator('button[type="submit"]:has-text("login"), button:has-text("Entrar"), input[type="submit"][value*="Login" i]').first
                    login_button_locator.wait_for(state="visible", timeout=30000)
                    login_button_locator.click()

                    print("[TASK LOG] Aguardando navegação pós-login ou indicador...")
                    # Espera um pouco mais flexível: ou a URL muda OU a verificação de login passa
                    # Isso pode ser complexo, vamos manter a espera pela URL e depois verificar
                    try:
                        page.wait_for_url(lambda url: "/login" not in url, timeout=60000)
                        print(f"[TASK LOG] Navegou para fora da página de login. URL atual: {page.url}")
                    except Exception as nav_err:
                         if "/login" in page.url:
                              print(f"[TASK ERROR] Falha ao sair da página de login após clique.")
                              # ... (screenshot e upload de erro de navegação) ...
                              login_fail_path = os.path.join(temp_dir, f'manual_login_nav_fail_{int(time.time())}.png')
                              try:
                                   page.screenshot(path=login_fail_path)
                                   upload_debug_screenshot_to_drive(drive_service, login_fail_path, folder_id, "MANUAL_LOGIN_NAV_FAIL")
                              except Exception as ss_nav_err: print(f"[TASK WARNING] Falha screenshot falha navegação login: {ss_nav_err}")
                              raise Exception(f"Falha ao navegar após login manual: {nav_err}")
                         else:
                              print(f"[TASK WARNING] Timeout ao esperar URL pós-login, mas URL mudou para: {page.url}. Verificando status...")


                    # >>> VERIFICAÇÃO DE LOGIN PÓS-MANUAL (Nova Função) <<<
                    print("[TASK LOG] Verificando status do login após tentativa manual...")
                    time.sleep(2) # Pequena pausa antes de verificar
                    if check_login_status(page):
                        print("[TASK LOG] Verificação de login manual BEM-SUCEDIDA.")
                        login_bem_sucedido = True
                        # Salvar cookies APÓS confirmação do login manual
                        try:
                            cookies = context.cookies()
                            from app import save_designi_cookies
                            save_designi_cookies(cookies, client_ip)
                            print("[TASK LOG] Cookies salvos após login manual bem-sucedido.")
                        except ImportError: print("[TASK WARNING] Não importou save_designi_cookies...")
                        except Exception as save_err: print(f"[TASK WARNING] Erro ao salvar cookies: {save_err}")
                    else:
                        print("[TASK ERROR] Verificação de login manual FALHOU (baseado na nova função).")
                        # Upload do screenshot de falha já é feito dentro de check_login_status
                        raise Exception("Login manual realizado, mas verificação falhou (msg sucesso não vista / botão Entrar ainda visível).")

                # === PROCEDER PARA DOWNLOAD (APENAS SE LOGIN FOI BEM SUCEDIDO) ===
                if not login_bem_sucedido:
                    raise Exception("Estado de login inconsistente. Não foi possível confirmar login.")

                # Garante que estamos na página correta
                if page.url != designi_url:
                     print(f"[TASK LOG] Navegando para a URL final do arquivo: {designi_url}")
                     page.goto(designi_url, wait_until='networkidle', timeout=60000)
                     print(f"[TASK LOG] Página final do arquivo carregada ({page.url}).")
                else:
                     print(f"[TASK LOG] Já está na URL do arquivo ({page.url}).")

                # Procurar botão de Download
                print("[TASK LOG] Procurando botão download (usando #downButton)...")
                download_button_selector = "#downButton"
                download_button = page.locator(download_button_selector)
                try:
                    # Screenshot ANTES de esperar pelo botão (logado)
                    screenshot_path_before = os.path.join(temp_dir, f'before_wait_downbutton_LOGGEDIN_{int(time.time())}.png')
                    print(f"[TASK DEBUG] Tirando screenshot ANTES de esperar pelo botão (logado): {screenshot_path_before}")
                    page.screenshot(path=screenshot_path_before)
                    print(f"[TASK DEBUG] Screenshot 'antes' salvo localmente. Esperando por {download_button_selector} ficar visível...")

                    download_button.wait_for(state="visible", timeout=180000)
                    print("[TASK LOG] Botão #downButton visível encontrado!")
                    if os.path.exists(screenshot_path_before):
                         try: os.remove(screenshot_path_before)
                         except Exception: pass

                except Exception as btn_err:
                    print(f"[TASK ERROR] Timeout ou erro ao esperar por #downButton (mesmo após login verificado): {btn_err}")
                    if os.path.exists(screenshot_path_before):
                        print(f"[TASK DEBUG] Tentando upload do screenshot 'antes' ({screenshot_path_before}) devido ao erro.")
                        upload_debug_screenshot_to_drive(drive_service, screenshot_path_before, folder_id, "BEFORE_WAIT_LOGGEDIN_FAIL")
                    screenshot_path_after_fail = os.path.join(temp_dir, f'after_fail_wait_downbutton_LOGGEDIN_{int(time.time())}.png')
                    try:
                        page.screenshot(path=screenshot_path_after_fail)
                        print(f"[TASK DEBUG] Screenshot após falha salvo em: {screenshot_path_after_fail}")
                        upload_debug_screenshot_to_drive(drive_service, screenshot_path_after_fail, folder_id, "AFTER_WAIT_LOGGEDIN_FAIL")
                    except Exception as ss_fail_err: print(f"[TASK WARNING] Falha ao tirar/upload screenshot 'depois': {ss_fail_err}")
                    raise Exception(f"Login verificado, mas botão de download ('#downButton') não encontrado/visível após 180s.")

                # --- Lógica de Clique, Popup e Download ---
                # ... (código igual: expect_download, click, popup, save_as, etc) ...
                print("[TASK LOG] Configurando espera download...")
                with page.expect_download(timeout=300000) as download_info:
                    print("[TASK LOG] Clicando botão #downButton...")
                    download_button.click()
                    # ... (resto da lógica igual) ...
                    print("[TASK LOG] Clique realizado, aguardando popup/download...")
                    time.sleep(3) # Pausa
                    thank_you_popup = page.locator("div.modal-content:has-text('Obrigado por baixar meu arquivo!')")
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
                     failure_reason = download.failure()
                     # ... (screenshot e upload de falha de download) ...
                     download_fail_path = os.path.join(temp_dir, f'download_fail_{int(time.time())}.png')
                     try:
                          page.screenshot(path=download_fail_path)
                          upload_debug_screenshot_to_drive(drive_service, download_fail_path, folder_id, "DOWNLOAD_FAIL")
                     except Exception as ss_down_err: print(f"[TASK WARNING] Falha screenshot falha download: {ss_down_err}")
                     raise Exception(f"Falha salvar download ou arquivo vazio: {temp_file_path}. Razão: {failure_reason}")

                file_size = os.path.getsize(temp_file_path)
                print(f"[TASK LOG] Arquivo salvo. Tamanho: {file_size} bytes")


            except Exception as pw_error:
                # Erro genérico Playwright
                print(f"[TASK ERROR] Erro durante automação Playwright: {pw_error}")
                # ... (screenshot e upload de erro playwright) ...
                if 'page' in locals() and page and not page.is_closed():
                    screenshot_path_generic_error = os.path.join(temp_dir, f'playwright_error_{int(time.time())}.png')
                    try:
                        page.screenshot(path=screenshot_path_generic_error)
                        print(f"[TASK DEBUG] Screenshot de erro Playwright salvo: {screenshot_path_generic_error}")
                        upload_debug_screenshot_to_drive(drive_service, screenshot_path_generic_error, folder_id, "PLAYWRIGHT_ERROR")
                    except Exception as ss_err: print(f"[TASK WARNING] Falha screenshot erro Playwright: {ss_err}")
                raise

            finally:
                if browser: print("[TASK LOG] Fechando navegador Playwright..."); browser.close(); print("[TASK LOG] Navegador fechado.")

        # --- Upload Google Drive (Arquivo Principal) ---
        if temp_file_path and os.path.exists(temp_file_path):
             if not drive_service:
                 print("[TASK ERROR] Serviço Google Drive não disponível para upload do arquivo principal.")
                 raise Exception("Serviço Google Drive indisponível.")

             print("[TASK LOG] Iniciando upload Google Drive do arquivo principal...")
             # ... (Código de upload principal inalterado) ...
             filename = os.path.basename(temp_file_path)
             file_metadata = {'name': filename}
             if folder_id: file_metadata['parents'] = [folder_id]
             mimetype, _ = mimetypes.guess_type(temp_file_path)
             mimetype = mimetype or 'application/octet-stream'
             media = MediaFileUpload(temp_file_path, mimetype=mimetype, resumable=True)
             print(f"[TASK LOG] Enviando '{filename}' ({mimetype}, {os.path.getsize(temp_file_path)} bytes) para Google Drive...")
             # ... (try/except upload, permissão, etc - inalterado) ...
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
             # Limpa o temp principal APÓS upload bem sucedido
             if temp_file_path and os.path.exists(temp_file_path):
                  try:
                      os.remove(temp_file_path)
                      print(f"[TASK LOG] Temp principal removido: {temp_file_path}")
                  except Exception as e_clean_main: print(f"[TASK WARNING] Erro remover temp principal {temp_file_path}: {e_clean_main}")
             return {'success': True, 'file_id': file.get('id'), 'download_link': file.get('webViewLink'), 'filename': filename, 'duration_seconds': duration}
        elif not temp_file_path:
             raise Exception("Nenhum arquivo foi baixado (temp_file_path não definido).")
        else:
             raise Exception(f"Arquivo temporário não encontrado ou inválido pós-download: {temp_file_path}")

    except Exception as e:
        # Erro geral na tarefa
        end_time = time.time(); duration = end_time - start_time
        error_message = f"Erro na tarefa download: {getattr(e, 'message', str(e))}"
        print(f"[TASK FAILED] {error_message} (Duração: {duration:.2f}s)")
        print(traceback.format_exc())
        return {'success': False, 'error': error_message, 'duration_seconds': duration}