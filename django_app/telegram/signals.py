from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
import asyncio
import threading
import logging
from datetime import datetime

from telegram.models import BotSession, BotProfile
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError, PhoneNumberBannedError,
    AuthKeyUnregisteredError, SessionPasswordNeededError
)
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.photos import UpdateProfilePhotoRequest, DeletePhotosRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import InputPhoto

# Глобальный словарь для хранения старых значений
_bot_old_values = {}

logger = logging.getLogger(__name__)


@receiver(post_save, sender=BotSession)
def create_bot_profile(sender, instance, created, **kwargs):
    """Автоматическое создание профиля при создании бота."""
    if created:
        BotProfile.objects.create(bot=instance)


async def sync_bot_info_to_telegram(bot: BotSession, changes: dict = None):
    """Синхронизация информации бота с Telegram при изменении в БД."""
    client = None
    try:
        client = TelegramClient(
            session=StringSession(bot.session_string),
            api_id=bot.api_id,
            api_hash=bot.api_hash
        )
        await client.connect()

        if not await client.is_user_authorized():
            logger.warning(f"Бот {bot.phone} не авторизован")
            bot.is_active = False
            await bot.asave(update_fields=['is_active', 'updated_at'])
            return

        me = await client.get_me()
        logger.info(
            f"Синхронизация с Telegram для бота {bot.phone} (ID: {me.id})")

        # Обновление username
        if changes and changes.get('username') and bot.username:
            try:
                # Для обновления username используем UpdateUsernameRequest
                # Но сначала проверим, доступен ли username
                from telethon.tl.functions.account import UpdateUsernameRequest
                if bot.username:
                    await client(UpdateUsernameRequest(username=bot.username))
                    logger.info(
                        f"Username бота {bot.phone} обновлен в Telegram: @{bot.username}")
            except Exception as e:
                logger.warning(
                    f"Не удалось обновить username для бота {bot.phone}: {str(e)}")

        # Обновление имени и фамилии
        if changes and changes.get('name'):
            first_name = bot.first_name or ""
            last_name = bot.last_name or ""

            current_me = await client.get_me()
            if (current_me.first_name != first_name) or (current_me.last_name != last_name):
                await client(UpdateProfileRequest(
                    first_name=first_name,
                    last_name=last_name
                ))
                logger.info(
                    f"Имя бота {bot.phone} обновлено в Telegram: {first_name} {last_name}")

        # Обновление био
        if changes and changes.get('bio'):
            bio = bot.bio or ""
            full_info = await client(GetFullUserRequest(me))
            current_about = getattr(full_info.full_user, 'about', '') or ''

            if current_about != bio:
                await client(UpdateProfileRequest(about=bio))
                logger.info(
                    f"Био бота {bot.phone} обновлено в Telegram: {bio[:50]}...")

        # Обновление дня рождения (в био)
        if changes and changes.get('birthday') and bot.birthday:
            # Telegram не имеет отдельного поля для дня рождения,
            # поэтому добавляем его в био
            new_bio = bot.bio or ""
            if "🎂" not in new_bio:
                birthday_str = bot.birthday.strftime("%d.%m.%Y")
                new_bio = f"{new_bio} 🎂 {birthday_str}".strip()
                await client(UpdateProfileRequest(about=new_bio))
                logger.info(f"День рождения добавлен в био бота {bot.phone}")

        # Обновление аватарки
        if changes and changes.get('avatar'):
            await update_telegram_avatar(client, bot)

        # Если были изменения, обновляем время синхронизации
        if any(changes.values() if changes else []):
            bot.last_sync_at = datetime.now()
            await bot.asave(update_fields=['last_sync_at'])
            logger.info(f"Синхронизация бота {bot.phone} с Telegram завершена")

    except Exception as e:
        logger.error(
            f"Ошибка синхронизации бота {bot.phone} с Telegram: {str(e)}", exc_info=True)
    finally:
        if client:
            await client.disconnect()


async def update_telegram_avatar(client, bot):
    """
    Обновление аватара Telegram для Telethon 1.41.2
    """
    import os
    import aiohttp
    from io import BytesIO
    from django.conf import settings
    from django.utils import timezone
    from telethon.tl.functions.photos import (
        DeletePhotosRequest,
        UploadProfilePhotoRequest,
    )

    try:
        if not bot.avatar:
            logger.warning(f"У бота {bot.phone} нет аватарки")
            return

        logger.info(f"Обновление аватара бота {bot.phone}: {bot.avatar.name}")

        # ------------------------------------------------------------
        # 1. Удаляем старую аватарку (опционально)
        # ------------------------------------------------------------
        try:
            photos = await client.get_profile_photos("me", limit=1)
            if photos:
                await client(DeletePhotosRequest(photos))
                logger.info(f"Старая аватарка бота {bot.phone} удалена")
        except Exception as e:
            logger.warning(f"Не удалось удалить старую аватарку: {e}")

        # ------------------------------------------------------------
        # 2. Получаем данные изображения из ImageField
        # ------------------------------------------------------------
        try:
            # ImageField хранит файл, можно прочитать его содержимое
            if bot.avatar and bot.avatar.file:
                bot.avatar.file.open('rb')
                image_bytes = bot.avatar.file.read()
                bot.avatar.file.close()
                file_name = os.path.basename(bot.avatar.name)
            else:
                logger.error(
                    f"Не удалось прочитать файл аватара для бота {bot.phone}")
                return
        except Exception as e:
            logger.error(f"Ошибка чтения файла аватара: {e}")
            return

        # ------------------------------------------------------------
        # 3. Загружаем файл в Telegram
        # ------------------------------------------------------------
        uploaded = await client.upload_file(
            BytesIO(image_bytes),
            file_name=file_name,
        )

        # ------------------------------------------------------------
        # 4. Устанавливаем аватар
        # ------------------------------------------------------------
        await client(
            UploadProfilePhotoRequest(
                file=uploaded
            )
        )

        logger.info(f"Аватар бота {bot.phone} успешно обновлён")

        bot.last_sync_at = timezone.now()
        bot.save(update_fields=["last_sync_at"])

    except Exception as e:
        logger.error(
            f"Критическая ошибка при обновлении аватара бота {bot.phone}: {e}",
            exc_info=True,
        )


def run_sync_in_thread(bot_id, changes):
    """Запускает синхронизацию в отдельном потоке с новой event loop."""
    def sync_task():
        try:
            # Создаем новую event loop для этого потока
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Получаем свежий экземпляр из БД
            from django.db import connection
            connection.close()  # Закрываем старое соединение

            bot = BotSession.objects.get(id=bot_id)
            logger.info(
                f"Запуск синхронизации для бота {bot.phone} с изменениями: {changes}")

            # Запускаем синхронизацию
            loop.run_until_complete(sync_bot_info_to_telegram(bot, changes))
            loop.close()

            logger.info(f"Синхронизация для бота {bot.phone} завершена")

        except BotSession.DoesNotExist:
            logger.error(f"Бот {bot_id} не найден при синхронизации")
        except Exception as e:
            logger.error(
                f"Ошибка в потоке синхронизации: {str(e)}", exc_info=True)

    # Запускаем в отдельном потоке
    thread = threading.Thread(target=sync_task, daemon=True)
    thread.start()


@receiver(pre_save, sender=BotSession)
def bot_pre_save(sender, instance, **kwargs):
    """Сохраняет старые значения перед сохранением."""
    if instance.pk:
        try:
            old = BotSession.objects.get(pk=instance.pk)
            _bot_old_values[instance.pk] = {
                'first_name': old.first_name,
                'last_name': old.last_name,
                'bio': old.bio,
                'avatar': old.avatar,
                'username': old.username,
                'birthday': old.birthday,
            }
            logger.info(
                f"Предварительное сохранение старых значений для бота {instance.pk}")
        except BotSession.DoesNotExist:
            pass


@receiver(post_save, sender=BotSession)
def bot_post_save(sender, instance, created, **kwargs):
    """Автоматическая синхронизация с Telegram при изменении данных бота."""
    logger.info(
        f"Сработал сигнал post_save для BotSession {instance.id} ({instance.phone}), created={created}")

    if created:
        logger.info(
            f"Новый бот создан: {instance.phone}, синхронизация не требуется")
        return

    try:
        old_values = _bot_old_values.pop(instance.pk, None)

        if not old_values:
            logger.warning(
                f"Не найдены старые значения для бота {instance.pk}")
            return

        # Определяем, что изменилось
        old_avatar_name = old_values['avatar'].name if old_values['avatar'] else None
        new_avatar_name = instance.avatar.name if instance.avatar else None
        avatar_changed = old_avatar_name != new_avatar_name

        name_changed = (old_values['first_name'] != instance.first_name) or (
            old_values['last_name'] != instance.last_name)
        bio_changed = old_values['bio'] != instance.bio
        username_changed = old_values.get('username') != instance.username
        birthday_changed = old_values.get('birthday') != instance.birthday

        changes = {
            'name': name_changed,
            'bio': bio_changed,
            'avatar': avatar_changed,
            'username': username_changed,
            'birthday': birthday_changed,
        }

        # Если есть изменения, запускаем синхронизацию
        if any(changes.values()):
            logger.info(
                f"Обнаружены изменения для бота {instance.phone}: {changes}")
            logger.info(f"Запускаем синхронизацию...")
            run_sync_in_thread(instance.id, changes)
        else:
            logger.info(
                f"Нет изменений для синхронизации у бота {instance.phone}")

    except Exception as e:
        logger.error(
            f"Ошибка при проверке изменений бота {instance.id}: {str(e)}", exc_info=True)
