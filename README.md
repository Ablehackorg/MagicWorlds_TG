# Bot Manager

---

## 1. Назначение и контекст

**Bot Manager** — это многоуровневая система для управления Telegram-ботами, каналами и рекламными публикациями. Состоит из веб-панели (Django + AdminLTE), набора фоновых сервисов (Telethon, Twiboost API) и инфраструктуры Docker. Цель — централизованно контролировать публикации, накрутку просмотров, реакций и подписчиков, управлять задачами, логами и состоянием процессов.

---

## 1.1. Быстрый старт (для разработчика)

Этот раздел описывает минимальные шаги для запуска проекта и начала разработки.

### Требования

- Linux / macOS (Windows — через WSL2)
- Docker + Docker Compose
- Git
- Python 3.10+ (опционально, для локальной отладки)

---

### Клонирование репозитория

```bash
git clone <repo_url>
cd bot-manager
```

---

### Переменные окружения

Проект использует .env файлы для конфигурации контейнеров. Минимально необходимы:

- PostgreSQL (DB, user, password)
- Telegram API (api_id, api_hash)
- токены ботов
- таймзона

Пример:

```env
POSTGRES_DB=botmanager
POSTGRES_USER=bot_user
POSTGRES_PASSWORD=secret

TZ=Europe/Moscow
LOG_LEVEL=INFO
```

---

### Первый запуск

```bash
docker compose up -d --build
```

После запуска:

- Панель доступна на http://localhost:80
- Все фоновые сервисы стартуют автоматически через supervisord
- Логи доступны через docker logs и в UI

---

### Базовая инициализация данных

Для корректной работы системы необходимо создать базовые сущности:

1. **Country** — таймзоны и региональная логика
2. **Category** — тематики каналов / групп
3. **MainEntity** — Telegram-каналы и группы
4. **BotSession** — Telegram-аккаунты / боты

После этого становятся доступны:

- задачи публикаций,
- бустеры,
- синхронизация,
- специализированные сервисы.

---

Для корректной работы модуля ТГ-парсера, необходимо создать django_app/tg_parser/.env файл со следующим содержимым:

```env
TG_API_ID=
TG_API_HASH=
TG_SESSION_STRING=
```

Перед заполнением нужно авторизовать бота на сервере (в Админ-панели: Сообщества->Боты->Добавить бота). Данные должны быть взяты из БД, из таблицы telegram_botsession.

---

## 2. Архитектура

Система построена по микросервисному принципу: Django-панель управляет сущностями в БД, а независимые сервисы выполняют задачи.

### Основные уровни

| Слой | Назначение |
|------|------------|
| **Django / AdminLTE** | Панель управления и визуализация состояния системы |
| **API (ORM)** | Общее ядро данных и бизнес-сущности |
| **Post_TG Services** | Асинхронные публикации, бустинг, синхронизация |
| **TG Parser** | Проверка и сбор данных Telegram |
| **Infrastructure** | Docker, PostgreSQL, Redis, Nginx |

---

### 2.1. Архитектура моделей данных

Проект использует **двухуровневую систему моделей** для обеспечения гибкости и разделения ответственности:

#### Django-модели (django_app/models/*.py)

Django-модели являются **источником истины** для структуры базы данных:

- Определяют полную схему PostgreSQL БД
- Управляют миграциями через Django ORM
- Содержат бизнес-логику, валидацию и сигналы
- Используются веб-панелью (AdminLTE) для управления данными
- Организованы в модульную структуру по функциональным доменам

**Основные модули Django-моделей:**

```
django_app/models/
├── entities.py              # Каналы, категории, страны
├── publication_tasks.py     # Задачи публикаций
├── ads.py                   # Рекламные заказы
├── pinning.py              # Задачи закрепления
├── view_booster.py         # Накрутка просмотров
├── old_views.py            # Бустинг старых постов
├── subscribers.py          # Управление подписчиками
├── reactions.py            # Реакции на посты
├── channel_sync.py         # Синхронизация каналов
├── blondinka.py           # Комментарии в группах
└── booster.py             # Настройки бустеров
```

#### SQLAlchemy-модели (post_tg/models.py)

SQLAlchemy-модели обеспечивают **доступ к данным** для фоновых сервисов:

- **Не создают** схему БД, только отражают существующую
- Копируют только необходимые поля из Django-моделей
- Используются асинхронными сервисами (post_tg/*)
- Обеспечивают независимость сервисов от Django
- Оптимизированы для высокопроизводительных операций чтения/записи

**Принцип работы:**

```python
# Django создает таблицу
class MainEntity(models.Model):
    class Meta:
        db_table = "api_mainentity"
    
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=255)
    # ... полная схема с валидацией, индексами, связями

# SQLAlchemy подключается к существующей таблице
class MainEntity(Base):
    __tablename__ = "api_mainentity"
    
    id = Column(BigInteger, primary_key=True)
    name = Column(String(255))
    # ... только необходимые для работы сервиса поля
```

**Важно:** Любые изменения схемы БД должны выполняться через Django-миграции. SQLAlchemy-модели обновляются вручную только для добавления доступа к новым полям.

---

## 3. Основные модули

### Django-панель (admin_panel/)

- Реализована на **Django + AdminLTE**
- Каждая секция вынесена в отдельный views/*.py
- Использует api.models как единое ядро данных
- Поддерживает простое расширение новыми разделами

---

### API-ядро (api/)

- Общие ORM-модели (Django + SQLAlchemy)
- Единая точка истины для всех сервисов
- Используется панелью и всеми фоновыми контейнерами
- **Структура:**
  - `api/models.py` — центральный импорт всех моделей для обратной совместимости
  - `api/models/*.py` — модульная организация по доменам

---

### Telegram-парсер (tg_parser/)

- Асинхронный сервис на базе **Telethon**
- Проверка ссылок, сбор метаданных, аватары
- REST-эндпоинт /parse_channel

---

### Публикаторы и бустеры (post_tg/)

| Модуль | Назначение | Особенности |
|------|-----------|------------|
| entity_post | Плановые публикации | APScheduler, Telethon |
| ads_post | Рекламные посты | Автопин / автоудаление |
| daily_pinner | Закрепы | Таймзоны |
| view_booster | Накрутка просмотров | Twiboost API |
| old_views_booster | Старые посты | Лимиты, расходы |
| subscribers_booster | Подписчики | Сравнение списков |
| second_subscribers_booster | Компенсация оттока | Анализ счётчика |
| reaction_booster | Реакции | Гибкие сценарии |
| channel_sync | Синхронизация каналов | Альбомы, прогресс |
| blondinka_manager | Доменно-специфичный менеджер | Темы, run_now |
| admin_promoter | Административный сервис | Оркестрация |

---

## 4. Инфраструктура

- PostgreSQL
- Redis
- Nginx
- pgbackups
- Docker Compose

Используются общие volume: /avatars/, /static/, /telethon_sessions/.

---

## 5. Запуск и мониторинг

```bash
docker compose up -d
```

- Все сервисы запускаются автоматически
- Логи доступны через Docker и панель

---

## 6. Расширяемость

Добавление нового модуля:

1. **Модели** — создать в `django_app/models/` новый модуль
2. **Миграции** — выполнить `python manage.py makemigrations` и `migrate`
3. **SQLAlchemy** — при необходимости отразить нужные поля в `post_tg/models.py`
4. **UI** — добавить views и templates в `admin_panel/`
5. **Фоновая логика** — создать новый сервис в `post_tg/`
6. **Контейнер** — добавить сервис в `docker-compose.yml`

---

## 7. Работа с моделями

### Создание новой модели

1. Создайте Django-модель в соответствующем файле `django_app/models/`:

```python
# django_app/models/new_feature.py
from django.db import models

class NewFeature(models.Model):
    class Meta:
        db_table = "api_newfeature"
    
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

2. Импортируйте в `django_app/models/__init__.py`:

```python
from .new_feature import NewFeature
```

3. Создайте и примените миграцию:

```bash
python manage.py makemigrations
python manage.py migrate
```

4. Если сервисам нужен доступ, добавьте SQLAlchemy-модель:

```python
# post_tg/models.py
class NewFeature(Base):
    __tablename__ = "api_newfeature"
    
    id = Column(BigInteger, primary_key=True)
    name = Column(String(255))
    is_active = Column(Boolean, default=True)
```

### Изменение существующей модели

1. Измените Django-модель
2. Создайте миграцию
3. Обновите SQLAlchemy-модель при необходимости

**Важно:** Никогда не изменяйте SQLAlchemy-модели напрямую — только через Django-миграции!

---

## 8. Итог

**Django управляет**, **API хранит**, **сервисы выполняют**, **парсер проверяет**, а **Docker объединяет** всё в единую масштабируемую систему.

**Модели:** Django создаёт схему БД и управляет миграциями, SQLAlchemy предоставляет доступ фоновым сервисам к существующим данным.
