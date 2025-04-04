# -*- coding: utf-8 -*-
import os
import time
import json
import base64
import requests
import re # Importar re para limpeza de nome de arquivo
from datetime import datetime # Para timestamp nos nomes dos screenshots
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError # Para tratar erros da API do Drive
import mimetypes
import traceback

# --- FUNÇÃO HELPER PARA UPLOAD DE SCREENSHOT ---
def upload_debug_screenshot(page, filename_prefix, drive_service, base_folder_id, temp_dir):
    """Tira um screenshot, salva temporariamente e faz upload para pasta debug no Drive."""
    if not drive_service or not page:
        print("[TASK DEBUG] Screenshot não pode ser tirado (Drive Service ou Page indisponível).")
        return

    debug_folder_name = "printsdebug"
    debug_folder_id = None

    try:
        # 1. Tentar encontrar a pasta 'printsdebug' dentro da pasta base
        query = f"'{base_folder_id}' in parents and name='{debug_folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        response = drive_service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        folders = response.get('files', [])
        if folders:
            debug_folder_id = folders[0].get('id')
            print(f"[TASK DEBUG] Pasta de debug '{debug_folder_name}' encontrada com ID: {debug_folder_id}")
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

        # 3. Tirar e salvar screenshot localmente
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Limpar prefixo para nome de arquivo
        safe_prefix = re.sub(r'[\\/*?:"<>|]', "_", filename_prefix).strip()
        screenshot_filename = f"{safe_prefix}_{timestamp}.png"
        local_screenshot_path = os.path.join(temp_dir, screenshot_filename)

        print(f"[TASK DEBUG] Tirando screenshot: {local_screenshot_path}")
        page.screenshot(path=local_screenshot_path, full_page=True) # Tira da página inteira

        # 4. Fazer upload para o Google Drive na pasta de debug
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
                os.remove(local_screenshot_path)
                print(f"[TASK DEBUG] Screenshot local removido: {local_screenshot_path}")
        else:
             print(f"[TASK WARNING] Screenshot local não encontrado ou vazio: {local_screenshot_path}")

    except HttpError as drive_error:
        print(f"[TASK ERROR] Erro na API do Google Drive ao gerenciar pasta/upload de debug: {drive_error}")
    except Exception as e:
        print(f"[TASK ERROR] Erro inesperado na função upload_debug_screenshot: {e}")
        traceback.print_exc()


# --- Funções Auxiliares (get_drive_service_from_credentials, solve_captcha - Inalteradas) ---
# ... (código de get_drive_service_from_credentials e solve_captcha permanece igual ao da versão anterior) ...
def get_drive_service_from_credentials(credentials_base64_str):
    """Obtém o serviço autenticado do Google Drive a partir das credenciais."""
    SCOPES = ['https://www.googleapis.com/auth/drive'] # Escopo completo necessário para criar pastas/arquivos
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
    """Resolve o reCAPTCHA na página usando o serviço 2Captcha, se presente."""
    captcha_element = page.locator("iframe[src*='recaptcha']")
    if captcha_element.count() > 0 and captcha_api_key:
        print("[TASK DEBUG] CAPTCHA detectado! Tentando resolver...")
        site_key = page.evaluate('''() => { const d = document.querySelector('.g-recaptcha'); return d ? d.getAttribute('data-sitekey') : null; }''')
        if not site_key: raise Exception('Não foi possível encontrar site key do CAPTCHA.')
        response = requests.post("http://2captcha.com/in.php", data={ "key": captcha_api_key, "method": "userrecaptcha", "googlekey": site_key, "pageurl": url_login, "json": 1 }, timeout=20)
        response.raise_for_status()
        request_result = response.json()
        if request_result.get("status") != 1: raise Exception(f'Falha ao enviar CAPTCHA: {request_result.get("request")}')
        captcha_id = request_result["request"]
        print(f"[TASK DEBUG] CAPTCHA enviado, ID: {captcha_id}. Aguardando solução...")
        token = None
        for _ in range(60): # Tenta por até 3 minutos (60 * 3s)
            time.sleep(3)
            try:
                result_response = requests.get(f"http://2captcha.com/res.php?key={captcha_api_key}&action=get&id={captcha_id}&json=1", timeout=10)
                result_response.raise_for_status()
                result = result_response.json()
                if result.get("status") == 1: token = result["request"]; print("[TASK DEBUG] CAPTCHA resolvido!"); break
                elif result.get("request") == "CAPCHA_NOT_READY": continue
                else: raise Exception(f"Erro obter resultado CAPTCHA: {result.get('request')}")
            except requests.exceptions.RequestException as captcha_req_err: print(f"[TASK WARNING] Erro rede verificar CAPTCHA: {captcha_req_err}. Tentando novamente..."); time.sleep(5)
            except Exception as captcha_err: raise Exception(f"Erro inesperado verificar CAPTCHA: {captcha_err}")
        if not token: raise Exception('Timeout ou erro ao resolver CAPTCHA excedido.')
        page.evaluate(f"const ta = document.getElementById('g-recaptcha-response'); if (ta) ta.value = '{token}';")
        print("[TASK DEBUG] Token CAPTCHA inserido.")
        time.sleep(1)
        return True
    print("[TASK DEBUG] Nenhum CAPTCHA visível encontrado ou chave API não fornecida.")
    return False


# --- Função de Verificação de Login (Com mais prints) ---
# ... (código de check_login_status permanece igual ao da versão anterior) ...
def check_login_status(page):
    """Verifica status do login checando msg de sucesso ou ausência de botão 'Entrar'."""
    print("[TASK DEBUG] --- Iniciando verificação de status do login ---")
    print(f"[TASK DEBUG] URL atual para verificação: {page.url}")

    # 1. Procurar pela mensagem de sucesso explícita
    success_message_locator = page.locator("*:has-text('Login efetuado com sucesso!')")
    try:
        print("[TASK DEBUG] Tentando encontrar mensagem 'Login efetuado com sucesso!'...")
        # Usar first=True para tentar evitar erro de múltiplos elementos se possível
        success_message_locator.first.wait_for(state="visible", timeout=5000) # Timeout curto (5s)
        print("[TASK DEBUG] ====> MENSAGEM 'Login efetuado com sucesso!' ENCONTRADA. Login considerado OK.")
        print("[TASK DEBUG] --- Fim da verificação de status do login (Resultado: True) ---")
        return True
    except PlaywrightTimeoutError: # Usar o TimeoutError específico
        print("[TASK DEBUG] Mensagem 'Login efetuado com sucesso!' NÃO encontrada (timeout 5s).")
    except Exception as e:
        # Logar o erro de múltiplos elementos que vimos no log anterior
        print(f"[TASK WARNING] Erro ao buscar mensagem de sucesso (pode ser múltiplos elementos): {e}")
        # Continuar para a próxima verificação mesmo assim


    # 2. Verificar a AUSÊNCIA do botão/link "Entrar"
    print("[TASK DEBUG] Verificando AUSÊNCIA do botão/link 'Entrar' (ou similar)...")
    login_prompt_selector = "a:has-text('Entrar'), button:has-text('Entrar'), a:has-text('Login'), button:has-text('Login'), input[type='submit'][value*='Entrar' i], input[type='submit'][value*='Login' i]"
    login_prompt_locator = page.locator(login_prompt_selector).first
    try:
        print(f"[TASK DEBUG] Verificando se o elemento '{login_prompt_selector}' está OCULTO...")
        login_prompt_locator.wait_for(state="hidden", timeout=5000) # Espera ficar oculto
        print("[TASK DEBUG] ====> Botão/link 'Entrar' (ou similar) NÃO está visível (oculto). Login considerado OK.")
        print("[TASK DEBUG] --- Fim da verificação de status do login (Resultado: True) ---")
        return True
    except PlaywrightTimeoutError: # Usar o TimeoutError específico
        print("[TASK DEBUG] ====> Botão/link 'Entrar' (ou similar) AINDA ESTÁ VISÍVEL (timeout 5s ao esperar ocultar). Login considerado FALHO.")
        try:
            body_html = page.locator('body').inner_html(timeout=1000)
            print(f"[TASK DEBUG] HTML body (parcial) onde botão 'Entrar' pode estar visível:\n---\n{body_html[:1000]}...\n---")
        except Exception as html_err:
            print(f"[TASK DEBUG] Não foi possível capturar HTML para depuração: {html_err}")
        print("[TASK DEBUG] --- Fim da verificação de status do login (Resultado: False) ---")
        return False
    except Exception as e:
         print(f"[TASK DEBUG] Erro inesperado ao verificar ausência do botão 'Entrar': {e}")
         print("[TASK DEBUG] --- Fim da verificação de status do login (Resultado: False - por erro) ---")
         return False


# --- A Tarefa Principal do RQ (Integrada com Screenshots) ---
def perform_designi_download_task(
    designi_url,
    client_ip,
    folder_id, # ID da pasta base no Google Drive
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
    # Usar um diretório temporário consistente
    temp_dir = '/tmp/designi_downloads'
    os.makedirs(temp_dir, exist_ok=True)
    print(f"[TASK LOG] Usando diretório temp para downloads e screenshots: {temp_dir}")

    # Tenta obter o serviço do Drive (crítico para a tarefa e screenshots)
    try:
        print("[TASK LOG] Obtendo serviço Google Drive...")
        drive_service = get_drive_service_from_credentials(drive_credentials_base64)
        if not drive_service:
             raise Exception("Não foi possível obter serviço Google Drive. Verifique as credenciais.")
        print("[TASK LOG] Serviço Google Drive obtido.")
    except Exception as drive_init_err:
         print(f"[TASK CRITICAL] Erro crítico ao obter serviço Google Drive: {drive_init_err}")
         end_time = time.time(); duration = end_time - start_time
         error_message = f"Erro inicialização Drive: {getattr(drive_init_err, 'message', str(drive_init_err))}"
         # Não pode salvar screenshot se o drive_service falhou
         return {'success': False, 'error': error_message, 'duration_seconds': duration}

    # Configuração Playwright
    # ... (código de configuração Playwright permanece o mesmo) ...
    chrome_executable_path = "/ms-playwright/chromium-1161/chrome-linux/chrome"
    headless_shell_path = "/ms-playwright/chromium_headless_shell-1161/chrome-linux/headless_shell"
    launch_options = {'headless': True, 'args': ['--no-sandbox', '--disable-setuid-sandbox']}
    if os.path.exists(chrome_executable_path): launch_options['executable_path'] = chrome_executable_path; print(f"[TASK DEBUG] Usando executável: {chrome_executable_path}")
    elif os.path.exists(headless_shell_path): launch_options['executable_path'] = headless_shell_path; print(f"[TASK DEBUG] Usando executável: {headless_shell_path}")
    else:
        print(f"[TASK CRITICAL] Executável navegador NÃO encontrado.")
        end_time = time.time(); duration = end_time - start_time
        # Não pode salvar screenshot sem browser
        return {'success': False, 'error': "Executável navegador não encontrado.", 'duration_seconds': duration}


    login_bem_sucedido = False

    try:
        with sync_playwright() as p:
            print("[TASK LOG] Iniciando Chromium headless...")
            browser = p.chromium.launch(**launch_options)
            print("[TASK DEBUG] Navegador lançado.")
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
            print(f"[TASK DEBUG] Criando contexto com User-Agent: {user_agent}")
            context = browser.new_context(user_agent=user_agent)
            print("[TASK DEBUG] Contexto criado.")
            page = context.new_page()
            print("[TASK DEBUG] Página criada.")
            page.set_default_timeout(90000)
            print("[TASK DEBUG] Timeout padrão da página definido para 90000ms.")

            # === TENTATIVA DE LOGIN COM COOKIES ===
            if saved_cookies:
                # ... (lógica de login com cookies igual à anterior) ...
                 print("[TASK LOG] --- Tentando usar COOKIES salvos ---")
                 try:
                    print(f"[TASK DEBUG] Adicionando {len(saved_cookies)} cookies ao contexto.")
                    context.add_cookies(saved_cookies)
                    print(f"[TASK LOG] Cookies adicionados. Navegando para URL do arquivo para verificar: {designi_url}")
                    page.goto(designi_url, wait_until='domcontentloaded', timeout=90000)
                    print(f"[TASK LOG] Página ({page.url}) carregada (DOM). Verificando status do login com cookies...")
                    time.sleep(2)
                    if check_login_status(page):
                         print("[TASK LOG] ====> SUCESSO: Login com cookies VERIFICADO.")
                         login_bem_sucedido = True
                    else:
                         print("[TASK WARNING] FALHA: Login com cookies NÃO verificado. Tentará login manual.")
                         # Tira screenshot da falha do login por cookie
                         upload_debug_screenshot(page, "FalhaLoginCookie", drive_service, folder_id, temp_dir)
                 except Exception as cookie_err:
                    print(f"[TASK WARNING] Erro ao tentar usar cookies/navegar: {cookie_err}. Detalhes: {traceback.format_exc()}. Tentará login manual.")
                    login_bem_sucedido = False
                    # Tira screenshot do erro ao usar cookie
                    upload_debug_screenshot(page, "ErroLoginCookie", drive_service, folder_id, temp_dir)
                    try:
                        print("[TASK DEBUG] Limpando cookies do contexto após falha.")
                        context.clear_cookies()
                    except Exception as clear_err:
                        print(f"[TASK WARNING] Não foi possível limpar cookies: {clear_err}")

            else:
                print("[TASK LOG] Nenhum cookie salvo encontrado. Procedendo para login manual.")


            # === TENTATIVA DE LOGIN MANUAL (se necessário) ===
            if not login_bem_sucedido:
                # ... (lógica de login manual igual à anterior, MAS com screenshot no sucesso) ...
                print("[TASK LOG] --- Iniciando processo de LOGIN MANUAL ---")
                print(f"[TASK LOG] Acessando página de login: {url_login}")
                try:
                    page.goto(url_login, wait_until='domcontentloaded', timeout=90000)
                    print(f"[TASK LOG] Página de login ({page.url}) carregada (DOM).")
                    if not email or not senha: raise ValueError('Credenciais Designi (email/senha) não fornecidas.')

                    print("[TASK LOG] Preenchendo credenciais...")
                    page.locator("input[name=email]").fill(email, timeout=30000)
                    print("[TASK DEBUG] Email preenchido.")
                    page.locator("input[name=password]").fill(senha, timeout=30000)
                    print("[TASK DEBUG] Senha preenchida.")

                    captcha_resolvido = solve_captcha(page, captcha_api_key, url_login)
                    if captcha_resolvido: print("[TASK DEBUG] CAPTCHA foi resolvido (ou tentado resolver).")

                    print("[TASK LOG] Tentando clicar botão login...")
                    login_button_selector = 'button[type="submit"]:has-text("login"), button:has-text("Entrar"), input[type="submit"][value*="Login" i], input[type="submit"][value*="Entrar" i]'
                    login_button_locator = page.locator(login_button_selector).first
                    login_button_locator.wait_for(state="visible", timeout=30000)
                    print("[TASK DEBUG] Botão de login visível. Clicando...")
                    login_button_locator.click()
                    print("[TASK DEBUG] Botão de login clicado.")

                    print("[TASK LOG] Aguardando navegação pós-login ou indicador...")
                    try:
                        page.wait_for_url(lambda url: url != url_login and "/login" not in url, timeout=60000)
                        print(f"[TASK LOG] Navegou para fora da página de login. URL atual: {page.url}")
                    except PlaywrightTimeoutError:
                         print(f"[TASK WARNING] Timeout (60s) ao esperar mudança de URL após login. URL atual: {page.url}. Verificando status mesmo assim...")
                    except Exception as nav_err:
                         print(f"[TASK ERROR] Erro inesperado ao esperar navegação pós-login: {nav_err}")

                    print("[TASK LOG] Verificando status do login APÓS tentativa manual...")
                    time.sleep(3)
                    if check_login_status(page):
                        print("[TASK LOG] ====> SUCESSO: Login manual VERIFICADO.")
                        login_bem_sucedido = True
                        # TIRA SCREENSHOT LOGO APÓS LOGIN MANUAL BEM SUCEDIDO
                        upload_debug_screenshot(page, "AposLoginManualOK", drive_service, folder_id, temp_dir)
                        try:
                            cookies = context.cookies()
                            print(f"[TASK DEBUG] Tentando salvar {len(cookies)} cookies após login manual bem-sucedido.")
                            from app import save_designi_cookies
                            save_designi_cookies(cookies, client_ip)
                            print("[TASK LOG] Cookies salvos na sessão global após login manual.")
                        except ImportError: print("[TASK WARNING] Não importou 'save_designi_cookies' de 'app'. Não foi possível salvar cookies.")
                        except Exception as save_err: print(f"[TASK WARNING] Erro ao salvar cookies: {save_err}")
                    else:
                        print("[TASK ERROR] FALHA: Login manual REALIZADO, mas verificação posterior FALHOU.")
                        # Tira screenshot da falha da verificação pós-login
                        upload_debug_screenshot(page, "FalhaVerificacaoAposLogin", drive_service, folder_id, temp_dir)
                        try:
                            failed_login_html = page.content()
                            print(f"[TASK DEBUG] CONTEÚDO DA PÁGINA APÓS FALHA NA VERIFICAÇÃO DE LOGIN MANUAL:\n---\n{failed_login_html[:2000]}...\n---")
                        except Exception as content_err:
                            print(f"[TASK DEBUG] Não foi possível obter conteúdo da página após falha: {content_err}")
                        raise Exception("Login manual realizado, mas verificação pós-login falhou.")

                except Exception as login_manual_err:
                     print(f"[TASK ERROR] Erro durante processo de login manual: {login_manual_err}")
                     # Tira screenshot do erro no login manual
                     upload_debug_screenshot(page, "ErroLoginManual", drive_service, folder_id, temp_dir)
                     print(traceback.format_exc())
                     raise login_manual_err


            # === PROCEDER PARA DOWNLOAD (APENAS SE LOGIN FOI BEM SUCEDIDO) ===
            if not login_bem_sucedido:
                print("[TASK ERROR] Estado de login inconsistente.")
                raise Exception("Erro crítico: Tentando prosseguir sem login bem-sucedido.")

            print("[TASK LOG] --- Iniciando processo de DOWNLOAD ---")
            # Garante que estamos na página correta ANTES de buscar o botão
            if designi_url not in page.url:
                 print(f"[TASK LOG] URL atual ({page.url}) não é a URL do arquivo ({designi_url}). Navegando...")
                 page.goto(designi_url, wait_until='domcontentloaded', timeout=90000)
                 print(f"[TASK LOG] Página final do arquivo carregada ({page.url}).")
                 # TIRA SCREENSHOT APÓS NAVEGAR PARA PÁGINA DO ARQUIVO
                 upload_debug_screenshot(page, "PaginaArquivoCarregada", drive_service, folder_id, temp_dir)
            else:
                 print(f"[TASK LOG] Já está na URL correta do arquivo ({page.url}).")
                 # TIRA SCREENSHOT MESMO SE JÁ ESTAVA NA PÁGINA
                 upload_debug_screenshot(page, "PaginaArquivoJaEstava", drive_service, folder_id, temp_dir)

            # Procurar botão de Download
            download_button_selector = "#downButton" # <<-- ESTE PODE SER O PROBLEMA
            print(f"[TASK LOG] Procurando botão download com seletor: '{download_button_selector}'...")
            download_button = page.locator(download_button_selector)
            try:
                # TIRA SCREENSHOT ANTES DE ESPERAR PELO BOTÃO
                print("[TASK DEBUG] Tirando screenshot ANTES de esperar pelo botão de download...")
                upload_debug_screenshot(page, "AntesEsperarBotaoDown", drive_service, folder_id, temp_dir)

                print("[TASK DEBUG] Esperando botão download ficar visível e habilitado...")
                download_button.wait_for(state="visible", timeout=180000) # 3 minutos para o botão aparecer
                download_button.wait_for(state="enabled", timeout=30000) # 30s extras para habilitar
                print(f"[TASK LOG] Botão '{download_button_selector}' encontrado, visível e habilitado!")
            except Exception as btn_err:
                print(f"[TASK ERROR] Timeout ou erro ao esperar por '{download_button_selector}': {btn_err}")
                # TIRA SCREENSHOT NO MOMENTO DA FALHA AO ENCONTRAR O BOTÃO
                print("[TASK DEBUG] Tirando screenshot NO MOMENTO DA FALHA ao encontrar/esperar botão de download...")
                upload_debug_screenshot(page, "FalhaEncontrarBotaoDown", drive_service, folder_id, temp_dir)
                # Log HTML (já existia)
                try:
                    button_area_html = page.locator('body').inner_html(timeout=1000)
                    print(f"[TASK DEBUG] HTML (parcial) onde botão de download deveria estar:\n---\n{button_area_html[:1000]}...\n---")
                except Exception as html_err:
                    print(f"[TASK DEBUG] Não foi possível capturar HTML da área do botão: {html_err}")
                raise Exception(f"Botão de download ('{download_button_selector}') não encontrado/visível/habilitado após timeout.")


            # --- Lógica de Clique, Popup e Download --- (igual anterior)
            print("[TASK LOG] Configurando espera pelo evento 'download' (timeout 300s)...")
            popup_detected = None
            def handle_popup(popup):
                nonlocal popup_detected
                print(f"[TASK DEBUG] !! POPUP DETECTADO !! URL: {popup.url}")
                popup_detected = popup

            page.on("popup", handle_popup)
            print("[TASK DEBUG] Listener para 'popup' adicionado.")

            try:
                with page.expect_download(timeout=300000) as download_info:
                    print(f"[TASK LOG] ---> CLICANDO no botão '{download_button_selector}' <---")
                    download_button.click()
                    print("[TASK LOG] Clique realizado. Aguardando início do evento 'download' OU popup...")

                download = download_info.value
                print(f"[TASK LOG] Evento 'download' recebido! Nome sugerido: {download.suggested_filename}")
                page.remove_listener("popup", handle_popup)
                print("[TASK DEBUG] Listener 'popup' removido.")
                if popup_detected:
                     print(f"[TASK WARNING] Um popup foi detectado durante o processo (URL: {popup_detected.url}). O download principal ainda foi recebido.")

            except PlaywrightTimeoutError as timeout_err:
                 print(f"[TASK ERROR] !!! Timeout (300s) EXCEDIDO enquanto esperava pelo evento 'download' após o clique !!!")
                 print(f"[TASK ERROR] Detalhes do Timeout: {timeout_err}")
                 # Tira screenshot no timeout do download
                 upload_debug_screenshot(page, "TimeoutEsperandoDownloadEvent", drive_service, folder_id, temp_dir)
                 try:
                     timeout_html = page.content()
                     print(f"[TASK DEBUG] CONTEÚDO DA PÁGINA NO MOMENTO DO TIMEOUT DE DOWNLOAD:\n---\n{timeout_html[:2000]}...\n---")
                     if popup_detected:
                          print(f"[TASK DEBUG] Um popup foi detectado ANTES do timeout (URL: {popup_detected.url}). Pode ter interferido?")
                          # Tentar tirar screenshot do popup também, se possível
                          try: upload_debug_screenshot(popup_detected, "PopupDetectadoAntesTimeoutDown", drive_service, folder_id, temp_dir)
                          except: print("[TASK DEBUG] Não foi possível tirar screenshot do popup.")
                 except Exception as content_err:
                     print(f"[TASK DEBUG] Não foi possível obter conteúdo da página/popup no momento do timeout: {content_err}")
                 raise timeout_err
            except Exception as click_download_err:
                 print(f"[TASK ERROR] Erro inesperado durante o clique ou espera do download: {click_download_err}")
                 # Tira screenshot no erro do clique/espera
                 upload_debug_screenshot(page, "ErroClickOuEsperaDownload", drive_service, folder_id, temp_dir)
                 print(traceback.format_exc())
                 raise click_download_err

            # --- Processa e salva o arquivo baixado --- (igual anterior)
            if not download:
                 raise Exception("Falha inesperada: Evento download registrado, mas objeto não acessível.")

            suggested_filename = download.suggested_filename
            if not suggested_filename:
                parsed_url = urlparse(download.url)
                _, ext = os.path.splitext(parsed_url.path)
                suggested_filename = f"designi_download_{int(time.time())}{ext if ext else '.file'}"
                print(f"[TASK WARNING] Download não sugeriu nome. Usando nome gerado: {suggested_filename}")
            else:
                 suggested_filename = re.sub(r'[\\/*?:"<>|]', "_", suggested_filename).strip()
                 print(f"[TASK DEBUG] Nome de arquivo sugerido limpo: {suggested_filename}")

            temp_file_path = os.path.join(temp_dir, suggested_filename)
            print(f"[TASK LOG] Download recebido. Salvando em: {temp_file_path}")
            try:
                download.save_as(temp_file_path)
                print(f"[TASK LOG] Download salvo com sucesso: {temp_file_path}")
            except Exception as save_err:
                print(f"[TASK ERROR] Falha ao salvar o download em {temp_file_path}: {save_err}")
                failure_reason = download.failure()
                print(f"[TASK DEBUG] Razão da falha (se disponível): {failure_reason}")
                raise Exception(f"Falha ao salvar download em disco: {save_err}. Razão: {failure_reason}")

            if not os.path.exists(temp_file_path):
                 raise Exception(f"Erro crítico: Arquivo {temp_file_path} não encontrado após save_as ser chamado.")
            elif os.path.getsize(temp_file_path) == 0:
                 print(f"[TASK WARNING] Arquivo salvo {temp_file_path} está VAZIO (0 bytes).")
                 failure_reason = download.failure()
                 if failure_reason:
                      print(f"[TASK DEBUG] Razão da falha reportada pelo download: {failure_reason}")
                      raise Exception(f"Arquivo salvo vazio. Razão da falha reportada: {failure_reason}")
                 else:
                      raise Exception(f"Arquivo salvo {temp_file_path} está vazio (0 bytes). Download pode ter sido incompleto ou inválido.")

            file_size = os.path.getsize(temp_file_path)
            print(f"[TASK LOG] Arquivo salvo e verificado. Tamanho: {file_size} bytes")


    # --- Fim do Bloco 'with sync_playwright()' ---
    except Exception as e:
        end_time = time.time(); duration = end_time - start_time
        error_message = f"Erro durante automação ou setup: {getattr(e, 'message', str(e))}"
        print(f"[TASK FAILED] {error_message} (Duração até falha: {duration:.2f}s)")
        print(f"--- TRACEBACK INÍCIO ---"); traceback.print_exc(); print(f"--- TRACEBACK FIM ---")
        # Tenta tirar um último screenshot se a página ainda existir
        if page:
             upload_debug_screenshot(page, "ErroGeralNaAutomacao", drive_service, folder_id, temp_dir)

        if temp_file_path and os.path.exists(temp_file_path):
            try: os.remove(temp_file_path); print(f"[TASK DEBUG] Temp removido após erro: {temp_file_path}")
            except Exception as e_clean: print(f"[TASK WARNING] Erro ao remover temp {temp_file_path} após erro: {e_clean}")
        if browser and browser.is_connected():
            try: print("[TASK DEBUG] Tentando fechar navegador em bloco de exceção geral..."); browser.close(); print("[TASK DEBUG] Navegador fechado (exceção geral).")
            except Exception as close_err: print(f"[TASK WARNING] Erro ao fechar navegador (exceção geral): {close_err}")
        return {'success': False, 'error': error_message, 'duration_seconds': duration}

    finally:
        print("[TASK DEBUG] Bloco Finally principal alcançado.")
        if browser and browser.is_connected():
            try:
                print("[TASK LOG] Fechando navegador Playwright (bloco finally)...")
                browser.close()
                print("[TASK LOG] Navegador fechado (bloco finally).")
            except Exception as close_final_err:
                 print(f"[TASK WARNING] Erro ao fechar navegador no bloco finally principal: {close_final_err}")
        else:
            print("[TASK DEBUG] Navegador não estava aberto ou conectado no bloco finally.")

    # --- Upload Google Drive (Arquivo Principal) ---
    # ... (lógica de upload do arquivo principal permanece a mesma) ...
    print("[TASK LOG] --- Iniciando UPLOAD para Google Drive ---")
    if temp_file_path and os.path.exists(temp_file_path) and file_size > 0:
         print(f"[TASK LOG] Preparando upload do arquivo: {temp_file_path}")
         filename = os.path.basename(temp_file_path)
         file_metadata = {'name': filename}
         if folder_id: file_metadata['parents'] = [folder_id]
         mimetype, _ = mimetypes.guess_type(temp_file_path)
         mimetype = mimetype or 'application/octet-stream'
         media = MediaFileUpload(temp_file_path, mimetype=mimetype, resumable=True)
         print(f"[TASK LOG] Enviando '{filename}' ({mimetype}, {file_size} bytes) para Google Drive...")
         file = None
         try:
             file = drive_service.files().create(
                 body=file_metadata,
                 media_body=media,
                 fields='id, webViewLink'
             ).execute()
             file_id = file.get('id')
             web_view_link = file.get('webViewLink')
             if not file_id:
                 print("[TASK ERROR] Upload Google Drive retornou SEM ID.")
                 raise Exception("Upload Google Drive falhou (sem ID retornado).")
             print(f"[TASK LOG] Upload Drive concluído. ID: {file_id}")

             try:
                 print("[TASK LOG] Definindo permissão pública...")
                 drive_service.permissions().create(fileId=file_id, body={'role': 'reader', 'type': 'anyone'}).execute()
                 print("[TASK LOG] Permissão pública definida.")
             except Exception as perm_err:
                 print(f"[TASK WARNING] Falha ao definir permissão pública para {file_id}: {perm_err}. O arquivo pode não ser acessível publicamente.")

             end_time = time.time(); duration = end_time - start_time
             print(f"[TASK SUCCESS] Tarefa concluída com sucesso em {duration:.2f} segundos.")
             result = {
                 'success': True,
                 'file_id': file_id,
                 'download_link': web_view_link,
                 'filename': filename,
                 'duration_seconds': round(duration, 2)
                 }

         except Exception as upload_err:
             end_time = time.time(); duration = end_time - start_time
             error_message = f"Erro durante upload Google Drive: {getattr(upload_err, 'message', str(upload_err))}"
             print(f"[TASK FAILED] {error_message} (Duração até falha upload: {duration:.2f}s)")
             print(f"--- TRACEBACK UPLOAD INÍCIO ---"); traceback.print_exc(); print(f"--- TRACEBACK UPLOAD FIM ---")
             result = {'success': False, 'error': error_message, 'duration_seconds': round(duration, 2)}

    elif not temp_file_path:
         end_time = time.time(); duration = end_time - start_time
         error_message = "Nenhum arquivo foi baixado (caminho temporário não definido)."
         print(f"[TASK FAILED] {error_message}")
         result = {'success': False, 'error': error_message, 'duration_seconds': round(duration, 2)}
    elif not os.path.exists(temp_file_path):
        end_time = time.time(); duration = end_time - start_time
        error_message = f"Arquivo temporário não encontrado ({temp_file_path}) antes do upload."
        print(f"[TASK FAILED] {error_message}")
        result = {'success': False, 'error': error_message, 'duration_seconds': round(duration, 2)}
    elif file_size == 0:
        end_time = time.time(); duration = end_time - start_time
        error_message = f"Arquivo temporário ({temp_file_path}) está vazio, upload cancelado."
        print(f"[TASK FAILED] {error_message}")
        result = {'success': False, 'error': error_message, 'duration_seconds': round(duration, 2)}
    else:
         end_time = time.time(); duration = end_time - start_time
         error_message = "Condição inesperada antes do upload."
         print(f"[TASK FAILED] {error_message}")
         result = {'success': False, 'error': error_message, 'duration_seconds': round(duration, 2)}

    # Limpeza final do arquivo temporário principal
    if temp_file_path and os.path.exists(temp_file_path):
        try:
            os.remove(temp_file_path)
            print(f"[TASK LOG] Temp principal removido (final): {temp_file_path}")
        except Exception as e_clean_final:
            print(f"[TASK WARNING] Erro remover temp principal (final) {temp_file_path}: {e_clean_final}")

    print(f"[TASK LOG] ===> Resultado final da tarefa: {result}")
    return result