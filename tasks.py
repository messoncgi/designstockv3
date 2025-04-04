# -*- coding: utf-8 -*-
import os
import time
import json
import base64
import requests
import re
from datetime import datetime
from urllib.parse import urljoin # Para construir URL da conta
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
import mimetypes
import traceback

# --- FUNÇÃO HELPER PARA UPLOAD DE SCREENSHOT ---
# ... (Função upload_debug_screenshot permanece igual à versão anterior) ...
def upload_debug_screenshot(page, filename_prefix, drive_service, base_folder_id, temp_dir):
    """Tira um screenshot, salva temporariamente e faz upload para pasta debug no Drive."""
    if not drive_service or not page or page.is_closed(): # Adiciona verificação se a página está fechada
        print(f"[TASK DEBUG] Screenshot '{filename_prefix}' não pode ser tirado (Drive Service, Page indisponível ou fechada).")
        return

    debug_folder_name = "printsdebug"
    debug_folder_id = None

    try:
        # 1. Tentar encontrar a pasta 'printsdebug'
        query = f"'{base_folder_id}' in parents and name='{debug_folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        response = drive_service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        folders = response.get('files', [])
        if folders:
            debug_folder_id = folders[0].get('id')
            # print(f"[TASK DEBUG] Pasta de debug '{debug_folder_name}' encontrada com ID: {debug_folder_id}") # Mover log para após ter certeza que precisa usar
        else:
            # 2. Se não encontrou, criar a pasta
            print(f"[TASK DEBUG] Pasta '{debug_folder_name}' não encontrada. Criando...")
            folder_metadata = {
                'name': debug_folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [base_folder_id]
            }
            folder = drive_service.files().create(body=folder_metadata, fields='id').execute()
            debug_folder_id = folder.get('id')
            print(f"[TASK DEBUG] Pasta '{debug_folder_name}' criada com ID: {debug_folder_id}")

        if not debug_folder_id:
            print("[TASK ERROR] Não foi possível obter/criar ID da pasta de debug. Screenshot não será salvo no Drive.")
            return

        # Log da pasta só quando for realmente usar
        # print(f"[TASK DEBUG] Usando pasta de debug '{debug_folder_name}' (ID: {debug_folder_id})")

        # 3. Tirar e salvar screenshot localmente
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_prefix = re.sub(r'[\\/*?:"<>|]', "_", filename_prefix).strip()
        screenshot_filename = f"{safe_prefix}_{timestamp}.png"
        local_screenshot_path = os.path.join(temp_dir, screenshot_filename)

        print(f"[TASK DEBUG] Tirando screenshot: {local_screenshot_path}")
        # Garantir que a página está aberta antes de tirar o screenshot
        if page.is_closed():
            print(f"[TASK WARNING] Tentativa de tirar screenshot '{screenshot_filename}', mas a página já estava fechada.")
            return
        page.screenshot(path=local_screenshot_path, full_page=True)

        # 4. Fazer upload
        if os.path.exists(local_screenshot_path) and os.path.getsize(local_screenshot_path) > 0:
            print(f"[TASK DEBUG] Fazendo upload do screenshot '{screenshot_filename}' para pasta ID {debug_folder_id}...")
            file_metadata = {'name': screenshot_filename, 'parents': [debug_folder_id]}
            media = MediaFileUpload(local_screenshot_path, mimetype='image/png')
            try:
                drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                print("[TASK DEBUG] Upload do screenshot concluído.")
            except HttpError as upload_error:
                print(f"[TASK ERROR] Falha no upload do screenshot para o Drive: {upload_error}")
            finally:
                # 5. Remover arquivo local
                try:
                    os.remove(local_screenshot_path)
                    # print(f"[TASK DEBUG] Screenshot local removido: {local_screenshot_path}") # Log menos verboso
                except OSError as e:
                    print(f"[TASK WARNING] Falha ao remover screenshot local {local_screenshot_path}: {e}")
        else:
             print(f"[TASK WARNING] Screenshot local não encontrado ou vazio após captura: {local_screenshot_path}")

    except PlaywrightError as pe:
         print(f"[TASK ERROR] Erro do Playwright ao tirar screenshot '{filename_prefix}': {pe}")
    except HttpError as drive_error:
        print(f"[TASK ERROR] Erro na API do Google Drive ao gerenciar pasta/upload de debug: {drive_error}")
    except Exception as e:
        print(f"[TASK ERROR] Erro inesperado na função upload_debug_screenshot: {e}")
        traceback.print_exc()


# --- Funções Auxiliares (get_drive_service_from_credentials, solve_captcha) ---
# ... (código de get_drive_service_from_credentials e solve_captcha permanece igual) ...
def get_drive_service_from_credentials(credentials_base64_str):
    """Obtém o serviço autenticado do Google Drive a partir das credenciais."""
    SCOPES = ['https://www.googleapis.com/auth/drive']
    try:
        # ... (código interno igual) ...
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
    """Resolve o reCAPTCHA na página usando o serviço 2Captcha, se presente."""
    # ... (código interno igual) ...
    captcha_element = page.locator("iframe[src*='recaptcha']")
    if captcha_element.count() > 0 and captcha_api_key:
        print("[TASK DEBUG] CAPTCHA detectado! Tentando resolver...")
        site_key = page.evaluate('''() => { const d = document.querySelector('.g-recaptcha'); return d ? d.getAttribute('data-sitekey') : null; }''')
        if not site_key: raise Exception('Não foi possível encontrar site key do CAPTCHA.')
        response = requests.post("http://2captcha.com/in.php", data={ "key": captcha_api_key, "method": "userrecaptcha", "googlekey": site_key, "pageurl": url_login, "json": 1 }, timeout=20)
        response.raise_for_status(); request_result = response.json()
        if request_result.get("status") != 1: raise Exception(f'Falha ao enviar CAPTCHA: {request_result.get("request")}')
        captcha_id = request_result["request"]
        print(f"[TASK DEBUG] CAPTCHA enviado, ID: {captcha_id}. Aguardando solução...")
        token = None; start_time = time.time()
        while time.time() - start_time < 180: # Tentar por 3 minutos
            time.sleep(5) # Verificar a cada 5 segundos
            try:
                result_response = requests.get(f"http://2captcha.com/res.php?key={captcha_api_key}&action=get&id={captcha_id}&json=1", timeout=10)
                result_response.raise_for_status(); result = result_response.json()
                if result.get("status") == 1: token = result["request"]; print("[TASK DEBUG] CAPTCHA resolvido!"); break
                elif result.get("request") == "CAPCHA_NOT_READY": print("[TASK DEBUG] CAPTCHA ainda não pronto..."); continue
                else: print(f"[TASK WARNING] Erro obter resultado CAPTCHA: {result.get('request')}"); time.sleep(10); # Esperar mais se der erro
            except requests.exceptions.RequestException as captcha_req_err: print(f"[TASK WARNING] Erro rede verificar CAPTCHA: {captcha_req_err}. Tentando novamente..."); time.sleep(5)
        if not token: raise Exception('Timeout ou erro ao resolver CAPTCHA excedido (180s).')
        page.evaluate(f"const ta = document.getElementById('g-recaptcha-response'); if (ta) ta.value = '{token}';")
        print("[TASK DEBUG] Token CAPTCHA inserido.")
        time.sleep(1); return True
    print("[TASK DEBUG] Nenhum CAPTCHA visível encontrado ou chave API não fornecida.")
    return False


# --- NOVA Função de Verificação de Login ---
def check_login_via_account_page(page, base_url="https://www.designi.com.br/"):
    """Verifica o login tentando acessar a página /conta e checando a URL final."""
    print("[TASK DEBUG] --- Iniciando verificação de status do login via /conta ---")
    account_url = urljoin(base_url, "/conta")
    login_url_part = "/login" # Parte da URL de login para verificar redirecionamento

    try:
        print(f"[TASK DEBUG] Navegando para a página da conta: {account_url}")
        # Usar wait_until='domcontentloaded' pode ser mais rápido e suficiente
        page.goto(account_url, wait_until='domcontentloaded', timeout=45000) # Timeout 45s
        final_url = page.url
        print(f"[TASK DEBUG] Navegação para /conta concluída. URL final: {final_url}")

        # Verificar se a URL final AINDA é a da conta ou foi redirecionada para login
        if account_url in final_url:
             # Se contiver /conta, consideramos logado (mesmo que haja parâmetros extras)
             print("[TASK DEBUG] ====> SUCESSO: URL final contém '/conta'. Login considerado OK.")
             print("[TASK DEBUG] --- Fim da verificação de status do login (Resultado: True) ---")
             return True
        elif login_url_part in final_url:
             print("[TASK DEBUG] ====> FALHA: URL final contém '/login'. Fomos redirecionados. Login considerado FALHO.")
             print("[TASK DEBUG] --- Fim da verificação de status do login (Resultado: False) ---")
             return False
        else:
             # Redirecionou para outro lugar inesperado? Considerar falha por segurança.
             print(f"[TASK WARNING] ====> FALHA: URL final ({final_url}) NÃO é '/conta' nem '/login'. Redirecionamento inesperado. Login considerado FALHO.")
             print("[TASK DEBUG] --- Fim da verificação de status do login (Resultado: False) ---")
             return False

    except PlaywrightTimeoutError:
        print(f"[TASK WARNING] Timeout ao tentar navegar para {account_url}. Verificação falhou.")
        print("[TASK DEBUG] --- Fim da verificação de status do login (Resultado: False - Timeout) ---")
        return False
    except Exception as e:
        print(f"[TASK ERROR] Erro inesperado ao verificar login via /conta: {e}")
        traceback.print_exc()
        print("[TASK DEBUG] --- Fim da verificação de status do login (Resultado: False - Erro) ---")
        return False


# --- A Tarefa Principal do RQ (Com nova verificação e mais screenshots) ---
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
    print(f"[TASK LOG] =======================================================")
    print(f"[TASK LOG] Iniciando tarefa para URL: {designi_url} (IP: {client_ip})")
    print(f"[TASK LOG] Pasta Base GDrive ID: {folder_id}")
    print(f"[TASK LOG] =======================================================")
    temp_file_path = None
    browser = None
    context = None
    page = None
    drive_service = None
    start_time = time.time()
    temp_dir = '/tmp/designi_downloads'
    os.makedirs(temp_dir, exist_ok=True)
    print(f"[TASK LOG] Usando diretório temp: {temp_dir}")

    # Obter serviço do Drive (essencial)
    try:
        print("[TASK LOG] Obtendo serviço Google Drive...")
        drive_service = get_drive_service_from_credentials(drive_credentials_base64)
        if not drive_service: raise Exception("Falha ao obter serviço Google Drive.")
        print("[TASK LOG] Serviço Google Drive obtido.")
    except Exception as drive_init_err:
         print(f"[TASK CRITICAL] Erro crítico ao obter serviço Google Drive: {drive_init_err}")
         end_time = time.time(); duration = end_time - start_time
         return {'success': False, 'error': f"Erro inicialização Drive: {drive_init_err}", 'duration_seconds': duration}

    # Configuração Playwright
    # ... (igual) ...
    chrome_executable_path = "/ms-playwright/chromium-1161/chrome-linux/chrome"
    headless_shell_path = "/ms-playwright/chromium_headless_shell-1161/chrome-linux/headless_shell"
    launch_options = {'headless': True, 'args': ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu', '--single-process']} # Adicionado flags
    executable_path_found = None
    if os.path.exists(chrome_executable_path): executable_path_found = chrome_executable_path
    elif os.path.exists(headless_shell_path): executable_path_found = headless_shell_path

    if executable_path_found:
        launch_options['executable_path'] = executable_path_found
        print(f"[TASK DEBUG] Usando executável: {executable_path_found}")
    else:
        print(f"[TASK CRITICAL] Executável navegador NÃO encontrado.")
        end_time = time.time(); duration = end_time - start_time
        return {'success': False, 'error': "Executável navegador não encontrado.", 'duration_seconds': duration}

    login_bem_sucedido = False

    try:
        with sync_playwright() as p:
            print("[TASK LOG] Iniciando Chromium headless...")
            browser = p.chromium.launch(**launch_options)
            print("[TASK DEBUG] Navegador lançado.")
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
            context = browser.new_context(user_agent=user_agent)
            print("[TASK DEBUG] Contexto criado.")
            page = context.new_page()
            print("[TASK DEBUG] Página criada.")
            page.set_default_timeout(90000)
            print("[TASK DEBUG] Timeout padrão da página definido para 90000ms.")

            # === TENTATIVA DE LOGIN COM COOKIES ===
            if saved_cookies:
                 print("[TASK LOG] --- Tentando usar COOKIES salvos ---")
                 try:
                    print(f"[TASK DEBUG] Adicionando {len(saved_cookies)} cookies ao contexto.")
                    context.add_cookies(saved_cookies)
                    print("[TASK LOG] Cookies adicionados. Verificando login via /conta...")
                    # USA A NOVA FUNÇÃO DE VERIFICAÇÃO
                    if check_login_via_account_page(page):
                         print("[TASK LOG] ====> SUCESSO: Login com cookies VERIFICADO via /conta.")
                         login_bem_sucedido = True
                         # Tira screenshot do sucesso com cookie
                         upload_debug_screenshot(page, "SucessoLoginCookieConta", drive_service, folder_id, temp_dir)
                    else:
                         print("[TASK WARNING] FALHA: Login com cookies NÃO verificado via /conta.")
                         # Tira screenshot da falha com cookie
                         upload_debug_screenshot(page, "FalhaLoginCookieConta", drive_service, folder_id, temp_dir)
                         login_bem_sucedido = False # Garante que vai para login manual
                 except Exception as cookie_err:
                    print(f"[TASK WARNING] Erro durante a verificação com cookies via /conta: {cookie_err}.")
                    # Tira screenshot do erro
                    upload_debug_screenshot(page, "ErroVerificacaoCookieConta", drive_service, folder_id, temp_dir)
                    login_bem_sucedido = False
                    try: context.clear_cookies(); print("[TASK DEBUG] Cookies limpos após erro.")
                    except: pass # Ignora erro ao limpar

            else:
                print("[TASK LOG] Nenhum cookie salvo encontrado.")

            # === TENTATIVA DE LOGIN MANUAL (se necessário) ===
            if not login_bem_sucedido:
                print("[TASK LOG] --- Iniciando processo de LOGIN MANUAL ---")
                try:
                    print(f"[TASK LOG] Acessando página de login: {url_login}")
                    page.goto(url_login, wait_until='domcontentloaded', timeout=60000)
                    print(f"[TASK LOG] Página de login ({page.url}) carregada.")
                    # Screenshot da página de login
                    upload_debug_screenshot(page, "PaginaLoginCarregada", drive_service, folder_id, temp_dir)

                    if not email or not senha: raise ValueError('Credenciais Designi não fornecidas.')

                    print("[TASK LOG] Preenchendo credenciais...")
                    page.locator("input[name=email]").fill(email, timeout=30000)
                    page.locator("input[name=password]").fill(senha, timeout=30000)
                    print("[TASK DEBUG] Credenciais preenchidas.")

                    captcha_resolvido = solve_captcha(page, captcha_api_key, url_login)
                    if captcha_resolvido: print("[TASK DEBUG] CAPTCHA resolvido (ou tentado).")

                    # Screenshot ANTES de clicar em login
                    upload_debug_screenshot(page, "AntesClickLoginManual", drive_service, folder_id, temp_dir)

                    print("[TASK LOG] Clicando botão login...")
                    login_button_selector = 'button[type="submit"]:has-text("login"), button:has-text("Entrar"), input[type="submit"][value*="Login" i], input[type="submit"][value*="Entrar" i]'
                    page.locator(login_button_selector).first.click(timeout=30000)
                    print("[TASK DEBUG] Botão de login clicado.")

                    print("[TASK LOG] Aguardando um momento e verificando login via /conta...")
                    time.sleep(5) # Espera um pouco após o clique antes de verificar

                    # USA A NOVA FUNÇÃO DE VERIFICAÇÃO
                    if check_login_via_account_page(page):
                        print("[TASK LOG] ====> SUCESSO: Login manual VERIFICADO via /conta.")
                        login_bem_sucedido = True
                        # Screenshot do sucesso do login manual (já na página /conta)
                        upload_debug_screenshot(page, "SucessoLoginManualConta", drive_service, folder_id, temp_dir)
                        try:
                            cookies = context.cookies()
                            from app import save_designi_cookies
                            save_designi_cookies(cookies, client_ip)
                            print("[TASK LOG] Cookies salvos após login manual.")
                        except Exception as save_err: print(f"[TASK WARNING] Erro ao salvar cookies: {save_err}")
                    else:
                        print("[TASK ERROR] FALHA: Login manual realizado, mas verificação via /conta FALHOU.")
                        # Screenshot da falha do login manual (pode estar em /login ou outra URL)
                        upload_debug_screenshot(page, "FalhaLoginManualConta", drive_service, folder_id, temp_dir)
                        raise Exception("Login manual realizado, mas verificação via /conta falhou.")

                except Exception as login_manual_err:
                     print(f"[TASK ERROR] Erro durante processo de login manual: {login_manual_err}")
                     # Screenshot do erro durante o processo
                     upload_debug_screenshot(page, "ErroProcessoLoginManual", drive_service, folder_id, temp_dir)
                     print(traceback.format_exc())
                     raise login_manual_err

            # === PROCEDER PARA DOWNLOAD ===
            if not login_bem_sucedido:
                raise Exception("Erro crítico: Tentando prosseguir sem login bem-sucedido.")

            print("[TASK LOG] --- Iniciando processo de DOWNLOAD ---")
            print(f"[TASK LOG] Navegando para a URL final do arquivo: {designi_url}")
            page.goto(designi_url, wait_until='domcontentloaded', timeout=90000)
            print(f"[TASK LOG] Página final do arquivo carregada ({page.url}).")
            upload_debug_screenshot(page, "PaginaArquivoFinalCarregada", drive_service, folder_id, temp_dir)

            # --- AJUSTE NO SELETOR E ESPERA DO BOTÃO ---
            # Tentar um seletor mais genérico baseado em texto visível ou atributos comuns
            # O ID '#downButton' parece não confiável.
            # Priorizar botões/links com texto "Download" ou "Baixar"
            download_button_selector = "button:has-text('Download'), button:has-text('Baixar'), a:has-text('Download'), a:has-text('Baixar'), a[download], #downButton"
            print(f"[TASK LOG] Procurando botão download com seletores: '{download_button_selector}'...")
            # Usar .first para pegar o primeiro que corresponder
            download_button = page.locator(download_button_selector).first

            try:
                print("[TASK DEBUG] Tirando screenshot ANTES de esperar pelo botão de download...")
                upload_debug_screenshot(page, "AntesEsperarBotaoDownV2", drive_service, folder_id, temp_dir)

                print("[TASK DEBUG] Esperando botão download ficar visível (timeout 240s)...")
                # Aumentar bastante o timeout para visibilidade, talvez a página demore
                download_button.wait_for(state="visible", timeout=240000) # 4 minutos
                print("[TASK DEBUG] Botão download VISÍVEL. Esperando ficar habilitado (timeout 30s)...")
                download_button.wait_for(state="enabled", timeout=30000) # 30s extras
                print(f"[TASK LOG] Botão download encontrado com '{download_button_selector}', visível e habilitado!")

            except Exception as btn_err:
                print(f"[TASK ERROR] Timeout ou erro ao esperar por botão download ('{download_button_selector}'): {btn_err}")
                print("[TASK DEBUG] Tirando screenshot NO MOMENTO DA FALHA ao encontrar/esperar botão...")
                # Captura o screenshot ANTES de re-levantar a exceção
                if page and not page.is_closed():
                     upload_debug_screenshot(page, "FalhaEncontrarBotaoDownV2", drive_service, folder_id, temp_dir)
                else:
                     print("[TASK WARNING] Não foi possível tirar screenshot da falha do botão (página fechada?).")
                # Log HTML (útil se o screenshot falhar)
                try:
                    if page and not page.is_closed():
                         button_area_html = page.locator('body').inner_html(timeout=2000)
                         print(f"[TASK DEBUG] HTML (parcial) onde botão deveria estar:\n---\n{button_area_html[:1500]}...\n---")
                except Exception as html_err: print(f"[TASK DEBUG] Não capturou HTML da área do botão: {html_err}")
                # Re-levanta a exceção para parar a execução da tarefa
                raise Exception(f"Botão de download ('{download_button_selector}') não encontrado/visível/habilitado após timeout.")


            # --- Lógica de Clique, Popup e Download ---
            # ... (igual à versão anterior, incluindo handle_popup e expect_download) ...
            print("[TASK LOG] Configurando espera pelo evento 'download' (timeout 300s)...")
            popup_detected = None
            def handle_popup(popup):
                nonlocal popup_detected; print(f"[TASK DEBUG] !! POPUP DETECTADO !! URL: {popup.url}"); popup_detected = popup
            page.on("popup", handle_popup)
            print("[TASK DEBUG] Listener para 'popup' adicionado.")

            try:
                with page.expect_download(timeout=300000) as download_info:
                    print(f"[TASK LOG] ---> CLICANDO no botão download encontrado <---")
                    # Adicionar uma pequena espera antes do clique pode ajudar em alguns casos
                    page.wait_for_timeout(500) # 0.5 segundos
                    download_button.click()
                    print("[TASK LOG] Clique realizado. Aguardando início do evento 'download'...")

                download = download_info.value
                print(f"[TASK LOG] Evento 'download' recebido! Nome sugerido: {download.suggested_filename}")
                page.remove_listener("popup", handle_popup)
                if popup_detected: print(f"[TASK WARNING] Popup detectado durante processo (URL: {popup_detected.url}). Download principal recebido.")

            except PlaywrightTimeoutError as timeout_err:
                 print(f"[TASK ERROR] !!! Timeout (300s) EXCEDIDO esperando evento 'download' após clique !!!")
                 print(f"[TASK ERROR] Detalhes do Timeout: {timeout_err}")
                 if page and not page.is_closed():
                      upload_debug_screenshot(page, "TimeoutEsperandoDownloadEventV2", drive_service, folder_id, temp_dir)
                 raise timeout_err # Re-throw
            except Exception as click_download_err:
                 print(f"[TASK ERROR] Erro inesperado durante clique/espera download: {click_download_err}")
                 if page and not page.is_closed():
                      upload_debug_screenshot(page, "ErroClickOuEsperaDownloadV2", drive_service, folder_id, temp_dir)
                 print(traceback.format_exc())
                 raise click_download_err


            # --- Processa e salva o arquivo baixado ---
            # ... (lógica de salvar e verificar arquivo igual à anterior) ...
            if not download: raise Exception("Objeto 'download' não obtido.")
            suggested_filename = download.suggested_filename
            if not suggested_filename:
                # ... (lógica nome fallback igual) ...
                parsed_url = urlparse(download.url); _, ext = os.path.splitext(parsed_url.path)
                suggested_filename = f"designi_download_{int(time.time())}{ext if ext else '.file'}"
                print(f"[TASK WARNING] Download sem nome sugerido. Usando: {suggested_filename}")
            else:
                 suggested_filename = re.sub(r'[\\/*?:"<>|]', "_", suggested_filename).strip()
                 print(f"[TASK DEBUG] Nome de arquivo sugerido limpo: {suggested_filename}")

            temp_file_path = os.path.join(temp_dir, suggested_filename)
            print(f"[TASK LOG] Salvando download em: {temp_file_path}")
            try:
                download.save_as(temp_file_path)
                print(f"[TASK LOG] Download salvo: {temp_file_path}")
            except Exception as save_err:
                print(f"[TASK ERROR] Falha ao salvar download em {temp_file_path}: {save_err}")
                failure_reason = download.failure(); print(f"[TASK DEBUG] Razão da falha: {failure_reason}")
                raise Exception(f"Falha ao salvar download: {save_err}. Razão: {failure_reason}")

            if not os.path.exists(temp_file_path): raise Exception(f"Arquivo {temp_file_path} não existe após save_as.")
            file_size = os.path.getsize(temp_file_path)
            if file_size == 0:
                 failure_reason = download.failure(); print(f"[TASK WARNING] Arquivo salvo VAZIO. Razão: {failure_reason}")
                 raise Exception(f"Arquivo salvo vazio. Razão: {failure_reason or 'Desconhecida'}")
            print(f"[TASK LOG] Arquivo salvo. Tamanho: {file_size} bytes")


    # --- Fim do Bloco 'with sync_playwright()' ---
    except Exception as e:
        end_time = time.time(); duration = end_time - start_time
        error_message = f"Erro durante automação: {getattr(e, 'message', str(e))}"
        print(f"[TASK FAILED] {error_message} (Duração: {duration:.2f}s)")
        print(f"--- TRACEBACK INÍCIO ---"); traceback.print_exc(); print(f"--- TRACEBACK FIM ---")
        # Tenta tirar screenshot final ANTES de fechar o browser no finally
        if page and not page.is_closed():
             print("[TASK DEBUG] Tentando screenshot final de erro...")
             upload_debug_screenshot(page, "ErroGeralFinal", drive_service, folder_id, temp_dir)
        else:
             print("[TASK DEBUG] Não foi possível tirar screenshot final (página/browser fechado?).")
        # Limpeza de arquivo temp se existir
        if temp_file_path and os.path.exists(temp_file_path):
             try: os.remove(temp_file_path); print(f"[TASK DEBUG] Temp removido após erro: {temp_file_path}")
             except Exception as e_clean: print(f"[TASK WARNING] Erro ao remover temp {temp_file_path} após erro: {e_clean}")
        # Retorna falha
        return {'success': False, 'error': error_message, 'duration_seconds': duration}

    finally:
        # Garante fechamento do browser
        print("[TASK DEBUG] Bloco Finally principal.")
        if browser and browser.is_connected():
            try: print("[TASK LOG] Fechando navegador (finally)..."); browser.close(); print("[TASK LOG] Navegador fechado (finally).")
            except Exception as close_final_err: print(f"[TASK WARNING] Erro fechar navegador (finally): {close_final_err}")
        else: print("[TASK DEBUG] Navegador já fechado ou não conectado (finally).")

    # --- Upload Google Drive (Arquivo Principal) ---
    # ... (lógica de upload do arquivo principal igual à anterior) ...
    print("[TASK LOG] --- Iniciando UPLOAD para Google Drive ---")
    # ... (Código igual para upload, permissão, retorno de sucesso/falha no upload) ...
    if temp_file_path and os.path.exists(temp_file_path) and file_size > 0:
         print(f"[TASK LOG] Preparando upload: {temp_file_path}")
         filename = os.path.basename(temp_file_path); file_metadata = {'name': filename}
         if folder_id: file_metadata['parents'] = [folder_id]
         mimetype, _ = mimetypes.guess_type(temp_file_path); mimetype = mimetype or 'application/octet-stream'
         media = MediaFileUpload(temp_file_path, mimetype=mimetype, resumable=True)
         print(f"[TASK LOG] Enviando '{filename}' ({mimetype}, {file_size} bytes)...")
         file = None
         try:
             file = drive_service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
             file_id = file.get('id'); web_view_link = file.get('webViewLink')
             if not file_id: raise Exception("Upload Drive falhou (sem ID).")
             print(f"[TASK LOG] Upload Drive OK. ID: {file_id}")
             try:
                 print("[TASK LOG] Definindo permissão pública..."); drive_service.permissions().create(fileId=file_id, body={'role': 'reader', 'type': 'anyone'}).execute(); print("[TASK LOG] Permissão pública OK.")
             except Exception as perm_err: print(f"[TASK WARNING] Falha permissão pública {file_id}: {perm_err}")
             end_time = time.time(); duration = end_time - start_time
             print(f"[TASK SUCCESS] Concluído em {duration:.2f}s.")
             result = {'success': True, 'file_id': file_id, 'download_link': web_view_link, 'filename': filename, 'duration_seconds': round(duration, 2)}
         except Exception as upload_err:
             end_time = time.time(); duration = end_time - start_time
             error_message = f"Erro upload Google Drive: {getattr(upload_err, 'message', str(upload_err))}"
             print(f"[TASK FAILED] {error_message} (Upload duration: {duration:.2f}s)"); print(f"--- TRACEBACK UPLOAD ---"); traceback.print_exc(); print(f"--- TRACEBACK UPLOAD FIM ---")
             result = {'success': False, 'error': error_message, 'duration_seconds': round(duration, 2)}
    # ... (Restante das verificações de temp_file_path e file_size igual) ...
    elif not temp_file_path: end_time = time.time(); duration = end_time - start_time; error_message = "Arquivo não baixado."; print(f"[TASK FAILED] {error_message}"); result = {'success': False, 'error': error_message, 'duration_seconds': round(duration, 2)}
    elif not os.path.exists(temp_file_path): end_time = time.time(); duration = end_time - start_time; error_message = f"Temp não encontrado ({temp_file_path})."; print(f"[TASK FAILED] {error_message}"); result = {'success': False, 'error': error_message, 'duration_seconds': round(duration, 2)}
    elif file_size == 0: end_time = time.time(); duration = end_time - start_time; error_message = f"Temp vazio ({temp_file_path})."; print(f"[TASK FAILED] {error_message}"); result = {'success': False, 'error': error_message, 'duration_seconds': round(duration, 2)}
    else: end_time = time.time(); duration = end_time - start_time; error_message = "Erro inesperado pré-upload."; print(f"[TASK FAILED] {error_message}"); result = {'success': False, 'error': error_message, 'duration_seconds': round(duration, 2)}

    # Limpeza final
    if temp_file_path and os.path.exists(temp_file_path):
        try: os.remove(temp_file_path); # print(f"[TASK LOG] Temp removido (final): {temp_file_path}") # Menos verboso
        except Exception as e_clean_final: print(f"[TASK WARNING] Erro remover temp (final) {temp_file_path}: {e_clean_final}")

    print(f"[TASK LOG] ===> Resultado final: {result}")
    return result