FROM python:3.10-slim-bookworm

# Устанавливаем зависимости системы
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Рабочая директория
WORKDIR /app

# Копируем requirements и устанавливаем Python-зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/add_docker_group.sh /add_docker_group.sh
RUN chmod +x /add_docker_group.sh
ENTRYPOINT ["/add_docker_group.sh"]

# Копируем весь проект
COPY . .

# Запуск через gunicorn
CMD ["gunicorn", "bot_manager.wsgi:application", "--bind", "0.0.0.0:8000", "--config", "gunicorn.conf.py"]

