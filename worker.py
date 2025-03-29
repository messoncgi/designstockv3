import os
import sys
from redis import Redis
from rq import Worker, Queue # Importamos apenas Worker e Queue
from dotenv import load_dotenv
from urllib.parse import urlparse

load_dotenv()

# Configurações de Fila
listen = ['default']

# Conexão Redis (Mesma lógica de antes)
REDIS_URL = os.environ.get('REDIS_URL')
conn = None

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
            ssl_cert_reqs=None,
            # decode_responses=False # Manter False para RQ
        )
        conn.ping()
        print(f"[WORKER LOG] Conexão Redis estabelecida e ping bem-sucedido!")

    except ValueError as ve:
        print(f"[WORKER ERROR] Erro ao parsear REDIS_URL: {ve}")
        conn = None # Garante que é None em caso de falha
    except Exception as redis_err:
        import traceback
        print(f"[WORKER ERROR] Falha detalhada ao conectar/pingar Redis:")
        print(traceback.format_exc())
        print(f"[WORKER ERROR] Falha ao conectar ao Redis (Host: {redis_host}, Porta: {redis_port}, SSL: {use_ssl}): {redis_err}")
        conn = None
else:
    print("[WORKER ERROR] Variável de ambiente REDIS_URL não definida.")
    conn = None


# Iniciar o Worker RQ (Simplificado)
if __name__ == '__main__':
    if conn: # Verifica se a conexão foi bem sucedida
        try:
            # Criar uma lista de objetos Queue a partir dos nomes em 'listen'
            # Passando a conexão 'conn' diretamente para cada Queue
            queues = [Queue(name, connection=conn) for name in listen]

            # Instanciar o Worker, passando a lista de Queues
            # O worker usará a conexão associada às filas
            worker = Worker(queues, connection=conn) # Passar connection aqui também é seguro

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

            # Inicia o loop de processamento de tarefas (sem o 'with Connection...')
            print("[WORKER LOG] Iniciando processamento de tarefas...")
            worker.work(with_scheduler=False)

            print("[WORKER LOG] Worker encerrado (isso normalmente não deve acontecer a menos que seja interrompido).")

        except Exception as main_err:
             print(f"[WORKER ERROR] Erro inesperado ao configurar ou iniciar o worker: {main_err}")
             import traceback; traceback.print_exc()
             sys.exit(1)

    else:
        print("[WORKER ERROR] Worker não pode iniciar - Falha na conexão com o Redis.")
        sys.exit(1)