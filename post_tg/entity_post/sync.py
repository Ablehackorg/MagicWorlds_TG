import asyncio
import logging
from datetime import datetime, timedelta
from random import choice, random
import pytz

from telethon import functions, events
from telethon.errors import RPCError
from telethon.tl.custom.message import Message

from telegram_client import init_user_client
from entity_resolver import ensure_peer
from utils.db_utils import get_session, get_active_bots, get_tasks
from tg_copy import build_post, send_post, BuiltPost, _group_messages_for_posts
from models import EntityPostTask, MainEntity
from db_notify import listen_tasks_changed

# ------------------------------------------
#   Конфигурация
# ------------------------------------------
log = logging.getLogger("sync")
log.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
log.addHandler(handler)

MOSCOW_TZ = pytz.timezone("Europe/Moscow")
CHECK_INTERVAL = 30          # Проверка задач каждые 30 секунд
SEND_GAP_SECONDS = 25         # Допуск времени публикации (в секундах)
_RETRY_QUEUE: list[tuple[int, int]] = []  # Очередь (task_id, bot_id)
_SENT_GUARD: dict[int, datetime] = {}   # task_id → время последней публикации
GUARD_TTL = 60                          # антидубль: 60 секунд

# Глобальный кеш постов с подпиской на обновления
_POST_CACHE: dict[int, dict] = {}  # source_id -> {"posts": List[BuiltPost], "last_updated": datetime, "client": client, "entity": entity}
_CACHE_TTL = 300  # 5 минут
_SUBSCRIBED_SOURCES: set[int] = set()  # Уже подписанные источники

# Глобальные кеши данных из БД
_TASKS_CACHE: list[EntityPostTask] = []
_BOTS_CACHE: list = []
_TASKS_LAST_UPDATED: datetime = datetime.min
_BOTS_LAST_UPDATED: datetime = datetime.min
_CACHE_REFRESH_INTERVAL = 60  # Обновление кеша каждые 60 секунд

# Событие для уведомлений об изменениях в БД
_db_changed_event = asyncio.Event()

# ------------------------------------------
#   Кеширование данных из БД с подпиской на изменения
# ------------------------------------------

async def refresh_tasks_cache():
    """Обновляет кеш задач из БД."""
    global _TASKS_CACHE, _TASKS_LAST_UPDATED
    try:
        with get_session() as s:
            _TASKS_CACHE = get_tasks(s)
            _TASKS_LAST_UPDATED = datetime.now()
            log.debug(f"🔄 Кеш задач обновлен: {len(_TASKS_CACHE)} задач")
    except Exception as e:
        log.error(f"❌ Ошибка обновления кеша задач: {e}")


async def refresh_bots_cache():
    """Обновляет кеш ботов из БД."""
    global _BOTS_CACHE, _BOTS_LAST_UPDATED
    try:
        with get_session() as s:
            _BOTS_CACHE = get_active_bots(s)
            _BOTS_LAST_UPDATED = datetime.now()
            log.debug(f"🔄 Кеш ботов обновлен: {len(_BOTS_CACHE)} ботов")
    except Exception as e:
        log.error(f"❌ Ошибка обновления кеша ботов: {e}")


def get_cached_tasks() -> list[EntityPostTask]:
    """Возвращает кешированные задачи."""
    return _TASKS_CACHE.copy()


def get_cached_bots():
    """Возвращает кешированных ботов."""
    return _BOTS_CACHE.copy()


async def handle_db_changes():
    """Обрабатывает уведомления об изменениях в БД."""
    while True:
        await _db_changed_event.wait()
        _db_changed_event.clear()
        
        log.info("🔔 Обнаружены изменения в БД, обновляю кеши...")
        
        # Обновляем кеши
        await refresh_tasks_cache()
        await refresh_bots_cache()
        
        # Очищаем кеш постов, так как могли измениться источники
        global _POST_CACHE, _SUBSCRIBED_SOURCES
        _POST_CACHE.clear()
        _SUBSCRIBED_SOURCES.clear()
        log.info("✅ Кеши обновлены после изменений в БД")


async def check_cache_freshness():
    """Периодически проверяет актуальность кеша."""
    while True:
        now = datetime.now()
        
        # Проверяем кеш задач
        if (now - _TASKS_LAST_UPDATED).total_seconds() > _CACHE_REFRESH_INTERVAL:
            await refresh_tasks_cache()
        
        # Проверяем кеш ботов
        if (now - _BOTS_LAST_UPDATED).total_seconds() > _CACHE_REFRESH_INTERVAL:
            await refresh_bots_cache()
        
        await asyncio.sleep(_CACHE_REFRESH_INTERVAL)


# ------------------------------------------
#   Кеширование постов с подпиской на обновления
# ------------------------------------------

async def get_cached_posts(client, source_id: int) -> list:
    """Возвращает кэшированные посты из источника с подпиской на обновления."""
    now = datetime.now()
    
    # Проверяем актуальность кеша
    if source_id in _POST_CACHE:
        cache_data = _POST_CACHE[source_id]
        if (now - cache_data["last_updated"]).total_seconds() < _CACHE_TTL:
            return cache_data["posts"]
    
    # Получаем свежие посты
    posts = await build_post(client, source_id)
    
    # Получаем entity для подписки
    try:
        source_entity = await client.get_entity(source_id)
    except Exception as e:
        log.warning(f"⚠️ Не удалось получить entity для source_id {source_id}: {e}")
        return posts
    
    _POST_CACHE[source_id] = {
        "posts": posts,
        "last_updated": now,
        "client": client,
        "entity": source_entity
    }
    
    # Подписываемся на обновления, если еще не подписаны
    await _subscribe_to_updates(client, source_id, source_entity)
    
    return posts


async def _subscribe_to_updates(client, source_id: int, source_entity):
    """Подписывается на обновления канала для поддержания актуальности кеша."""
    if source_id in _SUBSCRIBED_SOURCES:
        return
        
    try:
        _SUBSCRIBED_SOURCES.add(source_id)
        
        @client.on(events.NewMessage(chats=source_entity))
        async def new_message_handler(event):
            """Обработчик новых сообщений."""
            try:
                if source_id not in _POST_CACHE:
                    return
                    
                msg = event.message
                if getattr(msg, "action", None) or getattr(msg, "service", False):
                    return
                if not (msg.message or msg.media):
                    return
                
                # Обновляем кеш
                await _update_cache_for_new_message(source_id, msg)
                log.debug(f"🔄 Кеш обновлен для source_id {source_id}: добавлено новое сообщение")
                
            except Exception as e:
                log.warning(f"⚠️ Ошибка в обработчике нового сообщения: {e}")
        
        @client.on(events.MessageDeleted(chats=source_entity))
        async def deleted_message_handler(event):
            """Обработчик удаленных сообщений."""
            try:
                if source_id not in _POST_CACHE:
                    return
                
                # Обновляем кеш
                await _update_cache_for_deleted_messages(source_id, event.deleted_ids)
                log.debug(f"🔄 Кеш обновлен для source_id {source_id}: удалены сообщения {event.deleted_ids}")
                
            except Exception as e:
                log.warning(f"⚠️ Ошибка в обработчике удаленных сообщений: {e}")
                
        log.info(f"✅ Подписка на обновления для source_id {source_id}")
    except Exception as e:
        log.warning(f"⚠️ Не удалось подписаться на обновления для source_id {source_id}: {e}")
        _SUBSCRIBED_SOURCES.discard(source_id)


async def _update_cache_for_new_message(source_id: int, new_message):
    """Обновляет кеш при получении нового сообщения."""
    if source_id not in _POST_CACHE:
        return
        
    cache_data = _POST_CACHE[source_id]
    current_posts = cache_data["posts"]
    
    # Получаем все текущие сообщения из постов
    all_messages = []
    for post in current_posts:
        all_messages.extend(post.messages)
    
    # Добавляем новое сообщение
    all_messages.append(new_message)
    
    # Перестраиваем посты
    new_posts = _group_messages_for_posts(all_messages)
    
    # Фильтруем посты старше 5 часов
    new_posts = [post for post in new_posts]
    
    # Обновляем кеш
    _POST_CACHE[source_id]["posts"] = new_posts
    _POST_CACHE[source_id]["last_updated"] = datetime.now()


async def _update_cache_for_deleted_messages(source_id: int, deleted_ids: list):
    """Обновляет кеш при удалении сообщений."""
    if source_id not in _POST_CACHE:
        return
        
    cache_data = _POST_CACHE[source_id]
    current_posts = cache_data["posts"]
    
    # Удаляем сообщения из постов
    new_posts = []
    for post in current_posts:
        remaining_messages = [msg for msg in post.messages if msg.id not in deleted_ids]
        if remaining_messages:
            new_posts.append(BuiltPost(messages=remaining_messages))
    
    # Обновляем кеш
    _POST_CACHE[source_id]["posts"] = new_posts
    _POST_CACHE[source_id]["last_updated"] = datetime.now()


def _clean_old_cache():
    """Очищает устаревшие записи кеша."""
    now = datetime.now()
    to_remove = []
    
    for source_id, cache_data in _POST_CACHE.items():
        if (now - cache_data["last_updated"]).total_seconds() > _CACHE_TTL * 2:  # Удаляем через 2*TTL
            to_remove.append(source_id)
    
    for source_id in to_remove:
        del _POST_CACHE[source_id]
        _SUBSCRIBED_SOURCES.discard(source_id)
        log.debug(f"🧹 Удален устаревший кеш для source_id {source_id}")


# ------------------------------------------
#   Публикация поста
# ------------------------------------------

async def publish_now(client, task: EntityPostTask, source: MainEntity, target: MainEntity):
    """Публикует посты с суффиксом, антидублем по времени и повтором."""
    try:
        now = datetime.now(MOSCOW_TZ)
        last_time = _SENT_GUARD.get(task.id)
        if last_time and (now - last_time).total_seconds() < GUARD_TTL:
            log.debug(f"🚫 Task#{task.id}: антидубль — прошло {int((now - last_time).total_seconds())} с")
            return
        _SENT_GUARD[task.id] = now

        # очищаем устаревшие записи (старше 5 мин)
        for k, t in list(_SENT_GUARD.items()):
            if (now - t).total_seconds() > 300:
                del _SENT_GUARD[k]

        log.info(f"🚀 Публикация задачи #{task.id}: {source.name} → {target.name} (bot#{task.bot_id})")

        # Используем кешированные посты с подпиской на обновления
        posts = await get_cached_posts(client, source.telegram_id)
        if not posts:
            log.warning(f"⚠️ Task#{task.id}: нет постов для публикации из '{source.name}'")
            return

        # Очищаем старый кеш периодически
        if random() < 0.1:  # 10% chance
            _clean_old_cache()

        post = choice(posts) if getattr(task, "choice_mode", "random") == "random" else posts[0]
            
        suffix = source.text_suffix or ""
        add_suffix = bool(source.is_add_suffix)

        try:
            target_entity = await ensure_peer(client, telegram_id=target.telegram_id)
        except Exception as e:
            log.warning(f"⚠️ Task#{task.id}: не удалось разрешить цель {target.telegram_id}: {e}")
            _RETRY_QUEUE.append((task.id, task.bot_id))
            return
        log.info(f"Превью: {post.first.web_preview}")
        sent_ids = await send_post(
            client,
            post,
            target_entity,
            text_suffix=suffix,
            is_add_suffix=add_suffix
        )
        log.info(f"✅ Task#{task.id}: опубликовано {len(sent_ids)} сообщений в '{target.name}' (ids={sent_ids})")

        if getattr(task, "after_publish", "cycle") == "remove":
            try:
                ids_to_del = [m.id for m in post.messages]
                if ids_to_del:
                    await client(functions.messages.DeleteMessagesRequest(
                        peer=await ensure_peer(client, telegram_id=source.telegram_id),
                        id=ids_to_del,
                        revoke=True
                    ))
                    log.info(f"🗑️ Task#{task.id}: удалены посты из '{source.name}'")
            except Exception as e:
                log.warning(f"⚠️ Task#{task.id}: ошибка удаления постов: {e}")

    except RPCError as e:
        log.warning(f"⚠️ Task#{task.id}: Telegram RPC ошибка: {e}")
        _RETRY_QUEUE.append((task.id, task.bot_id))
    except Exception as e:
        log.exception(f"💥 Task#{task.id}: ошибка публикации: {e}")
        _RETRY_QUEUE.append((task.id, task.bot_id))

# ------------------------------------------
#   Проверка и запуск публикаций
# ------------------------------------------
async def check_and_publish(client, bot_id: int):
    """Проверяет задачи и публикует при совпадении по времени."""
    now = datetime.now(MOSCOW_TZ)
    weekday = now.weekday()
    current_seconds = now.hour * 3600 + now.minute * 60 + now.second

    # Используем кешированные задачи
    tasks = get_cached_tasks()
    tasks = [t for t in tasks if t.bot_id == bot_id and t.is_active and t.is_global_active]
    log.info(f"🔎 bot#{bot_id}: проверка {len(tasks)} активных задач в {now.strftime('%H:%M:%S')}")

    for task in tasks:
        if not task.times:
            log.debug(f"Task#{task.id}: нет временных слотов")
            continue

        slots_today = [tt.seconds_from_day_start for tt in task.times if tt.weekday == weekday]
        if not slots_today:
            log.debug(f"Task#{task.id}: нет слотов на сегодня (день недели {weekday})")
            continue

        # Логируем слоты и текущее время
        log.debug(f"Task#{task.id}: слоты сегодня: {slots_today}, текущее время в секундах: {current_seconds}")

        match = any(abs(sec - current_seconds) <= SEND_GAP_SECONDS for sec in slots_today)
        if not match:
            continue

        log.info(f"▶️ Task#{task.id}: триггер по времени {now.strftime('%H:%M:%S')}")

        # Получаем source и target из БД
        with get_session() as s:
            src = s.get(MainEntity, task.source_id)
            tgt = s.get(MainEntity, task.target_id)
            if not (src and tgt):
                log.warning(f"⚠️ Task#{task.id}: некорректные source/target")
                continue

        await asyncio.sleep(2)
        await publish_now(client, task, src, tgt)

# ------------------------------------------
#   Обработка неудачных публикаций
# ------------------------------------------
async def retry_failed(clients: dict[int, any]):
    """Периодически повторяет неудачные публикации."""
    while True:
        if not _RETRY_QUEUE:
            await asyncio.sleep(60)
            continue

        log.info(f"🔁 Очередь повторных публикаций: {len(_RETRY_QUEUE)} задач")
        retries = _RETRY_QUEUE.copy()
        _RETRY_QUEUE.clear()

        for task_id, bot_id in retries:
            # Ищем задачу в кеше
            task = next((t for t in get_cached_tasks() if t.id == task_id), None)
            if not task:
                log.warning(f"⚠️ Retry Task#{task_id}: задача не найдена")
                continue
                
            client = clients.get(bot_id)
            if not client:
                log.warning(f"⚠️ Retry Task#{task_id}: клиент не найден")
                continue

            # Получаем source и target из БД
            with get_session() as s:
                src = s.get(MainEntity, task.source_id)
                tgt = s.get(MainEntity, task.target_id)
                if not (src and tgt):
                    log.warning(f"⚠️ Retry Task#{task_id}: некорректные source/target")
                    continue

            try:
                await publish_now(client, task, src, tgt)
                log.info(f"✅ Retry Task#{task_id}: успешно перепубликован.")
            except Exception as e:
                log.warning(f"⚠️ Retry Task#{task_id}: ошибка {e}")
                # Возвращаем в очередь для следующей попытки
                _RETRY_QUEUE.append((task_id, bot_id))

        await asyncio.sleep(120)


# ------------------------------------------
#   Основной цикл
# ------------------------------------------
async def run_sync():
    """Основной цикл realtime-публикаций."""
    log.info("🚀 Запуск realtime sync...")
    
    # Инициализация кешей
    await refresh_tasks_cache()
    await refresh_bots_cache()
    
    clients: dict[int, any] = {}

    # Используем кешированных ботов
    bots = get_cached_bots()
    log.info(f"🔍 Активных ботов: {len(bots)}")

    # Инициализация клиентов
    for b in bots:
        try:
            client = init_user_client(b)
            await client.start()
            if not await client.is_user_authorized():
                raise RuntimeError("Bot не авторизован")
            clients[b.id] = client
            me = await client.get_me()
            log.info(f"✅ Бот #{b.id} авторизован как @{getattr(me, 'username', None) or me.id}")
        except Exception as e:
            log.warning(f"⚠️ Не удалось запустить бота #{b.id}: {e}")

    if not clients:
        log.error("🚫 Нет активных клиентов. Завершение.")
        return

    # Запуск всех циклов
    try:
        await asyncio.gather(
            check_loop(clients),
            retry_failed(clients),
            handle_db_changes(),
            check_cache_freshness(),
            listen_tasks_changed(_db_changed_event)  # Запускаем слушатель БД
        )
    except KeyboardInterrupt:
        log.info("🛑 Остановка по Ctrl+C")
    except Exception as e:
        log.exception(f"💥 Фатальная ошибка в основном цикле: {e}")
    finally:
        # Закрываем клиентов при завершении
        for client in clients.values():
            await client.disconnect()


async def check_loop(clients: dict[int, any]):
    """Периодическая проверка задач."""
    while True:
        for bot_id, client in clients.items():
            try:
                await check_and_publish(client, bot_id)
            except Exception as e:
                log.exception(f"💥 Ошибка в check_and_publish для бота #{bot_id}: {e}")
        await asyncio.sleep(CHECK_INTERVAL)


# ------------------------------------------
#   Точка входа
# ------------------------------------------
if __name__ == "__main__":
    asyncio.run(run_sync())
