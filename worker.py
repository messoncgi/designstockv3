import os
import sys
from redis import Redis
from rq import Worker, Queue
from dotenv import load_dotenv
from urllib.parse import urlparse
try: from rq import Connection
except ImportError: Connection = None; print("[WORKER WARNING] Não importou 'Connection' de 'rq'.")

load_dotenv()

# Configurações de Fila
listen = ['default']

# Conexão Redis
REDIS_URL = os.environ.get('REDIS_URL')
conn = None

# --- Bloco de Conexão Redis com decode_responses=False ---
if REDIS_URL:
    try:
        print(f"[WORKER LOG] Tentando conectar ao Redis usando URL: {REDIS_URL}")
        parsed_url = urlparse(REDIS_URL)
        redis_host = parsed_url.hostname
        redis_port = parsed_url.port or 6379
        redis_password = parsed_url.password
        use_ssl = parsed_url.scheme == 'rediss' or (redis_host and 'upstash.io' in redis_host)
        if not redis_host or not redis_password: raise ValueError("Hostname/Senha não encontrados na REDIS_URL")
        print(f"[WORKER LOG] Conectando com: host={redis_host}, port={redis_port}, ssl={use_ssl}")

        conn = Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            ssl=use_ssl,
            ssl_cert_reqs=None,
            decode_responses=False # <<-- MUDANÇA PRINCIPAL AQUI
        )
        conn.ping()
        print(f"[WORKER LOG] Conexão Redis estabelecida e ping bem-sucedido!")
    except ValueError as ve: print(f"[WORKER ERROR] Erro ao parsear REDIS_URL: {ve}"); conn = None
    except Exception as redis_err: import traceback; print(f"[WORKER ERROR] Falha detalhada ao conectar/pingar Redis:\n{traceback.format_exc()}"); conn = None
else: print("[WORKER ERROR] Variável de ambiente REDIS_URL não definida."); conn = None

# Iniciar o Worker RQ (Versão Simplificada)
if __name__ == '__main__':
    if conn:
        try:
            # Passa a conexão diretamente para Queues e Worker
            queues = [Queue(name, connection=conn) for name in listen]
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

            print("[WORKER LOG] Worker encerrado.") # Não deve chegar aqui normalmente

        except Exception as main_err:
             print(f"[WORKER ERROR] Erro inesperado ao configurar/iniciar worker: {main_err}")
             import traceback; traceback.print_exc()
             sys.exit(1)
    else:
        print("[WORKER ERROR] Worker não pode iniciar - Falha conexão Redis.")
        sys.exit(1)