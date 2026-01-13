# admin_panel/views/bots.py

import json
import asyncio
import os
import logging
import threading
from datetime import datetime
from io import BytesIO

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_POST, require_GET
from django.contrib import messages
from django.db import transaction
from django.conf import settings
from django.utils.text import slugify
from django.db.models.signals import post_save
from django.dispatch import receiver

import aiohttp
from telegram.models import BotSession
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError, PhoneNumberBannedError, 
    AuthKeyUnregisteredError, SessionPasswordNeededError
)
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.photos import UpdateProfilePhotoRequest, DeletePhotosRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import InputPhoto

from telegram.models import BotProfile, BotNameHistory, BotUsernameHistory, BotPlugin, BotAdminGroup, BotSubscriberGroup, BotWarning
from admin_panel.models import Country, Category, Plugin

logger = logging.getLogger(__name__)

# ==================== СИНХРОНИЗАЦИЯ ====================

def get_bot_old_values(instance):
    """Сохраняет старые значения перед сохранением"""
    if instance.pk:
        try:
            old = BotSession.objects.get(pk=instance.pk)
            instance._old_first_name = old.first_name
            instance._old_last_name = old.last_name
            instance._old_bio = old.bio
            instance._old_avatar_url = old.avatar_url
        except BotSession.DoesNotExist:
            pass
    return instance



async def upload_avatar_to_telegram(client, bot: BotSession):
    """Загрузка аватарки в Telegram."""
    if not bot.avatar:
        logger.warning(f"У бота {bot.phone} нет аватарки")
        return
    
    avatar_url = bot.avatar.url
    
    if avatar_url.startswith('/media/'):
        file_path = os.path.join(settings.MEDIA_ROOT, avatar_url.replace('/media/', '', 1))
        if os.path.exists(file_path):
            result = await client.upload_file(file_path)
            await client(UpdateProfilePhotoRequest(file=result))
            logger.info(f"Аватар бота {bot.phone} обновлен из локального файла")
            return
    
    if avatar_url.startswith(settings.MEDIA_URL):
        file_path = os.path.join(settings.MEDIA_ROOT, avatar_url.replace(settings.MEDIA_URL, '', 1))
        if os.path.exists(file_path):
            result = await client.upload_file(file_path)
            await client(UpdateProfilePhotoRequest(file=result))
            logger.info(f"Аватар бота {bot.phone} обновлен из медиа")
            return
    
    if avatar_url.startswith('http'):
        async with aiohttp.ClientSession() as session:
            async with session.get(avatar_url) as resp:
                if resp.status == 200:
                    image_data = await resp.read()
                    result = await client.upload_file(BytesIO(image_data))
                    await client(UpdateProfilePhotoRequest(file=result))
                    logger.info(f"Аватар бота {bot.phone} обновлен из URL")

# ==================== АВАТАРКИ ====================

@login_required
@require_POST
def update_bot_avatar(request: HttpRequest, bot_id: int):
    """Обновление аватарки бота."""
    try:
        bot = BotSession.objects.get(id=bot_id)
        
        if 'avatar' not in request.FILES:
            return JsonResponse({"success": False, "error": "Файл не загружен"}, status=400)
        
        avatar_file = request.FILES['avatar']
        
        # Валидация файла
        error = validate_avatar_file(avatar_file)
        if error:
            return JsonResponse({"success": False, "error": error}, status=400)
        
        # Сохранение файла
        avatar_url = save_avatar_file(bot, avatar_file)
        
        return JsonResponse({
            "success": True,
            "avatar_url": avatar_url,
            "message": "Аватарка успешно обновлена"
        })
        
    except BotSession.DoesNotExist:
        return JsonResponse({"success": False, "error": "Бот не найден"}, status=404)
    except Exception as e:
        logger.error(f"Ошибка при обновлении аватарки бота {bot_id}: {str(e)}", exc_info=True)
        return JsonResponse({"success": False, "error": f"Внутренняя ошибка: {str(e)}"}, status=500)

@login_required
@require_POST
def remove_bot_avatar(request: HttpRequest, bot_id: int):
    """Удаление аватарки бота."""
    try:
        bot = BotSession.objects.get(id=bot_id)
        
        if not bot.avatar:
            return JsonResponse({"success": False, "error": "Аватарка не установлена"}, status=400)
        
        remove_avatar_file(bot)
        
        return JsonResponse({"success": True, "message": "Аватарка успешно удалена"})
        
    except BotSession.DoesNotExist:
        return JsonResponse({"success": False, "error": "Бот не найден"}, status=404)
    except Exception as e:
        logger.error(f"Ошибка при удалении аватарки бота {bot_id}: {str(e)}", exc_info=True)
        return JsonResponse({"success": False, "error": f"Внутренняя ошибка: {str(e)}"}, status=500)


@login_required
@require_POST
def fetch_bot_avatar(request: HttpRequest, bot_id: int):
    """Загрузка аватарки из Telegram."""
    try:
        bot = BotSession.objects.get(id=bot_id)
        
        if not all([bot.session_string, bot.api_id, bot.api_hash]):
            return JsonResponse({"success": False, "error": "Не настроена сессия или API"}, status=400)
        
        # Получаем аватар из Telegram
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        avatar_file_path = loop.run_until_complete(download_telegram_avatar(bot))
        loop.close()
        
        if avatar_file_path:
            # Открываем файл и сохраняем в ImageField
            with open(avatar_file_path, 'rb') as f:
                from django.core.files import File
                # Удаляем старый файл, если существует
                if bot.avatar and bot.avatar.name:
                    bot.avatar.delete(save=False)
                
                # Сохраняем новый файл
                bot.avatar.save(os.path.basename(avatar_file_path), File(f), save=True)
                bot.updated_at = datetime.now()
                bot.save(update_fields=['updated_at'])
            
            # Удаляем временный файл
            os.remove(avatar_file_path)
            
            return JsonResponse({
                "success": True,
                "avatar_url": bot.avatar.url,
                "message": "Аватарка загружена из Telegram"
            })
        else:
            return JsonResponse({"success": False, "error": "Не удалось загрузить аватар из Telegram"})
        
    except BotSession.DoesNotExist:
        return JsonResponse({"success": False, "error": "Бот не найден"}, status=404)
    except Exception as e:
        logger.error(f"Ошибка при загрузке аватарки из Telegram: {str(e)}", exc_info=True)
        return JsonResponse({"success": False, "error": f"Внутренняя ошибка: {str(e)}"}, status=500)

async def download_telegram_avatar(bot: BotSession) -> str:
    """Скачивание аватарки из Telegram и сохранение локально."""
    client = None
    try:
        client = TelegramClient(
            session=StringSession(bot.session_string),
            api_id=bot.api_id,
            api_hash=bot.api_hash
        )
        await client.connect()
        
        if not await client.is_user_authorized():
            return None
        
        # Получаем пользователя
        me = await client.get_me()
        
        # Получаем фото профиля напрямую
        photos = await client.get_profile_photos(me, limit=1)
        if not photos:
            logger.info(f"У бота {bot.phone} нет фото профиля")
            return None
        
        # Получаем первую фото
        photo = photos[0]
        
        # Пробуем скачать фото разными способами
        try:
            # Способ 1: Используем download_profile_photo (более надежный)
            file_bytes = await client.download_profile_photo(me, file=bytes)
            if file_bytes:
                logger.info(f"Способ 1: Аватар скачан через download_profile_photo")
            else:
                raise ValueError("Не удалось скачать аватар")
        except Exception as e1:
            logger.warning(f"Способ 1 не сработал: {str(e1)}")
            try:
                # Способ 2: Используем download_media
                file_bytes = await client.download_media(photo, bytes)
                logger.info(f"Способ 2: Аватар скачан через download_media")
            except Exception as e2:
                logger.warning(f"Способ 2 не сработал: {str(e2)}")
                try:
                    # Способ 3: Используем get_file для скачивания
                    from telethon.tl.functions.upload import GetFileRequest
                    from telethon.tl.types import InputPhotoFileLocation
                    
                    # Получаем расположение файла
                    location = InputPhotoFileLocation(
                        id=photo.id,
                        access_hash=photo.access_hash,
                        file_reference=photo.file_reference,
                        thumb_size='x'  # максимальный размер
                    )
                    
                    # Получаем файл
                    request = GetFileRequest(location, offset=0, limit=1024*1024*10)  # 10MB максимум
                    result = await client(request)
                    file_bytes = result.bytes
                    logger.info(f"Способ 3: Аватар скачан через GetFileRequest")
                except Exception as e3:
                    logger.error(f"Все способы скачивания аватара не сработали: {str(e3)}")
                    return None
        
        if not file_bytes or len(file_bytes) == 0:
            logger.error("Получены пустые данные файла")
            return None
        
        # Сохраняем локально
        avatars_dir = os.path.join(settings.MEDIA_ROOT, 'avatars', 'bots')
        os.makedirs(avatars_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"telegram_{bot.id}_{slugify(bot.phone)}_{timestamp}.jpg"
        file_path = os.path.join(avatars_dir, filename)
        
        # Определяем формат файла по заголовку
        import imghdr
        from io import BytesIO
        
        # Пробуем определить тип изображения
        img_type = imghdr.what(None, h=file_bytes)
        if img_type:
            filename = filename.replace('.jpg', f'.{img_type}')
            file_path = file_path.replace('.jpg', f'.{img_type}')
        
        with open(file_path, 'wb') as f:
            f.write(file_bytes)
        
        logger.info(f"Аватар Telegram сохранен: {file_path} ({len(file_bytes)} байт)")
        return file_path
        
    except Exception as e:
        logger.error(f"Ошибка при скачивании аватарки: {str(e)}", exc_info=True)
        return None
    finally:
        if client:
            await client.disconnect()

def validate_avatar_file(avatar_file):
    """Валидация загружаемого файла аватарки."""
    if avatar_file.size > 5 * 1024 * 1024:
        return "Файл слишком большой (максимум 5MB)"
    
    allowed_types = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
    if avatar_file.content_type not in allowed_types:
        return "Неподдерживаемый формат изображения"
    
    allowed_ext = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
    file_ext = os.path.splitext(avatar_file.name)[1].lower()
    if file_ext not in allowed_ext:
        return f"Неподдерживаемое расширение файла: {file_ext}"
    
    return None

def save_avatar_file(bot, avatar_file):
    """Сохранение файла аватарки на диск."""
    # Удаляем старый файл, если он существует
    if bot.avatar and bot.avatar.name:
        bot.avatar.delete(save=False)
    
    # Генерируем уникальное имя файла
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    ext = os.path.splitext(avatar_file.name)[1].lower()
    filename = f"bot_{bot.id}_{slugify(bot.phone)}_{timestamp}{ext}"
    
    # Сохраняем файл
    bot.avatar.save(filename, avatar_file, save=True)
    bot.save()
    
    return bot.avatar.url

def remove_avatar_file(bot):
    """Удаление файла аватарки."""
    if bot.avatar and bot.avatar.name:
        bot.avatar.delete(save=False)
        bot.avatar = None
        bot.save()

# ==================== TELEGRAM API ====================

@login_required
@require_GET
def fetch_bot_groups(request: HttpRequest, bot_id: int, group_type: str):
    """Получение групп бота из Telegram."""
    try:
        bot = BotSession.objects.get(id=bot_id)
        
        if not all([bot.session_string, bot.api_id, bot.api_hash]):
            return JsonResponse({"success": False, "error": "Не настроена сессия или API"})
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        if group_type == 'admin':
            groups = loop.run_until_complete(fetch_admin_groups(bot))
        elif group_type == 'subscriber':
            groups = loop.run_until_complete(fetch_subscriber_groups(bot))
        else:
            return JsonResponse({"success": False, "error": "Неверный тип групп"})
        
        loop.close()
        
        return JsonResponse({
            "success": True,
            "groups": groups,
            "message": f"Получено {len(groups)} групп"
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения групп: {str(e)}", exc_info=True)
        return JsonResponse({"success": False, "error": str(e)})

async def fetch_admin_groups(bot: BotSession):
    """Получение групп, где бот является администратором."""
    client = None
    admin_groups = []
    
    try:
        client = TelegramClient(
            session=StringSession(bot.session_string),
            api_id=bot.api_id,
            api_hash=bot.api_hash
        )
        await client.connect()
        
        if not await client.is_user_authorized():
            return admin_groups
        
        # Получаем диалоги
        async for dialog in client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                try:
                    # Проверяем права администратора
                    entity = await client.get_entity(dialog.entity)
                    if hasattr(entity, 'admin_rights') and entity.admin_rights:
                        admin_groups.append({
                            'id': entity.id,
                            'title': entity.title,
                            'username': entity.username,
                            'participants_count': getattr(entity, 'participants_count', 0),
                            'is_channel': dialog.is_channel,
                            'is_group': dialog.is_group,
                        })
                except Exception as e:
                    continue
        
    except Exception as e:
        logger.error(f"Ошибка получения админ-групп: {str(e)}")
    finally:
        if client:
            await client.disconnect()
    
    return admin_groups

async def fetch_subscriber_groups(bot: BotSession):
    """Получение групп, где бот является подписчиком."""
    client = None
    subscriber_groups = []
    
    try:
        client = TelegramClient(
            session=StringSession(bot.session_string),
            api_id=bot.api_id,
            api_hash=bot.api_hash
        )
        await client.connect()
        
        if not await client.is_user_authorized():
            return subscriber_groups
        
        # Получаем диалоги
        async for dialog in client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                try:
                    entity = await client.get_entity(dialog.entity)
                    
                    # Проверяем, является ли это нашей группой (из MainEntity)
                    is_our = False
                    # Здесь можно добавить логику проверки по username или id
                    
                    subscriber_groups.append({
                        'id': entity.id,
                        'title': entity.title,
                        'username': entity.username,
                        'is_our': is_our,
                        'is_channel': dialog.is_channel,
                        'is_group': dialog.is_group,
                    })
                except Exception as e:
                    continue
        
    except Exception as e:
        logger.error(f"Ошибка получения групп подписчика: {str(e)}")
    finally:
        if client:
            await client.disconnect()
    
    return subscriber_groups

async def fetch_bot_info_from_telegram(bot: BotSession) -> dict:
    """Получение актуальной информации о боте из Telegram."""
    client = None
    try:
        client = TelegramClient(
            session=StringSession(bot.session_string),
            api_id=bot.api_id,
            api_hash=bot.api_hash
        )
        await client.connect()
        
        if not await client.is_user_authorized():
            return error_response('Не авторизован. Проверьте сессию', False, False)
        
        me = await client.get_me()
        full_info = await client(GetFullUserRequest(me))
        
        # Проверка бана
        is_banned = False
        try:
            await client.get_dialogs(limit=1)
        except PhoneNumberBannedError:
            is_banned = True
        except AuthKeyUnregisteredError:
            return error_response('Сессия недействительна', False, False)
        
        # Получение фото
        photos = await client.get_profile_photos('me', limit=1)
        has_photo = bool(photos)
        
        # Подсчет диалогов
        dialogs_count = 0
        try:
            async for _ in client.iter_dialogs(limit=100):
                dialogs_count += 1
        except Exception as e:
            logger.warning(f"Не удалось получить диалоги для бота {bot.phone}: {str(e)}")
        
        return {
            'success': True,
            'is_active': True,
            'is_banned': is_banned,
            'username': me.username,
            'first_name': me.first_name,
            'last_name': me.last_name or '',
            'phone': me.phone,
            'user_id': me.id,
            'is_bot': me.bot,
            'verified': me.verified,
            'restricted': me.restricted,
            'about': getattr(full_info.full_user, 'about', '') or '',
            'has_photo': has_photo,
            'dialogs_count': dialogs_count,
            'common_chats_count': getattr(full_info.full_user, 'common_chats_count', 0),
            'last_seen': me.status.__class__.__name__ if me.status else 'offline',
            'updated_at': datetime.now().isoformat()
        }
        
    except SessionPasswordNeededError:
        return error_response('Требуется двухфакторная аутентификация', False, False)
    except FloodWaitError as e:
        return error_response(f'Ограничение Telegram: подождите {e.seconds} секунд', False, False)
    except PhoneNumberBannedError:
        return error_response('Аккаунт забанен в Telegram', False, True)
    except AuthKeyUnregisteredError:
        return error_response('Сессия устарела', False, False)
    except Exception as e:
        logger.error(f"Ошибка при получении информации о боте {bot.phone}: {str(e)}", exc_info=True)
        return error_response(f'Ошибка подключения: {str(e)}', False, False)
    finally:
        if client:
            await client.disconnect()

def error_response(error_msg, is_active, is_banned):
    """Создание ответа об ошибке."""
    return {
        'success': False,
        'is_active': is_active,
        'is_banned': is_banned,
        'error': error_msg
    }

# ==================== VIEWS ====================

@login_required
def bots_list(request: HttpRequest):
    """Отображает список всех ботов."""
    bots = BotSession.objects.all().order_by("-id")
    return render(request, "admin_panel/bots/bots.html", {"bots": bots})

def bot_edit_view(request, bot_id):
    """Страница детального редактирования бота."""
    bot = get_object_or_404(BotSession, id=bot_id)
    
    # Получаем или создаем профиль
    profile, created = BotProfile.objects.get_or_create(bot=bot)
    
    # Рассчитываем длительность регистрации в месяцах
    registration_duration = 0
    if bot.created_at:
        from datetime import datetime
        today = datetime.now().date()
        months = (today.year - bot.created_at.year) * 12 + (today.month - bot.created_at.month)
        registration_duration = max(months, 1)
    
    # Статусы
    status_choices = BotProfile.STATUS_CHOICES
    
    # Чей
    owner_choices = BotProfile.OWNER_CHOICES
    
    # Страны
    from admin_panel.models import Country
    countries = Country.objects.all()
    
    # Плагины
    from admin_panel.models import Plugin
    all_plugins = Plugin.objects.all()
    bot_plugins = BotPlugin.objects.filter(bot=bot, is_active=True).select_related('plugin')
    
    # Группы администратора
    admin_groups = BotAdminGroup.objects.filter(bot=bot, is_active=True).select_related('group')
    
    # Группы подписчика
    subscriber_groups = BotSubscriberGroup.objects.filter(bot=bot).select_related('group')
    
    # История
    username_history = BotUsernameHistory.objects.filter(bot=bot).order_by('-start_date')[:10]
    name_history = BotNameHistory.objects.filter(bot=bot).order_by('-start_date')[:10]
    
    # Предупреждения
    warnings = BotWarning.objects.filter(bot=bot).order_by('-date')[:10]
    
    # Прогресс для юзернейма (последняя запись)
    username_progress = 0
    if username_history.exists():
        username_progress = username_history.first().progress
    
    context = {
        'bot': bot,
        'profile': profile,
        'registration_duration': registration_duration,
        'status_choices': status_choices,
        'owner_choices': owner_choices,
        'countries': countries,
        'all_plugins': all_plugins,
        'plugins': bot_plugins,
        'admin_groups': admin_groups,
        'admin_groups_date': admin_groups.first().last_checked if admin_groups.exists() else None,
        'subscriber_groups': subscriber_groups,
        'subscriber_groups_total': subscriber_groups.count(),
        'username_history': username_history,
        'username_history_date': username_history.first().last_scrape_date if username_history.exists() else None,
        'username_progress': username_progress,
        'name_history': name_history,
        'warnings': warnings,
    }
    
    if request.method == 'POST':
        try:
            # Обработка плагинов
            selected_plugins = request.POST.getlist('plugins[]')
            remove_plugins_str = request.POST.get('remove_plugins', '')
            
            if remove_plugins_str:
                remove_ids = [int(id) for id in remove_plugins_str.split(',') if id.isdigit()]
                BotPlugin.objects.filter(id__in=remove_ids).delete()
            
            # Удаляем старые плагины
            BotPlugin.objects.filter(bot=bot).delete()
            
            # Добавляем новые
            for plugin_id in selected_plugins:
                if plugin_id:
                    try:
                        plugin = Plugin.objects.get(id=plugin_id)
                        BotPlugin.objects.create(bot=bot, plugin=plugin)
                    except Plugin.DoesNotExist:
                        pass

            bot.first_name = request.POST.get('first_name', '').strip()
            bot.last_name = request.POST.get('last_name', '').strip()
            bot.bio = request.POST.get('bio', '').strip()
            
            # Аватар
            if 'avatar' in request.FILES:
                avatar_file = request.FILES['avatar']
                
                # Проверяем валидность файла
                error = validate_avatar_file(avatar_file)
                if error:
                    messages.error(request, f'Ошибка загрузки аватарки: {error}')
                else:
                    # Сохраняем файл
                    avatar_url = save_avatar_file(bot, avatar_file)
                    messages.success(request, 'Аватарка успешно обновлена')

            # Обработка username (убираем @ если есть)
            username = request.POST.get('username', '').strip()
            if username:
                bot.username = username.lstrip('@')
            
            # Дата рождения
            birthday_str = request.POST.get('birthday', '').strip()
            if birthday_str:
                try:
                    bot.birthday = datetime.strptime(birthday_str, '%Y-%m-%d').date()
                except ValueError:
                    pass
            
            bot.save()
            
            # Обновление профиля
            profile.gender = request.POST.get('gender_value', 'male')
            profile.current_status = request.POST.get('current_status', '')
            profile.country = request.POST.get('country', '')
            profile.owner_type = request.POST.get('owner_type', '')
            profile.telegram_status = request.POST.get('telegram_status_value', 'regular')
            profile.notes = request.POST.get('notes', '')
            profile.save()
            
            
            messages.success(request, 'Данные бота успешно сохранены')
            return redirect('admin_panel:bot_edit', bot_id=bot.id)
            
        except Exception as e:
            messages.error(request, f'Ошибка сохранения: {str(e)}')
    
    return render(request, 'admin_panel/bots/edit.html', context)



@login_required
def add_bot(request: HttpRequest):
    """Отображает форму для добавления нового бота."""
    return render(request, "admin_panel/bots/add.html")

@login_required
@require_POST
def fetch_bot_telegram_info(request: HttpRequest, bot_id: int):
    """Получение актуальной информации о боте из Telegram."""
    try:
        bot = BotSession.objects.get(id=bot_id)
        
        if not bot.session_string:
            return JsonResponse({'success': False, 'error': 'Сессия не настроена'}, status=400)
        if not bot.api_id or not bot.api_hash:
            return JsonResponse({'success': False, 'error': 'API ID или API Hash не настроены'}, status=400)
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        telegram_info = loop.run_until_complete(fetch_bot_info_from_telegram(bot))
        loop.close()
        
        if telegram_info.get('success'):
            # Обновление информации в БД
            update_bot_from_telegram_info(bot, telegram_info)
            
            # Если есть фото в Telegram, но нет в нашей системе - загружаем его
            if telegram_info.get('has_photo'):
                logger.info(f"У бота {bot.phone} есть фото в Telegram, загружаем...")
                
                # Загружаем аватар из Telegram
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    avatar_file_path = loop.run_until_complete(download_telegram_avatar(bot))
                    loop.close()
                    
                    if avatar_file_path:
                        # Открываем файл и сохраняем в ImageField
                        with open(avatar_file_path, 'rb') as f:
                            from django.core.files import File
                            # Удаляем старый файл, если существует
                            if bot.avatar and bot.avatar.name:
                                bot.avatar.delete(save=False)
                            
                            # Сохраняем новый файл
                            filename = os.path.basename(avatar_file_path)
                            bot.avatar.save(filename, File(f), save=True)
                            bot.save(update_fields=['avatar'])
                        
                        # Удаляем временный файл
                        if os.path.exists(avatar_file_path):
                            os.remove(avatar_file_path)
                        
                        logger.info(f"Аватар бота {bot.phone} загружен из Telegram")
                    else:
                        logger.warning(f"Не удалось загрузить аватар из Telegram для бота {bot.phone}")
                except Exception as e:
                    logger.error(f"Ошибка при загрузке аватара из Telegram: {str(e)}", exc_info=True)
            
            return JsonResponse({
                'success': True,
                'telegram_info': telegram_info,
                'bot_data': get_bot_response_data(bot),
                'bot_id': bot_id,
                'message': 'Информация успешно синхронизирована'
            })
        else:
            bot.is_active = False
            if 'бан' in telegram_info.get('error', '').lower():
                bot.is_banned = True
            bot.save(update_fields=['is_active', 'is_banned', 'last_sync_at'])
            
            return JsonResponse({
                'success': False,
                'error': telegram_info.get('error', 'Неизвестная ошибка'),
                'telegram_info': telegram_info,
                'bot_id': bot_id
            }, status=400)
        
    except BotSession.DoesNotExist:
        return JsonResponse({"success": False, "error": "Бот не найден"}, status=404)
    except Exception as e:
        logger.error(f"Ошибка при получении информации из Telegram: {str(e)}", exc_info=True)
        return JsonResponse({"success": False, "error": f"Внутренняя ошибка: {str(e)}"}, status=500)

def update_bot_from_telegram_info(bot, telegram_info):
    """Обновление данных бота из информации Telegram."""
    bot.is_banned = telegram_info.get('is_banned', False)
    bot.is_active = telegram_info.get('is_active', True)  # По умолчанию True
    bot.last_sync_at = datetime.now()
    
    # Обновляем имя и фамилию всегда (не только если пустые)
    if telegram_info.get('first_name'):
        bot.first_name = telegram_info['first_name']
    if telegram_info.get('last_name'):
        bot.last_name = telegram_info['last_name']
    
    # Обновляем username
    if telegram_info.get('username'):
        bot.telegram_info = bot.telegram_info or {}
        bot.telegram_info['username'] = telegram_info['username']
    
    if not bot.telegram_info:
        bot.telegram_info = {}
    
    # Обновляем всю информацию
    bot.telegram_info.update({
        'last_sync': datetime.now().isoformat(),
        'username': telegram_info.get('username'),
        'user_id': telegram_info.get('user_id'),
        'has_photo': telegram_info.get('has_photo', False),
        'dialogs_count': telegram_info.get('dialogs_count', 0),
        'last_seen': telegram_info.get('last_seen', 'unknown'),
        'about': telegram_info.get('about', '')
    })
    
    bot.save(update_fields=[
        'is_banned', 'is_active', 'last_sync_at', 
        'first_name', 'last_name', 'telegram_info'
    ])
    
    logger.info(f"Данные бота {bot.phone} обновлены из Telegram")

def get_bot_response_data(bot):
    """Получение данных бота для ответа."""
    avatar_url = bot.avatar.url if bot.avatar else None
    
    return {
        'first_name': bot.first_name,
        'last_name': bot.last_name,
        'bio': bot.bio,
        'avatar_url': avatar_url,
        'is_active': bot.is_active,
        'is_banned': bot.is_banned,
        'last_sync_at': bot.last_sync_at.isoformat() if bot.last_sync_at else None,
    }

@login_required
@require_POST
def update_bot(request: HttpRequest):
    """Обновление данных одного бота."""
    try:
        data = parse_json_request(request)
        bot_id = data.get('id')
        first_name = (data.get('first_name') or "").strip()
        
        if not bot_id:
            return JsonResponse({"success": False, "error": "ID бота не указан"}, status=400)
        if not first_name:
            return JsonResponse({"success": False, "error": "Имя не может быть пустым"}, status=400)
        
        bot = BotSession.objects.get(id=bot_id)
        old_values = get_bot_old_values_for_log(bot)
        
        update_bot_fields(bot, data)
        bot.updated_at = datetime.now()
        bot.save()
        
        log_bot_changes(bot, old_values)
        
        return JsonResponse({
            "success": True,
            "message": "Данные бота успешно обновлены",
            "changes": get_change_list(bot, old_values)
        })
        
    except BotSession.DoesNotExist:
        return JsonResponse({"success": False, "error": "Бот не найден"}, status=404)
    except Exception as e:
        logger.error(f"Ошибка при обновлении бота: {str(e)}", exc_info=True)
        return JsonResponse({"success": False, "error": f"Внутренняя ошибка: {str(e)}"}, status=500)

@login_required
@require_POST
def bulk_update_bots(request: HttpRequest):
    """Пакетное обновление ботов."""
    try:
        payload = parse_json_request(request)
        items = payload.get("items") or []
        
        if not isinstance(items, list) or not items:
            return JsonResponse({"success": False, "error": "Пустой список изменений"}, status=400)

        updated, errors = bulk_update_bots_in_transaction(items)
        
        response_data = {
            "success": updated > 0,
            "updated": updated,
            "errors": errors,
            "message": f"Обновлено {updated} ботов"
        }
        
        status_code = 200
        if errors and not updated:
            status_code = 400
        elif errors and updated:
            status_code = 207
        
        return JsonResponse(response_data, status=status_code)
        
    except Exception as e:
        logger.error(f"Ошибка при пакетном обновлении ботов: {str(e)}", exc_info=True)
        return JsonResponse({"success": False, "error": f"Внутренняя ошибка: {str(e)}"}, status=500)

@login_required
@require_POST
def delete_bot(request: HttpRequest, bot_id: int):
    """Удаление бота только из локальной БД."""
    try:
        with transaction.atomic():
            bot = BotSession.objects.select_for_update().get(id=bot_id)
            
            # Удаляем аватар если он существует
            if bot.avatar:
                bot.avatar.delete(save=False)
            
            bot_info = f"{bot.first_name} {bot.last_name} ({bot.phone})"
            bot.delete()
            
            logger.info(f"Бот удален: {bot_info}")
            
        return JsonResponse({"success": True, "message": "Бот успешно удален"})
        
    except BotSession.DoesNotExist:
        return JsonResponse({"success": False, "error": "Бот не найден"}, status=404)
    except Exception as e:
        logger.error(f"Ошибка при удалении бота {bot_id}: {str(e)}", exc_info=True)
        return JsonResponse({"success": False, "error": f"Внутренняя ошибка: {str(e)}"}, status=500)

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def parse_json_request(request):
    """Парсинг JSON из запроса."""
    try:
        return json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        raise ValueError("Неверный формат JSON")

def get_bot_old_values_for_log(bot):
    """Получение старых значений бота для логирования."""
    return {
        'first_name': bot.first_name,
        'last_name': bot.last_name,
        'bio': bot.bio,
        'birthday': bot.birthday
    }

def update_bot_fields(bot, data):
    """Обновление полей бота из данных."""
    bot.first_name = (data.get('first_name') or "").strip()
    bot.last_name = (data.get('last_name') or "").strip()
    bot.description = (data.get('description') or "").strip()
    bot.bio = (data.get('bio') or "").strip()
    
    birthday_str = data.get('birthday', '').strip()
    if birthday_str:
        try:
            bot.birthday = datetime.strptime(birthday_str, '%Y-%m-%d').date()
        except ValueError:
            raise ValueError("Неверный формат даты рождения. Используйте YYYY-MM-DD")
    else:
        bot.birthday = None

def log_bot_changes(bot, old_values):
    """Логирование изменений бота."""
    changes = get_change_list(bot, old_values)
    if changes:
        logger.info(f"Бот {bot.phone} обновлен: {', '.join(changes)}")

def get_change_list(bot, old_values):
    """Получение списка изменений бота."""
    changes = []
    if old_values['first_name'] != bot.first_name or old_values['last_name'] != bot.last_name:
        changes.append(f"имя: {old_values['first_name']} {old_values['last_name']} → {bot.first_name} {bot.last_name}")
    if old_values['bio'] != bot.bio:
        changes.append("био обновлено")
    if old_values['birthday'] != bot.birthday:
        changes.append("день рождения обновлен")
    return changes

def bulk_update_bots_in_transaction(items):
    """Пакетное обновление ботов в транзакции."""
    updated = 0
    errors = []
    
    logger.info(f"Начинаем пакетное обновление {len(items)} ботов в транзакции")
    
    with transaction.atomic():
        for i, it in enumerate(items, start=1):
            try:
                bot_id = it.get("id")
                first_name = (it.get("first_name") or "").strip()
                
                logger.info(f"Обработка бота #{i}: id={bot_id}, имя='{first_name}'")
                
                if not bot_id:
                    raise ValueError("ID бота не указан")
                if not first_name:
                    raise ValueError("Имя не может быть пустым")

                bot = BotSession.objects.select_for_update().get(id=bot_id)
                
                # Сохраняем старые значения ДО обновления
                old_first_name = bot.first_name
                old_last_name = bot.last_name
                old_bio = bot.bio
                old_avatar = bot.avatar  # Изменено с old_avatar_url
                
                # Обновляем поля
                update_bot_fields(bot, it)
                
                # Определяем, что изменилось (для ImageField сравниваем имена файлов)
                old_avatar_name = old_avatar.name if old_avatar else None
                new_avatar_name = bot.avatar.name if bot.avatar else None
                avatar_changed = old_avatar_name != new_avatar_name
                
                changes_detected = {
                    'name': (old_first_name != bot.first_name) or (old_last_name != bot.last_name),
                    'bio': old_bio != bot.bio,
                    'avatar': avatar_changed,
                }
                
                # Сохраняем
                bot.updated_at = datetime.now()
                bot.save()
                updated += 1
                
                # Если есть изменения, запускаем синхронизацию напрямую
                if any(changes_detected.values()):
                    logger.info(f"Обнаружены изменения для бота {bot.phone}: {changes_detected}")
                    logger.info(f"Запускаем синхронизацию напрямую...")
                    
                    # Импортируем и запускаем синхронизацию
                    try:
                        from telegram.signals import run_sync_in_thread
                        run_sync_in_thread(bot.id, changes_detected)
                    except ImportError as e:
                        logger.error(f"Не удалось импортировать run_sync_in_thread: {e}")
                
                logger.info(f"Бот {bot_id} успешно обновлен")
                
            except BotSession.DoesNotExist:
                error_msg = "Бот не найден"
                logger.error(f"Бот {it.get('id')} не найден")
                errors.append({"index": i, "id": it.get("id"), "error": error_msg})
            except Exception as e:
                logger.error(f"Ошибка обновления бота {it.get('id')}: {str(e)}")
                errors.append({"index": i, "id": it.get("id"), "error": str(e)})
    
    logger.info(f"Пакетное обновление завершено: успешно {updated}, ошибок {len(errors)}")
    return updated, errors