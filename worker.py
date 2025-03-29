import os
import sys
from redis import Redis
from rq import Worker, Queue # Removido 'Connection' do import direto
from dotenv import load_dotenv
from urllib.parse import urlparse # Para extrair partes da URL
# Tentar importar Connection aqui ou usar diretamente no 'with'
try:
    from rq import Connection
except ImportError:
    # Em algumas versões/setups, pode não ser necessário importar explicitamente
    # O 'with Connection(conn):' pode funcionar mesmo assim.
    # Definimos como None para que a verificação posterior funcione.
    Connection = None
    print("[WORKER WARNING] Não foi possível importar 'Connection' de 'rq'. Tentando contexto 'with' diretamente.")


load_dotenv()

# Configurações de Fila
listen = ['default']

# Conexão Redis (Obtendo dados da URL e usando parâmetros separados)
REDIS_URL = os.environ.get('REDIS_URL')
conn = None

# --- Bloco de Conexão Redis CORRIGIDO para usar parâmetros separados ---
if REDIS_URL:
    try:
        print(f"[WORKER LOG] Tentando conectar ao Redis usando URL: {REDIS_URL}")
        parsed_url = urlparse(REDIS_URL)
        redis_host = parsed_url.hostname
        redis_port = parsed_url.port or 6379
        redis_password = parsed_url.password
        use_ssl = parsed_url.scheme == 'rediss' or 'upstash.io' in redis_host

        if not redis_host or not redis_password:
             raise ValueError("Hostname ou Senha não encontrados na REDIS_URL")

        print(f"[WORKER LOG] Conectando com: host={redis_host}, port={redis_port}, ssl={use_ssl}")

        conn = Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            ssl=use_ssl,
            ssl_cert_reqs=None, # Necessário com ssl=True
            # decode_responses=True # Manter False para RQ geralmente é mais seguro
        )
        conn.ping()
        print(f"[WORKER LOG] Conexão Redis estabelecida e ping bem-sucedido!")

    except ValueError as ve:
        print(f"[WORKER ERROR] Erro ao parsear REDIS_URL: {ve}")
    except Exception as redis_err:
        import traceback
        print(f"[WORKER ERROR] Falha detalhada ao conectar/pingar Redis:")
        print(traceback.format_exc())
        print(f"[WORKER ERROR] Falha ao conectar ao Redis (Host: {redis_host}, Porta: {redis_port}, SSL: {use_ssl}): {redis_err}")
        conn = None
else:
    print("[WORKER ERROR] Variável de ambiente REDIS_URL não definida.")


# Iniciar o Worker RQ (somente se a conexão Redis funcionou)
if __name__ == '__main__':
    if conn: # Verifica se a conexão foi bem sucedida
        # O contexto 'with Connection(conn):' deve funcionar agora
        # mesmo que 'Connection' não tenha sido importado no topo.
        # Se Connection foi importado com sucesso acima, usamos ele.
        # Se não foi, o 'with' pode funcionar internamente ou precisamos de rq.Connection.
        ContextManager = Connection # Usa o import se funcionou
        if ContextManager is None:
             # Se o import falhou, tenta acessar via rq.Connection (menos comum)
             try:
                  import rq
                  ContextManager = rq.Connection
                  print("[WORKER INFO] Usando rq.Connection para contexto.")
             except (ImportError, AttributeError):
                  print("[WORKER ERROR] Falha crítica: Não foi possível obter um gerenciador de contexto Connection.")
                  sys.exit(1)

        try:
             with ContextManager(conn): # Usa a classe de contexto obtida
                queues = map(Queue, listen)
                # Passar a conexão explicitamente para o Worker
                worker = Worker(queues, connection=conn)
                print(f"[WORKER LOG] Worker RQ pronto, escutando filas: {', '.join(listen)}")

                # Limpeza inicial (mantido)
                try:
                     temp_dir = '/tmp/designi_downloads'
                     if os.path.exists(temp_dir):
                          print(f"[WORKER STARTUP] Limpando dir worker: {temp_dir}")
                          import shutil
                          for filename in os.listdir(temp_dir):
                              file_path = os.path.join(temp_dir, filename)
                              try:
                                  if os.path.isfile(file_path) or os.path.islink(file_path): os.unlink(file_path)
                                  elif os.path.isdir(file_path): shutil.rmtree(file_path)
                              except Exception as e_rm: print(f'[WORKER WARN] Falha ao deletar {file_path}: {e_rm}')
                     else: os.makedirs(temp_dir, exist_ok=True)
                except Exception as e_clean_init: print(f"[WORKER WARN] Erro limpar/criar dir worker: {e_clean_init}")

                print("[WORKER LOG] Iniciando processamento de tarefas...")
                worker.work(with_scheduler=False)

             print("[WORKER LOG] Worker encerrado.")

        except Exception as main_err:
             print(f"[WORKER ERROR] Erro inesperado ao configurar/iniciar worker: {main_err}")
             import traceback; traceback.print_exc()
             sys.exit(1)

    else:
        print("[WORKER ERROR] Worker não pode iniciar - Falha conexão Redis.")
        sys.exit(1)