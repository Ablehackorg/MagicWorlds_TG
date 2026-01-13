FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN mkdir data

# зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# код
COPY . .


ENV TZ=Europe/Moscow
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

