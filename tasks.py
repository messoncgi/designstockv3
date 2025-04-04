# -*- coding: utf-8 -*-
import os
import time
import json
import base64
import requests
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
import mimetypes
import traceback

# --- FUNÇÃO HELPER PARA UPLOAD DE SCREENSHOT ---
# ... (Função upload_debug_screenshot igual à versão anterior) ...
def upload_debug_screenshot(page, filename_prefix, drive_service, base_folder_id, temp_dir):
    # Adicionada verificação inicial de base_folder_id
    if not drive_service or not page or page.is_closed() or not base_folder_id:
        print(f"[TASK DEBUG] Screenshot '{filename_prefix}' não pode ser tirado (Drive/Page/BaseFolderID indisponível ou pág fechada).")
        return

    debug_folder_name = "printsdebug"; debug_folder_id = None
    try:
        # ... (Restante da lógica de encontrar/criar pasta igual) ...
        query = f"'{base_folder_id}' in parents and name='{debug_folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        response = drive_service.files().list(q=query, spaces='drive', fields='files(id, name)').execute(); folders = response.get('files', [])
        if folders: debug_folder_id = folders[0].get('id')
        else:
            print(f"[TASK DEBUG] Pasta '{debug_folder_name}' não encontrada. Criando..."); folder_metadata = {'name': debug_folder_name, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [base_folder_id]}
            folder = drive_service.files().create(body=folder_metadata, fields='id').execute(); debug_folder_id = folder.get('id'); print(f"[TASK DEBUG] Pasta '{debug_folder_name}' criada com ID: {debug_folder_id}")
        if not debug_folder_id: print("[TASK ERROR] Não foi possível obter/criar ID da pasta de debug."); return

        # ... (Lógica de tirar screenshot e fazer upload igual) ...
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S"); safe_prefix = re.sub(r'[\\/*?:"<>|]', "_", filename_prefix).strip()
        screenshot_filename = f"{safe_prefix}_{timestamp}.png"; local_screenshot_path = os.path.join(temp_dir, screenshot_filename)
        print(f"[TASK DEBUG] Tirando screenshot: {local_screenshot_path}")
        if page.is_closed(): print(f"[TASK WARNING] Tentativa screenshot '{screenshot_filename}', mas página fechada."); return
        page.screenshot(path=local_screenshot_path, full_page=True)
        if os.path.exists(local_screenshot_path) and os.path.getsize(local_screenshot_path) > 0:
            print(f"[TASK DEBUG] Upload screenshot '{screenshot_filename}' para pasta ID {debug_folder_id}...")
            file_metadata = {'name': screenshot_filename, 'parents': [debug_folder_id]}; media = MediaFileUpload(local_screenshot_path, mimetype='image/png')
            try: drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute(); print("[TASK DEBUG] Upload screenshot OK.")
            except HttpError as upload_error: print(f"[TASK ERROR] Falha upload screenshot: {upload_error}")
            finally:
                try: os.remove(local_screenshot_path)
                except OSError as e: print(f"[TASK WARNING] Falha remover screenshot local {local_screenshot_path}: {e}")
        else: print(f"[TASK WARNING] Screenshot local não encontrado/vazio: {local_screenshot_path}")
    except PlaywrightError as pe: print(f"[TASK ERROR] Erro Playwright screenshot '{filename_prefix}': {pe}")
    except HttpError as drive_error: print(f"[TASK ERROR] Erro API GDrive (debug screenshot): {drive_error}")
    except Exception as e: print(f"[TASK ERROR] Erro inesperado em upload_debug_screenshot: {e}"); traceback.print_exc()


# --- Funções Auxiliares (get_drive_service_from_credentials, solve_captcha) ---
# ... (iguais) ...
def get_drive_service_from_credentials(credentials_base64_str):
    SCOPES = ['https://www.googleapis.com/auth/drive']
    try:
        if not credentials_base64_str: print("[TASK ERROR] GOOGLE_CREDENTIALS_BASE64 não configurada."); return None
        try: json_str = base64.b64decode(credentials_base64_str).decode('utf-8'); service_account_info = json.loads(json_str)
        except Exception as e: print(f"[TASK ERROR] Erro decode/parse credenciais Google: {e}"); return None
        required_fields = ['client_email', 'private_key', 'project_id']
        if not all(field in service_account_info for field in required_fields): print(f"[TASK ERROR] Campos faltando nas credenciais."); return None
        credentials = service_account.Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
        return build('drive', 'v3', credentials=credentials)
    except Exception as e: print(f"[TASK ERROR] Erro criar serviço Google Drive: {e}"); return None

def solve_captcha(page, captcha_api_key, url_login):
    captcha_element = page.locator("iframe[src*='recaptcha']")
    if captcha_element.count() > 0 and captcha_api_key:
        print("[TASK DEBUG] CAPTCHA detectado! Resolvendo...")
        site_key = page.evaluate('''() => { const d = document.querySelector('.g-recaptcha'); return d ? d.getAttribute('data-sitekey') : null; }''')
        if not site_key: raise Exception('Não encontrou site key.')
        response = requests.post("http://2captcha.com/in.php", data={ "key": captcha_api_key, "method": "userrecaptcha", "googlekey": site_key, "pageurl": url_login, "json": 1 }, timeout=20)
        response.raise_for_status(); request_result = response.json()
        if request_result.get("status") != 1: raise Exception(f'Falha enviar CAPTCHA: {request_result.get("request")}')
        captcha_id = request_result["request"]; print(f"[TASK DEBUG] CAPTCHA ID: {captcha_id}. Aguardando...")
        token = None; start_time = time.time()
        while time.time() - start_time < 180:
            try:
                print(f"[TASK DEBUG] Tentando resultado CAPTCHA (ID: {captcha_id})...")
                result_response = requests.get(f"http://2captcha.com/res.php?key={captcha_api_key}&action=get&id={captcha_id}&json=1", timeout=15)
                result_response.raise_for_status(); result = result_response.json(); print(f"[TASK DEBUG] Resultado CAPTCHA: {result}")
                if result.get("status") == 1: token = result["request"]; print("[TASK DEBUG] CAPTCHA resolvido!"); break
                elif result.get("request") == "CAPCHA_NOT_READY": print("[TASK DEBUG] CAPTCHA não pronto...")
                else: print(f"[TASK WARNING] Erro API 2Captcha: {result.get('request')}"); time.sleep(10)
            except requests.exceptions.Timeout: print(f"[TASK WARNING] Timeout buscar resultado CAPTCHA.")
            except requests.exceptions.RequestException as captcha_req_err: print(f"[TASK WARNING] Erro rede CAPTCHA: {captcha_req_err}."); time.sleep(5)
            except Exception as inner_err: print(f"[TASK ERROR] Erro loop CAPTCHA: {inner_err}"); time.sleep(10)
            print("[TASK DEBUG] Aguardando 5s antes próxima verif. CAPTCHA..."); time.sleep(5)
        if not token: raise Exception('Timeout/Falha resolver CAPTCHA (180s).')
        page.evaluate(f"const ta = document.getElementById('g-recaptcha-response'); if (ta) ta.value = '{token}';"); print("[TASK DEBUG] Token CAPTCHA inserido.")
        time.sleep(1); return True
    print("[TASK DEBUG] Nenhum CAPTCHA visível/chave API.")
    return False

# --- Função de Verificação de Login via /conta ---
# ... (igual) ...
def check_login_via_account_page(page, base_url="https://www.designi.com.br/"):
    print("[TASK DEBUG] --- Verificando login via /conta ---")
    account_url = urljoin(base_url, "/conta"); login_url_part = "/login"
    try:
        print(f"[TASK DEBUG] Navegando para: {account_url}"); page.goto(account_url, wait_until='load', timeout=60000)
        final_url = page.url; print(f"[TASK DEBUG] URL final: {final_url}")
        final_path = urlparse(final_url).path; account_path = urlparse(account_url).path
        if final_path == account_path: print("[TASK DEBUG] ====> SUCESSO: Login OK (/conta)."); return True
        elif login_url_part in final_path: print("[TASK DEBUG] ====> FALHA: Login FALHOU (Redirecionado /login)."); return False
        else: print(f"[TASK WARNING] ====> FALHA: Login FALHOU (Redirecionamento inesperado {final_url})."); return False
    except PlaywrightTimeoutError: print(f"[TASK WARNING] Timeout navegando {account_url}."); return False
    except Exception as e: print(f"[TASK ERROR] Erro verificar /conta: {e}"); traceback.print_exc(); return False

# --- Tarefa Principal RQ ---
def perform_designi_download_task(
    designi_url, client_ip, folder_id, email, senha, captcha_api_key,
    drive_credentials_base64, url_login='https://designi.com.br/login',
    saved_cookies=None
    ):
    print(f"[TASK LOG] =======================================================")
    print(f"[TASK LOG] Iniciando tarefa URL: {designi_url} (IP: {client_ip})")
    # --- CORREÇÃO LINHA 178 ---
    # Adicionar verificação se folder_id foi passado corretamente antes de usar
    if folder_id:
        print(f"[TASK LOG] Pasta Base GDrive: {folder_id}") # Esta era a linha 178 original
    else:
        print("[TASK WARNING] Pasta Base GDrive (folder_id) NÃO fornecida ou vazia!")
        # Decide se quer continuar sem folder_id (talvez salvando na raiz?) ou falhar
        # Por segurança, vamos falhar se a pasta base não for fornecida.
        return {'success': False, 'error': 'ID da Pasta Base do Google Drive não fornecido.', 'duration_seconds': 0}
    print(f"[TASK LOG] =======================================================")
    # --- FIM CORREÇÃO ---
    temp_file_path = None; browser = None; context = None; page = None
    drive_service = None; start_time = time.time()
    temp_dir = '/tmp/designi_downloads'; os.makedirs(temp_dir, exist_ok=True)
    print(f"[TASK LOG] Usando temp dir: {temp_dir}")

    try: print("[TASK LOG] Obtendo GDrive..."); drive_service = get_drive_service_from_credentials(drive_credentials_base64); assert drive_service; print("[TASK LOG] GDrive OK.")
    except Exception as drive_init_err: return {'success': False, 'error': f"Erro GDrive: {drive_init_err}", 'duration_seconds': time.time() - start_time}

    launch_options = {'headless': True, 'args': ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu', '--single-process']}
    chrome_executable_path = "/ms-playwright/chromium-1161/chrome-linux/chrome"
    if os.path.exists(chrome_executable_path): launch_options['executable_path'] = chrome_executable_path; print(f"[TASK DEBUG] Usando: {chrome_executable_path}")
    else: print(f"[TASK WARNING] Usando executável padrão Playwright.")

    login_bem_sucedido = False
    try: # Playwright main block
        with sync_playwright() as p:
            print("[TASK LOG] Iniciando Chromium..."); browser = p.chromium.launch(**launch_options)
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
            context = browser.new_context(user_agent=user_agent); page = context.new_page(); page.set_default_timeout(90000)
            print("[TASK DEBUG] Browser, contexto e página OK.")

            # === LOGIN FLOW ===
            if saved_cookies:
                 print("[TASK LOG] --- Tentando COOKIES ---")
                 try:
                    context.add_cookies(saved_cookies); print(f"[TASK DEBUG] {len(saved_cookies)} cookies add. Verificando /conta...")
                    if check_login_via_account_page(page): print("[TASK LOG] SUCESSO: Login cookie OK."); login_bem_sucedido = True; upload_debug_screenshot(page, "SucessoLoginCookieConta", drive_service, folder_id, temp_dir)
                    else: print("[TASK WARNING] FALHA: Login cookie FALHOU."); upload_debug_screenshot(page, "FalhaLoginCookieConta", drive_service, folder_id, temp_dir); login_bem_sucedido = False
                 except Exception as cookie_err:
                     print(f"[TASK WARNING] Erro verif. cookie: {cookie_err}.")
                     login_bem_sucedido = False
                     upload_debug_screenshot(page, "ErroVerificacaoCookieConta", drive_service, folder_id, temp_dir)
                     try:
                         context.clear_cookies()
                     except:
                         pass
            else: print("[TASK LOG] Sem cookies salvos.")

            if not login_bem_sucedido:
                # ... (lógica login manual igual) ...
                print("[TASK LOG] --- Iniciando LOGIN MANUAL ---")
                try:
                    print(f"[TASK LOG] Acessando: {url_login}"); page.goto(url_login, wait_until='load', timeout=60000); print(f"[TASK LOG] Página login ({page.url}) OK."); upload_debug_screenshot(page, "PaginaLoginCarregada", drive_service, folder_id, temp_dir)
                    if not email or not senha: raise ValueError('Credenciais não fornecidas.')
                    print("[TASK LOG] Preenchendo..."); page.locator("input[name=email]").fill(email, timeout=30000); page.locator("input[name=password]").fill(senha, timeout=30000); print("[TASK DEBUG] Credenciais OK.")
                    if solve_captcha(page, captcha_api_key, url_login): print("[TASK DEBUG] CAPTCHA OK.")
                    upload_debug_screenshot(page, "AntesClickLoginManual", drive_service, folder_id, temp_dir); print("[TASK LOG] Clicando login..."); login_button_selector = 'button[type="submit"]:has-text("login"), button:has-text("Entrar")'; page.locator(login_button_selector).first.click(timeout=30000); print("[TASK DEBUG] Click OK. Verificando /conta..."); page.wait_for_timeout(5000)
                    if check_login_via_account_page(page):
                        print("[TASK LOG] SUCESSO: Login manual OK."); login_bem_sucedido = True; upload_debug_screenshot(page, "SucessoLoginManualConta", drive_service, folder_id, temp_dir)
                        try: cookies = context.cookies(); from app import save_designi_cookies; save_designi_cookies(cookies, client_ip); print("[TASK LOG] Cookies salvos.")
                        except Exception as save_err: print(f"[TASK WARNING] Erro salvar cookies: {save_err}")
                    else: print("[TASK ERROR] FALHA: Verificação /conta FALHOU pós-login."); upload_debug_screenshot(page, "FalhaLoginManualConta", drive_service, folder_id, temp_dir); raise Exception("Verificação /conta falhou pós-login manual.")
                except Exception as login_manual_err: print(f"[TASK ERROR] Erro login manual: {login_manual_err}"); upload_debug_screenshot(page, "ErroProcessoLoginManual", drive_service, folder_id, temp_dir); raise login_manual_err

            # === PROCESSO DE DOWNLOAD ===
            if not login_bem_sucedido: raise Exception("Login não bem-sucedido.")
            print("[TASK LOG] --- Iniciando DOWNLOAD ---")
            print(f"[TASK LOG] Navegando URL final: {designi_url}"); page.goto(designi_url, wait_until='load', timeout=90000); print(f"[TASK LOG] Página final OK ({page.url})."); upload_debug_screenshot(page, "PaginaArquivoFinalCarregada", drive_service, folder_id, temp_dir)

            # --- FOCO #downButton + ESPERA ROBUSTA ---
            download_button_selector = "#downButton"
            print(f"[TASK LOG] Procurando botão: '{download_button_selector}'...")
            download_button = page.locator(download_button_selector)
            try:
                print("[TASK DEBUG] Screenshot ANTES espera (V4)..."); upload_debug_screenshot(page, "AntesEsperarBotaoDownV4", drive_service, folder_id, temp_dir)
                print(f"[TASK DEBUG] 1. Rolando para '{download_button_selector}'..."); download_button.scroll_into_view_if_needed(timeout=10000)
                print("[TASK DEBUG] 2. Esperando VISÍVEL (60s)..."); download_button.wait_for(state="visible", timeout=60000)
                print("[TASK DEBUG] 3. Esperando HABILITADO (15s)..."); download_button.wait_for(state="enabled", timeout=15000)
                print(f"[TASK LOG] Botão '{download_button_selector}' OK!"); download_button_to_click = download_button # Usar a variável localizada
            except Exception as btn_err:
                print(f"[TASK ERROR] Timeout/Erro esperar '{download_button_selector}': {btn_err}")
                print("[TASK DEBUG] Screenshot FALHA botão (V4)..."); upload_debug_screenshot(page, "FalhaEncontrarBotaoDownV4", drive_service, folder_id, temp_dir)
                try: print(f"[TASK DEBUG] HTML body:\n---\n{page.locator('body').inner_html(timeout=2000)[:3000]}...\n---")
                except Exception as html_err: print(f"[TASK DEBUG] Erro log HTML: {html_err}")
                raise Exception(f"Botão '{download_button_selector}' não encontrado/visível/habilitado.")

            # --- Clique e Espera Download ---
            # ... (igual) ...
            print("[TASK LOG] Configurando espera 'download' (300s)...")
            popup_detected = None
            def handle_popup(popup): nonlocal popup_detected; print(f"[TASK DEBUG] POPUP: {popup.url}"); popup_detected = popup
            page.on("popup", handle_popup)
            try:
                with page.expect_download(timeout=300000) as download_info:
                    print(f"[TASK LOG] ---> CLICANDO '{download_button_selector}' <---"); page.wait_for_timeout(500); download_button_to_click.click(); print("[TASK LOG] Click OK. Aguardando download...")
                download = download_info.value; print(f"[TASK LOG] Download OK! Nome: {download.suggested_filename}")
                page.remove_listener("popup", handle_popup);
                if popup_detected: print(f"[TASK WARNING] Popup detectado: {popup_detected.url}")
            except PlaywrightTimeoutError as timeout_err: print(f"[TASK ERROR] !!! Timeout (300s) esperando download !!!"); upload_debug_screenshot(page, "TimeoutEsperandoDownloadEventV4", drive_service, folder_id, temp_dir); raise timeout_err
            except Exception as click_download_err: print(f"[TASK ERROR] Erro clique/espera download: {click_download_err}"); upload_debug_screenshot(page, "ErroClickOuEsperaDownloadV4", drive_service, folder_id, temp_dir); raise click_download_err

            # --- Salvar arquivo ---
            # ... (lógica igual) ...
            if not download: raise Exception("Objeto download não obtido.")
            suggested_filename = download.suggested_filename
            if not suggested_filename: parsed_url_dl = urlparse(download.url); _, ext = os.path.splitext(parsed_url_dl.path); suggested_filename = f"designi_download_{int(time.time())}{ext if ext else '.file'}"; print(f"[TASK WARNING] Sem nome. Usando: {suggested_filename}")
            else: suggested_filename = re.sub(r'[\\/*?:"<>|]', "_", suggested_filename).strip()
            temp_file_path = os.path.join(temp_dir, suggested_filename); print(f"[TASK LOG] Salvando em: {temp_file_path}")
            try: download.save_as(temp_file_path); print(f"[TASK LOG] Salvo: {temp_file_path}")
            except Exception as save_err: failure_reason = download.failure(); raise Exception(f"Falha salvar: {save_err}. Razão: {failure_reason}")
            if not os.path.exists(temp_file_path): raise Exception(f"Arquivo {temp_file_path} não existe.")
            file_size = os.path.getsize(temp_file_path)
            if file_size == 0: failure_reason = download.failure(); raise Exception(f"Arquivo vazio. Razão: {failure_reason or 'Desconhecida'}")
            print(f"[TASK LOG] Arquivo salvo. Tamanho: {file_size} bytes")

    # --- Fim Bloco Playwright ---
    except Exception as e:
        # ... (Tratamento de erro geral igual) ...
        end_time = time.time(); duration = end_time - start_time; error_message = f"Erro automação: {e}"
        print(f"[TASK FAILED] {error_message} (Duração: {duration:.2f}s)")
        print(f"--- TRACEBACK ---"); traceback.print_exc(); print(f"--- TRACEBACK FIM ---")
        if page and not page.is_closed(): print("[TASK DEBUG] Screenshot final erro..."); upload_debug_screenshot(page, "ErroGeralFinalV5", drive_service, folder_id, temp_dir) # V5 no nome
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except:
                pass
        return {'success': False, 'error': error_message, 'duration_seconds': duration}
    finally:
        # ... (Fechamento do browser igual) ...
        print("[TASK DEBUG] Bloco Finally principal.")
        if browser and browser.is_connected():
            try: print("[TASK LOG] Fechando navegador (finally)..."); browser.close(); print("[TASK LOG] Navegador fechado (finally).")
            except Exception as close_final_err: print(f"[TASK WARNING] Erro fechar navegador (finally): {close_final_err}")

    # --- Upload Google Drive ---
    # ... (lógica igual) ...
    print("[TASK LOG] --- Iniciando UPLOAD GDrive ---")
    result = {'success': False, 'error': 'Upload não iniciado', 'duration_seconds': time.time() - start_time}
    if temp_file_path and os.path.exists(temp_file_path) and file_size > 0:
         filename = os.path.basename(temp_file_path); file_metadata = {'name': filename}
         if folder_id: file_metadata['parents'] = [folder_id]
         mimetype, _ = mimetypes.guess_type(temp_file_path); mimetype = mimetype or 'application/octet-stream'
         media = MediaFileUpload(temp_file_path, mimetype=mimetype, resumable=True); print(f"[TASK LOG] Enviando '{filename}'...")
         try:
             file = drive_service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
             file_id = file.get('id'); web_view_link = file.get('webViewLink')
             if not file_id: raise Exception("Upload falhou (sem ID).")
             print(f"[TASK LOG] Upload OK. ID: {file_id}")
             try: print("[TASK LOG] Permissão pública..."); drive_service.permissions().create(fileId=file_id, body={'role': 'reader', 'type': 'anyone'}).execute(); print("[TASK LOG] Permissão OK.")
             except Exception as perm_err: print(f"[TASK WARNING] Falha permissão {file_id}: {perm_err}")
             end_time = time.time(); duration = end_time - start_time; print(f"[TASK SUCCESS] Concluído em {duration:.2f}s.")
             result = {'success': True, 'file_id': file_id, 'download_link': web_view_link, 'filename': filename, 'duration_seconds': round(duration, 2)}
         except Exception as upload_err: end_time = time.time(); duration = end_time - start_time; error_message = f"Erro upload GDrive: {upload_err}"; print(f"[TASK FAILED] {error_message}"); traceback.print_exc(); result = {'success': False, 'error': error_message, 'duration_seconds': round(duration, 2)}
    elif not temp_file_path: end_time = time.time(); duration = end_time - start_time; result = {'success': False, 'error': "Arquivo não baixado.", 'duration_seconds': round(duration, 2)}
    elif not os.path.exists(temp_file_path): end_time = time.time(); duration = end_time - start_time; result = {'success': False, 'error': f"Temp não encontrado.", 'duration_seconds': round(duration, 2)}
    elif file_size == 0: end_time = time.time(); duration = end_time - start_time; result = {'success': False, 'error': f"Temp vazio.", 'duration_seconds': round(duration, 2)}

    if temp_file_path and os.path.exists(temp_file_path):
        try:
            os.remove(temp_file_path)
        except:
            pass
    print(f"[TASK LOG] ===> Resultado final: {result}")
    return result