# gunicorn.conf.py
import multiprocessing

# Количество воркеров
workers = multiprocessing.cpu_count() * 2 + 1

# Таймауты
timeout = 120  # Увеличиваем до 2 минут
graceful_timeout = 30
keepalive = 5

# Логирование
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Перезапуск воркеров
max_requests = 1000
max_requests_jitter = 100
