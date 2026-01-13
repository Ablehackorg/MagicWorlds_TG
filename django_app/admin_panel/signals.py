# from django.db.models.signals import post_migrate, post_save
# from django.contrib.auth.models import Group
# from django.dispatch import receiver
# import asyncio
# import threading
# import logging
# from datetime import datetime

# from telegram.models import BotSession
# from telethon import TelegramClient
# from telethon.sessions import StringSession
# from telethon.errors import (
#     FloodWaitError, PhoneNumberBannedError, 
#     AuthKeyUnregisteredError, SessionPasswordNeededError
# )
# from telethon.tl.functions.account import UpdateProfileRequest
# from telethon.tl.functions.photos import UpdateProfilePhotoRequest, DeletePhotosRequest
# from telethon.tl.functions.users import GetFullUserRequest
# from telethon.tl.types import InputPhoto

# logger = logging.getLogger(__name__)

# @receiver(post_migrate)
# def create_user_roles(sender, **kwargs):
#     if sender.name == "auth":  # только после миграций auth
#         Group.objects.get_or_create(name="moderator")
#         Group.objects.get_or_create(name="advertiser")

# async def sync_bot_info_to_telegram(bot: BotSession, changes: dict = None):
#     """Синхронизация информации бота с Telegram при изменении в БД."""
#     client = None
#     try:
#         client = TelegramClient(
#             session=StringSession(bot.session_string),
#             api_id=bot.api_id,
#             api_hash=bot.api_hash
#         )
#         await client.connect()
        
#         if not await client.is_user_authorized():
#             logger.warning(f"Бот {bot.phone} не авторизован")
#             bot.is_active = False
#             await bot.asave(update_fields=['is_active', 'updated_at'])
#             return
        
#         me = await client.get_me()
#         logger.info(f"Синхронизация с Telegram для бота {bot.phone} (ID: {me.id})")
        
#         # Обновление имени и фамилии
#         if changes and changes.get('name'):
#             first_name = bot.first_name or ""
#             last_name = bot.last_name or ""
            
#             # Получаем текущие данные из Telegram для сравнения
#             current_me = await client.get_me()
#             if (current_me.first_name != first_name) or (current_me.last_name != last_name):
#                 await client(UpdateProfileRequest(
#                     first_name=first_name,
#                     last_name=last_name
#                 ))
#                 logger.info(f"Имя бота {bot.phone} обновлено в Telegram: {first_name} {last_name}")
        
#         # Обновление био
#         if changes and changes.get('bio'):
#             bio = bot.bio or ""
#             full_info = await client(GetFullUserRequest(me))
#             current_about = getattr(full_info.full_user, 'about', '') or ''
            
#             if current_about != bio:
#                 await client(UpdateProfileRequest(about=bio))
#                 logger.info(f"Био бота {bot.phone} обновлено в Telegram: {bio[:50]}...")
        
#         # Обновление аватарки
#         if changes and changes.get('avatar'):
#             await update_telegram_avatar(client, bot)
        
#         # Если были изменения, обновляем время синхронизации
#         if any(changes.values() if changes else []):
#             bot.last_sync_at = datetime.now()
#             await bot.asave(update_fields=['last_sync_at'])
#             logger.info(f"Синхронизация бота {bot.phone} с Telegram завершена")
        
#     except PhoneNumberBannedError:
#         logger.error(f"Бот {bot.phone} забанен")
#         bot.is_banned = True
#         bot.is_active = False
#         await bot.asave(update_fields=['is_banned', 'is_active', 'updated_at'])
#     except AuthKeyUnregisteredError:
#         logger.error(f"Сессия бота {bot.phone} недействительна")
#         bot.is_active = False
#         await bot.asave(update_fields=['is_active', 'updated_at'])
#     except FloodWaitError as e:
#         logger.warning(f"Flood wait для бота {bot.phone}: {e.seconds} сек")
#     except SessionPasswordNeededError:
#         logger.error(f"Для бота {bot.phone} требуется 2FA")
#         bot.is_active = False
#         await bot.asave(update_fields=['is_active', 'updated_at'])
#     except Exception as e:
#         logger.error(f"Ошибка синхронизации бота {bot.phone} с Telegram: {str(e)}", exc_info=True)
#         # Не меняем статус при ошибке, возможно временные проблемы
#     finally:
#         if client:
#             await client.disconnect()

# async def update_telegram_avatar(client, bot):
#     """Обновление аватарки в Telegram."""
#     try:
#         # Удаляем старую аватарку если есть
#         photos = await client.get_profile_photos('me', limit=1)
#         if photos:
#             await client(DeletePhotosRequest([
#                 InputPhoto(id=photos[0].id, access_hash=photos[0].access_hash)
#             ]))
        
#         # Загружаем новую аватарку если есть URL
#         if bot.avatar_url:
#             import os
#             from django.conf import settings
#             from io import BytesIO
#             import aiohttp
            
#             # Проверяем локальный файл
#             if bot.avatar_url.startswith('/media/'):
#                 file_path = os.path.join(settings.MEDIA_ROOT, bot.avatar_url.replace('/media/', '', 1))
#                 if os.path.exists(file_path):
#                     result = await client.upload_file(file_path)
#                     await client(UpdateProfilePhotoRequest(file=result))
#                     logger.info(f"Аватар бота {bot.phone} обновлен из локального файла")
#                     return
            
#             # Проверяем URL
#             if bot.avatar_url.startswith('http'):
#                 async with aiohttp.ClientSession() as session:
#                     async with session.get(bot.avatar_url) as resp:
#                         if resp.status == 200:
#                             image_data = await resp.read()
#                             result = await client.upload_file(BytesIO(image_data))
#                             await client(UpdateProfilePhotoRequest(file=result))
#                             logger.info(f"Аватар бота {bot.phone} обновлен из URL")
#                             return
        
#         logger.warning(f"Не удалось обновить аватар для бота {bot.phone}: нет доступного файла")
        
#     except Exception as e:
#         logger.error(f"Ошибка при обновлении аватара бота {bot.phone}: {str(e)}")

# def run_sync_in_thread(bot_id, changes):
#     """Запускает синхронизацию в отдельном потоке с новой event loop."""
#     def sync_task():
#         try:
#             # Создаем новую event loop для этого потока
#             loop = asyncio.new_event_loop()
#             asyncio.set_event_loop(loop)
            
#             # Получаем свежий экземпляр из БД
#             from django.db import connection
#             connection.close()  # Закрываем старое соединение
            
#             bot = BotSession.objects.get(id=bot_id)
            
#             # Запускаем синхронизацию
#             loop.run_until_complete(sync_bot_info_to_telegram(bot, changes))
#             loop.close()
            
#         except BotSession.DoesNotExist:
#             logger.error(f"Бот {bot_id} не найден при синхронизации")
#         except Exception as e:
#             logger.error(f"Ошибка в потоке синхронизации: {str(e)}", exc_info=True)
    
#     # Запускаем в отдельном потоке
#     thread = threading.Thread(target=sync_task, daemon=True)
#     thread.start()

# @receiver(post_save, sender=BotSession)
# def bot_post_save(sender, instance, created, **kwargs):
#     """Автоматическая синхронизация с Telegram при изменении данных бота."""
#     if created:
#         return  # Не синхронизируем при создании
    
#     try:
#         # Получаем старую версию из базы данных
#         old = BotSession.objects.get(pk=instance.pk)
        
#         # Определяем, что изменилось
#         changes = {
#             'name': (old.first_name != instance.first_name) or (old.last_name != instance.last_name),
#             'bio': old.bio != instance.bio,
#             'avatar': old.avatar_url != instance.avatar_url,
#         }
        
#         # Если есть изменения, запускаем синхронизацию
#         if any(changes.values()):
#             logger.info(f"Обнаружены изменения для бота {instance.phone}: {changes}")
#             run_sync_in_thread(instance.id, changes)
#         else:
#             logger.debug(f"Нет изменений для синхронизации у бота {instance.phone}")
            
#     except BotSession.DoesNotExist:
#         logger.error(f"Бот {instance.id} не найден при проверке изменений")
#     except Exception as e:
#         logger.error(f"Ошибка при проверке изменений бота {instance.id}: {str(e)}", exc_info=True)