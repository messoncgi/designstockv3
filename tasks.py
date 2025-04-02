# -*- coding: utf-8 -*-
import os
import time
import json
import base64
import requests
# Removido import re pois não usaremos regex no seletor
from playwright.sync_api import sync_playwright
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import mimetypes

# --- Funções Auxiliares (Inalteradas) ---
def get_drive_service_from_credentials(credentials_base64_str):
    # (código igual)
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
    # (código igual)
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
    temp_file_path = None
    browser = None
    context = None
    start_time = time.time()
    temp_dir = '/tmp/designi_downloads'
    os.makedirs(temp_dir, exist_ok=True)
    print(f"[TASK LOG] Usando diretório temp: {temp_dir}")

    # Usar caminho v1161 (mantido)
    chrome_executable_path = "/ms-playwright/chromium-1161/chrome-linux/chrome"
    headless_shell_path = "/ms-playwright/chromium_headless_shell-1161/chrome-linux/headless_shell"
    launch_options = {'headless': True, 'args': ['--no-sandbox', '--disable-setuid-sandbox']}
    if os.path.exists(chrome_executable_path):
        print(f"[TASK INFO] Usando caminho explícito: {chrome_executable_path}")
        launch_options['executable_path'] = chrome_executable_path
    elif os.path.exists(headless_shell_path):
        print(f"[TASK WARNING] Usando fallback: {headless_shell_path}")
        launch_options['executable_path'] = headless_shell_path
    else:
        print(f"[TASK ERROR] CRÍTICO: Executável navegador NÃO encontrado.")
        return {'success': False, 'error': f"Executável navegador não encontrado.", 'duration_seconds': time.time() - start_time }

    try:
        with sync_playwright() as p:
            try:
                print("[TASK LOG] Iniciando Chromium headless...")
                browser = p.chromium.launch(**launch_options)
                context = browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
                page = context.new_page()
                page.set_default_timeout(90000) # Timeout geral alto

                # --- Lógica de Login e Navegação (Modificada para usar cookies salvos) ---
                login_necessario = True

                # Verificar se temos cookies salvos
                if saved_cookies:
                    try:
                        print("[TASK LOG] Tentando usar cookies salvos...")
                        # Adicionar cookies salvos ao contexto do navegador
                        context.add_cookies(saved_cookies) # Corrigido para adicionar a lista inteira

                        # Tentar acessar diretamente a URL do arquivo
                        print(f"[TASK LOG] Navegando diretamente para URL do arquivo: {designi_url}")
                        page.goto(designi_url, wait_until='networkidle')

                        # Verificar se estamos logados verificando se não fomos redirecionados para login
                        if "/login" not in page.url:
                            print("[TASK LOG] Cookies válidos! Login automático bem-sucedido.")
                            login_necessario = False
                        else:
                            print("[TASK LOG] Cookies expirados ou inválidos. Realizando login manual.")
                    except Exception as cookie_err:
                        print(f"[TASK WARNING] Erro ao usar cookies salvos: {cookie_err}. Realizando login manual.")
                else:
                    print("[TASK LOG] Nenhum cookie salvo encontrado. Realizando login manual.")

                # Se não conseguimos usar cookies, fazer login normal
                if login_necessario:
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
                            # Tenta tirar screenshot se falhar o login
                            try:
                                page.screenshot(path=os.path.join(temp_dir, 'login_fail_screenshot.png'))
                                print("[TASK DEBUG] Screenshot de falha de login salvo.")
                            except Exception as ss_login_err:
                                print(f"[TASK WARNING] Não foi possível tirar screenshot de falha de login: {ss_login_err}")
                            raise Exception(f"Falha login (ainda em /login): {nav_err}")
                        else: print(f"[TASK WARNING] Espera pós-login falhou, mas URL mudou: {page.url}")
                    print(f"[TASK LOG] Login OK! URL: {page.url}")

                    # Salvar cookies após login bem-sucedido
                    try:
                        cookies = context.cookies()
                        # Enviar cookies para o app.py salvar no Redis
                        # Importamos aqui para evitar dependência circular e garantir disponibilidade da função
                        from app import save_designi_cookies
                        save_designi_cookies(cookies, client_ip) # Passa o IP se quiser salvar por usuário, ou None/remova para global
                        print("[TASK LOG] Cookies salvos (ou tentativa de salvar) após login.")
                    except ImportError:
                         print("[TASK WARNING] Não foi possível importar save_designi_cookies do app.py. Cookies não serão salvos.")
                    except Exception as save_err:
                        print(f"[TASK WARNING] Erro ao salvar cookies: {save_err}")

                    # Navegar para a URL do arquivo
                    print(f"[TASK LOG] Navegando para URL do arquivo: {designi_url}")
                    page.goto(designi_url, wait_until='networkidle') # Espera carregar bem

                print(f"[TASK LOG] Página arquivo carregada. URL: {page.url}")

                # --- CORREÇÃO: Voltar a usar o seletor #downButton ---
                print("[TASK LOG] Aguardando botão download (usando #downButton)...")
                download_button_selector = "#downButton" # <<-- SELETOR SIMPLIFICADO

                download_button = page.locator(download_button_selector) # Não precisa de .first com ID

                # =====================================================
                # === INÍCIO DAS MODIFICAÇÕES PARA DEBUG E TIMEOUT ===
                # =====================================================
                try:
                    # PASSO 1: Adicionar Screenshot ANTES de esperar
                    screenshot_path_before = os.path.join(temp_dir, f'before_wait_downbutton_{int(time.time())}.png')
                    print(f"[TASK DEBUG] Tirando screenshot antes de esperar pelo botão: {screenshot_path_before}")
                    page.screenshot(path=screenshot_path_before)
                    print(f"[TASK DEBUG] Screenshot salvo. Esperando por {download_button_selector} ficar visível...")

                    # PASSO 2: Aumentar o Timeout
                    download_button.wait_for(state="visible", timeout=180000) # Aumentado para 180s (3 minutos)

                    print("[TASK LOG] Botão #downButton visível encontrado!")

                except Exception as btn_err:
                    # Se ainda falhar, tirar outro screenshot pode ajudar
                    screenshot_path_after_fail = os.path.join(temp_dir, f'after_fail_wait_downbutton_{int(time.time())}.png')
                    print(f"[TASK ERROR] Timeout ou erro ao esperar por #downButton: {btn_err}")
                    try:
                        page.screenshot(path=screenshot_path_after_fail)
                        print(f"[TASK DEBUG] Screenshot após falha ao esperar pelo botão salvo: {screenshot_path_after_fail}")
                    except Exception as ss_fail_err:
                         print(f"[TASK WARNING] Não foi possível tirar screenshot após falha ao esperar: {ss_fail_err}")
                    # Fornecer um erro mais específico
                    raise Exception(f"Botão de download com id='downButton' não encontrado ou visível após 180s.")
                # =====================================================
                # === FIM DAS MODIFICAÇÕES PARA DEBUG E TIMEOUT =====
                # =====================================================

                # --- Lógica de Clique, Popup e Download (Inalterada) ---
                print("[TASK LOG] Configurando espera download...")
                # Usar um timeout maior para o download em si também pode ser útil
                with page.expect_download(timeout=300000) as download_info: # Timeout download aumentado para 5 min
                    print("[TASK LOG] Clicando botão #downButton...")
                    download_button.click() # Clica no botão encontrado
                    print("[TASK LOG] Clique realizado, aguardando popup/download...")

                    # Espera um pouco para o popup aparecer (se houver)
                    time.sleep(3) # Pequena pausa pode ajudar

                    # Verifica e fecha o popup de agradecimento (lógica existente mantida)
                    thank_you_popup = page.locator("div.modal-content:has-text('Obrigado por baixar meu arquivo!')")
                    if thank_you_popup.count() > 0:
                        print("[TASK LOG] Popup agradecimento detectado")
                        # Tenta encontrar um botão de fechar mais genérico dentro do popup
                        close_button = thank_you_popup.locator("button[aria-label='Close'], button:has-text('Fechar'), button.close, [data-bs-dismiss='modal']").first
                        try:
                            if close_button.is_visible(timeout=5000): # Espera rápida pelo botão de fechar
                                 print("[TASK LOG] Tentando fechar popup...")
                                 close_button.click()
                                 print("[TASK LOG] Popup fechado (ou tentativa).")
                                 time.sleep(1) # Pausa após fechar
                            else: print("[TASK LOG] Botão fechar popup não visível.")
                        except Exception as close_err:
                            print(f"[TASK WARNING] Erro ao tentar fechar popup: {close_err}")
                    else: print("[TASK LOG] Nenhum popup agradecimento detectado.")

                    print("[TASK LOG] Aguardando download começar...")
                    download = download_info.value # Pega o objeto de download

                if not download: raise Exception("Evento download não ocorreu ou timeout excedido (300s).")

                suggested_filename = download.suggested_filename or f"designi_download_{int(time.time())}.file"
                # Limpar nome do arquivo (opcional, mas bom)
                suggested_filename = "".join(c for c in suggested_filename if c.isalnum() or c in ('.', '_', '-')).rstrip()
                temp_file_path = os.path.join(temp_dir, suggested_filename)

                print(f"[TASK LOG] Download iniciado: {suggested_filename}. Salvando em: {temp_file_path}")
                download.save_as(temp_file_path)
                print(f"[TASK LOG] Download concluído: {temp_file_path}")

                if not os.path.exists(temp_file_path) or os.path.getsize(temp_file_path) == 0:
                     # Tenta obter o erro do download se falhou
                     failure_reason = await download.failure() # Use await se estiver em contexto async, senão remova await
                     raise Exception(f"Falha salvar download ou arquivo vazio: {temp_file_path}. Razão: {failure_reason}")

                file_size = os.path.getsize(temp_file_path)
                print(f"[TASK LOG] Arquivo salvo. Tamanho: {file_size} bytes")


            except Exception as pw_error:
                print(f"[TASK ERROR] Erro durante automação Playwright: {pw_error}")
                # Tenta tirar screenshot em qualquer erro Playwright
                if 'page' in locals() and page and not page.is_closed():
                    screenshot_path_generic_error = os.path.join(temp_dir, f'playwright_error_{int(time.time())}.png')
                    try:
                        page.screenshot(path=screenshot_path_generic_error)
                        print(f"[TASK DEBUG] Screenshot de erro Playwright salvo: {screenshot_path_generic_error}")
                    except Exception as ss_err: print(f"[TASK WARNING] Não foi possível tirar screenshot erro Playwright: {ss_err}")
                raise # Re-levanta a exceção para que o RQ a capture como falha

            finally:
                if browser: print("[TASK LOG] Fechando navegador Playwright..."); browser.close(); print("[TASK LOG] Navegador fechado.")

        # --- Upload Google Drive (Inalterado, exceto por log adicionado) ---
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
             print(f"[TASK LOG] Enviando '{filename}' ({mimetype}, {os.path.getsize(temp_file_path)} bytes) para Google Drive...")

             # Tenta o upload
             try:
                 file = drive_service.files().create(
                     body=file_metadata,
                     media_body=media,
                     fields='id, webViewLink'
                 ).execute()
                 print(f"[TASK LOG] Upload Drive concluído. ID: {file.get('id')}")
             except Exception as upload_err:
                 print(f"[TASK ERROR] Erro durante upload Google Drive: {upload_err}")
                 raise Exception(f"Erro upload Google Drive: {upload_err}") # Re-levanta para falhar a tarefa

             # Tenta definir permissão pública
             try:
                 print("[TASK LOG] Definindo permissão pública...")
                 drive_service.permissions().create(
                     fileId=file.get('id'),
                     body={'role': 'reader', 'type': 'anyone'}
                 ).execute()
                 print("[TASK LOG] Permissão pública definida.")
             except Exception as perm_err:
                 # Não falha a tarefa inteira se só a permissão der erro, mas avisa
                 print(f"[TASK WARNING] Falha ao definir permissão pública para {file.get('id')}: {perm_err}")


             end_time = time.time(); duration = end_time - start_time
             print(f"[TASK SUCCESS] Tarefa concluída com sucesso em {duration:.2f} segundos.")
             return {'success': True, 'file_id': file.get('id'), 'download_link': file.get('webViewLink'), 'filename': filename, 'duration_seconds': duration}
        elif not temp_file_path:
             raise Exception("Nenhum arquivo foi baixado (temp_file_path não definido).")
        else: # temp_file_path existe mas o arquivo não?
             raise Exception(f"Arquivo temporário não encontrado ou inválido pós-download: {temp_file_path}")

    except Exception as e:
        end_time = time.time(); duration = end_time - start_time
        # Tenta obter a mensagem de erro mais específica possível
        error_message = f"Erro na tarefa download: {getattr(e, 'message', str(e))}"
        print(f"[TASK FAILED] {error_message} (Duração: {duration:.2f}s)")
        # Adiciona traceback ao log se possível
        import traceback
        print(traceback.format_exc())
        return {'success': False, 'error': error_message, 'duration_seconds': duration}

    finally:
        # Limpeza do arquivo temporário
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                print(f"[TASK LOG] Temp removido: {temp_file_path}")
            except Exception as e_clean:
                print(f"[TASK WARNING] Erro remover temp {temp_file_path}: {e_clean}")