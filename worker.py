import os
import sys
from redis import Redis
from rq import Worker, Queue, Connection
from dotenv import load_dotenv

load_dotenv() # Carrega .env se existir

# Configurações de Fila
listen = ['default'] # Escuta a fila 'default'

# Conexão Redis (igual ao app.py, busca URL do ambiente)
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379')
print(f"[WORKER LOG] Tentando conectar ao Redis em: {REDIS_URL}")
conn = None

# --- Bloco de Conexão Redis CORRIGIDO ---
try:
    # A URL 'rediss://' (ou 'redis://') fornecida já indica como conectar.
    # Redis.from_url lida com isso automaticamente. Não precisamos de args extras para SSL se 'rediss://'.
    conn = Redis.from_url(REDIS_URL)
    conn.ping() # Testa a conexão
    # A mensagem de log agora será genérica, mas o importante é conectar
    print(f"[{'APP' if 'app.py' in __file__ else 'WORKER'} LOG] Tentando conectar ao Redis... Ping bem-sucedido!")
except Exception as e:
    print(f"[{'APP' if 'app.py' in __file__ else 'WORKER'} ERROR] Falha ao conectar ao Redis em {REDIS_URL}: {e}")
    # conn permanecerá None

# Iniciar o Worker RQ (somente se a conexão Redis funcionou)
if __name__ == '__main__':
    if conn: # Verifica se a conexão foi bem sucedida
        with Connection(conn):
            queues = map(Queue, listen)
            worker = Worker(queues)
            print(f"[WORKER LOG] Worker RQ pronto, escutando filas: {', '.join(listen)}")

            # Limpeza inicial do diretório temporário do worker (se existir)
            try:
                 temp_dir = '/tmp/designi_downloads' # Usar /tmp é mais padrão em containers
                 if os.path.exists(temp_dir):
                      print(f"[WORKER STARTUP] Limpando diretório temporário do worker: {temp_dir}")
                      import shutil
                      for filename in os.listdir(temp_dir):
                          file_path = os.path.join(temp_dir, filename)
                          try:
                              if os.path.isfile(file_path) or os.path.islink(file_path):
                                  os.unlink(file_path)
                              elif os.path.isdir(file_path):
                                  shutil.rmtree(file_path)
                          except Exception as e_rm:
                              print(f'[WORKER STARTUP WARNING] Falha ao deletar {file_path}. Razão: {e_rm}')
                 else:
                      os.makedirs(temp_dir, exist_ok=True) # Garante que existe
            except Exception as e_clean_init:
                 print(f"[WORKER STARTUP WARNING] Erro ao limpar/criar diretório temporário do worker: {e_clean_init}")

            # Inicia o loop de processamento de tarefas
            print("[WORKER LOG] Iniciando processamento de tarefas...")
            worker.work(with_scheduler=False) # Use with_scheduler=True se usar RQ Scheduler

        print("[WORKER LOG] Worker encerrado.")
    else:
        print("[WORKER ERROR] Worker não pode iniciar - Falha na conexão com o Redis.")
        sys.exit(1) # Sai com erro se não conectar ao Redis