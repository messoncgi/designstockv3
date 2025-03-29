import os
import time
import json
import base64
import requests
from playwright.sync_api import sync_playwright
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- Funções Auxiliares (Movidas ou Adaptadas de app.py) ---

def get_drive_service_from_credentials(credentials_base64_str):
    """Cria o serviço do Drive a partir das credenciais em Base64."""
    SCOPES = ['https://www.googleapis.com/auth/drive']
    try:
        if not credentials_base64_str:
            print("[TASK ERROR] Variável GOOGLE_CREDENTIALS_BASE64 não configurada.")
            return None

        try:
            json_str = base64.b64decode(credentials_base64_str).decode('utf-8')
            service_account_info = json.loads(json_str)
        except Exception as e:
            print(f"[TASK ERROR] Erro ao decodificar/parsear credenciais do Google: {str(e)}")
            return None

        required_fields = ['client_email', 'private_key', 'project_id']
        for field in required_fields:
            if field not in service_account_info:
                print(f"[TASK ERROR] Campo obrigatório '{field}' não encontrado nas credenciais.")
                return None

        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=SCOPES
        )
        return build('drive', 'v3', credentials=credentials)

    except Exception as e:
        print(f"[TASK ERROR] Erro ao criar serviço do Google Drive: {str(e)}")
        return None

def solve_captcha(page, captcha_api_key, url_login):
    """Resolve o reCAPTCHA se presente."""
    captcha_element = page.locator("iframe[src*='recaptcha']")
    if captcha_element.count() > 0 and captcha_api_key:
        print("[TASK LOG] CAPTCHA detectado! Tentando resolver...")
        site_key = page.evaluate('''() => {
            const recaptchaDiv = document.querySelector('.g-recaptcha');
            return recaptchaDiv ? recaptchaDiv.getAttribute('data-sitekey') : null;
        }''')

        if not site_key:
            raise Exception('Não foi possível encontrar a site key do CAPTCHA.')

        response = requests.post("http://2captcha.com/in.php", data={
            "key": captcha_api_key, "method": "userrecaptcha",
            "googlekey": site_key, "pageurl": url_login, "json": 1
        }, timeout=20) # Adiciona timeout
        response.raise_for_status() # Verifica erros HTTP
        request_result = response.json()

        if request_result.get("status") != 1:
            raise Exception(f'Falha ao enviar CAPTCHA para resolução: {request_result.get("request")}')

        captcha_id = request_result["request"]
        print(f"[TASK LOG] CAPTCHA enviado, ID: {captcha_id}. Aguardando solução...")

        token = None
        # Aumentar um pouco a espera total, mas verificar mais rápido
        for _ in range(60):  # Espera até 180 segundos (60 * 3)
            time.sleep(3)
            try:
                result_response = requests.get(
                    f"http://2captcha.com/res.php?key={captcha_api_key}&action=get&id={captcha_id}&json=1",
                    timeout=10 # Adiciona timeout
                )
                result_response.raise_for_status()
                result = result_response.json()

                if result.get("status") == 1:
                    token = result["request"]
                    print("[TASK LOG] CAPTCHA resolvido!")
                    break
                elif result.get("request") == "CAPCHA_NOT_READY":
                    continue # Ainda não está pronto, continua esperando
                else:
                    # Algum outro erro retornado pela API
                    raise Exception(f"Erro ao obter resultado do CAPTCHA: {result.get('request')}")

            except requests.exceptions.RequestException as captcha_req_err:
                 print(f"[TASK WARNING] Erro de rede ao verificar CAPTCHA: {captcha_req_err}. Tentando novamente...")
                 time.sleep(5) # Espera extra em caso de erro de rede
            except Exception as captcha_err:
                raise Exception(f"Erro inesperado ao verificar CAPTCHA: {captcha_err}")


        if not token:
            raise Exception('Tempo limite ou erro ao resolver CAPTCHA excedido.')

        # Inserir o token do CAPTCHA usando JavaScript seguro
        page.evaluate(f"""() => {{
            const textarea = document.getElementById('g-recaptcha-response');
            if (textarea) {{
                textarea.value = '{token}';
                return true;
            }}
            return false;
        }}""")
        print("[TASK LOG] Token do CAPTCHA inserido.")
        time.sleep(1) # Pequena pausa após inserir o token
        return True
    return False # Sem captcha ou sem API Key

# --- A Tarefa Principal do RQ ---

def perform_designi_download_task(
    designi_url,
    client_ip, # Pode ser útil para logging ou lógica futura
    folder_id,
    email,
    senha,
    captcha_api_key,
    drive_credentials_base64,
    url_login='https://designi.com.br/login' # Valor padrão
    ):
    """
    Tarefa executada pelo worker RQ para baixar do Designi e subir no Drive.
    Retorna um dicionário com o resultado ou erro.
    """
    print(f"[TASK LOG] Iniciando tarefa de download para URL: {designi_url} (IP: {client_ip})")
    temp_file_path = None
    browser = None
    context = None
    start_time = time.time()

    # Diretório temporário seguro dentro do ambiente do worker
    # Usar /tmp que geralmente está disponível e é limpo em reinícios
    temp_dir = '/tmp/designi_downloads'
    os.makedirs(temp_dir, exist_ok=True)
    print(f"[TASK LOG] Usando diretório temporário: {temp_dir}")

    try:
        with sync_playwright() as p:
            try:
                print("[TASK LOG] Iniciando Chromium headless...")
                browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox']) # Args comuns para ambientes restritos
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36' # User Agent comum
                )
                page = context.new_page()
                # Aumentar timeout padrão para operações da página
                page.set_default_timeout(90000) # 90 segundos

                print(f"[TASK LOG] Acessando página de login: {url_login}")
                page.goto(url_login, wait_until='networkidle') # Espera rede ficar ociosa

                if not email or not senha:
                    raise ValueError('Credenciais do Designi não fornecidas para a tarefa.')

                print("[TASK LOG] Preenchendo credenciais...")
                page.fill("input[name=email]", email, timeout=30000)
                page.fill("input[name=password]", senha, timeout=30000)

                # Resolver CAPTCHA se necessário
                solve_captcha(page, captcha_api_key, url_login)

                print("[TASK LOG] Tentando clicar no botão de login...")
                login_button = page.locator('button:has-text("Fazer login"), input[type="submit"]:has-text("Login")').first # Seletores mais genéricos
                login_button.wait_for(state="visible", timeout=30000)
                login_button.click()

                print("[TASK LOG] Aguardando navegação após login...")
                try:
                    # Espera por um elemento que só existe após o login ou que a URL mude
                    page.wait_for_url(lambda url: "/login" not in url, timeout=60000)
                    # Ou esperar por um elemento específico da página logada, se conhecido:
                    # page.wait_for_selector("#elemento_pos_login", state="visible", timeout=60000)
                except Exception as nav_err:
                    # Se ainda estiver na página de login, falhou
                    if "/login" in page.url:
                        page.screenshot(path=os.path.join(temp_dir, 'login_fail_screenshot.png'))
                        raise Exception(f"Falha no login (ainda em /login após espera): {nav_err}")
                    else:
                        # Pode ter logado, mas a espera falhou, continuar com cautela
                        print(f"[TASK WARNING] Login possivelmente bem-sucedido, mas espera falhou: {nav_err}. URL atual: {page.url}")


                print(f"[TASK LOG] Login parece bem-sucedido! URL atual: {page.url}")
                print(f"[TASK LOG] Navegando para a URL do arquivo: {designi_url}")
                page.goto(designi_url, wait_until='networkidle') # Espera carregar
                print(f"[TASK LOG] Página do arquivo carregada. URL atual: {page.url}")

                # --- Lógica de Download ---
                print("[TASK LOG] Aguardando botão de download...")
                 # Tentar seletores diferentes, incluindo os que podem aparecer em popups
                download_button_selector = "#downButton, a:has-text('Download'), button:has-text('Download')"
                download_button = page.locator(download_button_selector).first
                try:
                    download_button.wait_for(state="visible", timeout=60000) # 60s para aparecer
                    print("[TASK LOG] Botão de download visível.")
                except Exception as btn_err:
                    page.screenshot(path=os.path.join(temp_dir, 'download_button_fail.png'))
                    raise Exception(f"Botão de download ({download_button_selector}) não encontrado ou visível: {btn_err}")

                # Configurar o evento de download e clicar
                print("[TASK LOG] Configurando espera pelo download...")
                with page.expect_download(timeout=120000) as download_info: # 120s para download iniciar
                    print("[TASK LOG] Clicando no botão de download...")
                    download_button.click()
                    print("[TASK LOG] Clique realizado, aguardando início do download...")

                download = download_info.value
                if not download:
                    raise Exception("Evento de download não ocorreu após o clique.")

                suggested_filename = download.suggested_filename or f"designi_download_{int(time.time())}.file"
                temp_file_path = os.path.join(temp_dir, suggested_filename)
                print(f"[TASK LOG] Download iniciado: {suggested_filename}. Salvando em: {temp_file_path}")

                # Salvar o arquivo - Aumentar timeout aqui se os arquivos forem grandes
                # O timeout padrão do save_as é 30s, pode ser pouco.
                # Playwright não tem um timeout direto para save_as, ele espera o download terminar.
                # A limitação vem do expect_download e do timeout geral da página/contexto.
                download.save_as(temp_file_path)
                print(f"[TASK LOG] Download concluído: {temp_file_path}")

                # Verificar se o arquivo foi salvo e tem tamanho > 0
                if not os.path.exists(temp_file_path) or os.path.getsize(temp_file_path) == 0:
                     raise Exception(f"Falha ao salvar o download ou arquivo vazio: {temp_file_path}")

                file_size = os.path.getsize(temp_file_path)
                print(f"[TASK LOG] Arquivo salvo com sucesso. Tamanho: {file_size} bytes")

            except Exception as pw_error:
                print(f"[TASK ERROR] Erro durante automação com Playwright: {pw_error}")
                # Tentar tirar screenshot se a página ainda existir
                if 'page' in locals() and page and not page.is_closed():
                    try:
                         page.screenshot(path=os.path.join(temp_dir, 'playwright_error_screenshot.png'))
                    except Exception as ss_err:
                         print(f"[TASK WARNING] Não foi possível tirar screenshot do erro: {ss_err}")
                raise # Re-lança a exceção para ser pega pelo bloco principal

            finally:
                # Fechar navegador LÁ DENTRO do 'with sync_playwright()'
                if browser:
                    print("[TASK LOG] Fechando navegador Playwright...")
                    browser.close()
                    print("[TASK LOG] Navegador fechado.")


        # --- Upload para o Google Drive ---
        if temp_file_path and os.path.exists(temp_file_path):
            print("[TASK LOG] Iniciando upload para o Google Drive...")
            drive_service = get_drive_service_from_credentials(drive_credentials_base64)
            if not drive_service:
                raise Exception("Não foi possível obter o serviço do Google Drive.")

            filename = os.path.basename(temp_file_path)
            file_metadata = {'name': filename}
            if folder_id:
                file_metadata['parents'] = [folder_id]

            # Tentar detectar mimetype, fallback para octet-stream
            import mimetypes
            mimetype, _ = mimetypes.guess_type(temp_file_path)
            mimetype = mimetype or 'application/octet-stream'

            media = MediaFileUpload(temp_file_path, mimetype=mimetype, resumable=True)
            print(f"[TASK LOG] Enviando arquivo '{filename}' ({mimetype}) para o Google Drive...")

            # Aumentar timeout do request do Google API se necessário (não direto, mas pode ser afetado por timeouts de socket)
            file = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink' # Corrigido para webViewLink
            ).execute()

            print(f"[TASK LOG] Upload para Drive concluído. File ID: {file.get('id')}")

            # Adicionar permissão pública
            print("[TASK LOG] Definindo permissão de leitura pública...")
            drive_service.permissions().create(
                fileId=file.get('id'),
                body={'role': 'reader', 'type': 'anyone'}
            ).execute()
            print("[TASK LOG] Permissão pública definida.")

            end_time = time.time()
            duration = end_time - start_time
            print(f"[TASK SUCCESS] Tarefa concluída com sucesso em {duration:.2f} segundos.")

            # Retorna dados de sucesso para possível uso futuro (ex: notificar usuário)
            return {
                'success': True,
                'file_id': file.get('id'),
                'download_link': file.get('webViewLink'),
                'filename': filename,
                'duration_seconds': duration
            }
        else:
             raise Exception("Arquivo temporário não encontrado ou inválido após download.")


    except Exception as e:
        end_time = time.time()
        duration = end_time - start_time
        error_message = f"Erro na tarefa de download: {str(e)}"
        print(f"[TASK FAILED] {error_message} (Duração: {duration:.2f}s)")
        # Retorna dados de erro
        return {
            'success': False,
            'error': error_message,
            'duration_seconds': duration
        }

    finally:
        # Limpeza final do arquivo temporário, se existir
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                print(f"[TASK LOG] Arquivo temporário removido: {temp_file_path}")
            except Exception as e_clean:
                print(f"[TASK WARNING] Erro ao remover arquivo temporário {temp_file_path}: {str(e_clean)}")
        # Limpeza de screenshots de erro (opcional)
        for f in os.listdir(temp_dir):
             if f.endswith('.png'):
                  try:
                       os.remove(os.path.join(temp_dir, f))
                       print(f"[TASK LOG] Screenshot de erro removido: {f}")
                  except: pass # Ignora erros na limpeza de screenshots