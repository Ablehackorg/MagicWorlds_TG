# tg_parser/profile_views.py
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
import json
import logging
import os
import mimetypes
from typing import Optional, Dict, Any

from telethon import functions, types
from telethon.errors import (
    PhotoInvalidError,
    FirstNameInvalidError,
)
from telethon.errors.rpcerrorlist import (
    AboutTooLongError,
)
from telethon.tl.functions.account import (
    UpdateProfileRequest,
    UpdateUsernameRequest,
    GetPrivacyRequest,
    SetPrivacyRequest,
)
from telethon.tl.functions.photos import (
    UploadProfilePhotoRequest,
    DeletePhotosRequest,
)
from telethon.tl.types import (
    InputPhoto,
    InputPrivacyKeyStatusTimestamp,
    InputPrivacyValueAllowAll,
    InputPrivacyValueAllowContacts,
    InputPrivacyValueAllowUsers,
    InputPrivacyValueDisallowAll,
    InputPrivacyValueDisallowContacts,
    InputPrivacyValueDisallowUsers,
)

from .client import get_client, run_in_client
import re

logger = logging.getLogger(__name__)

USERNAME_MIN_LENGTH = 5
USERNAME_MAX_LENGTH = 32

# ==================== Новые функции для изменения профиля ====================


async def _update_profile_info(
    client,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    about: Optional[str] = None
) -> Dict[str, Any]:
    """
    Обновление основной информации профиля.
    """
    updates = {}

    if first_name is not None or last_name is not None or about is not None:
        await client(UpdateProfileRequest(
            first_name=first_name or "",
            last_name=last_name or "",
            about=about or ""
        ))

        if first_name is not None:
            updates['first_name'] = first_name
        if last_name is not None:
            updates['last_name'] = last_name
        if about is not None:
            updates['about'] = about

    return updates


async def _update_username(client, username: str) -> Dict[str, Any]:
    """
    Обновление username.
    """
    if not username:
        await client(UpdateUsernameRequest(username=""))
        return {'username': None}

    # Проверка длины username
    if len(username) < USERNAME_MIN_LENGTH or len(username) > USERNAME_MAX_LENGTH:
        raise ValueError(
            f"Username должен быть от {USERNAME_MIN_LENGTH} до {USERNAME_MAX_LENGTH} символов"
        )

    # Проверка формата username
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', username):
        raise ValueError(
            "Username должен начинаться с буквы и содержать только буквы, цифры и нижние подчёркивания"
        )

    await client(UpdateUsernameRequest(username=username))
    return {'username': username}


async def _update_profile_photo(client, photo_path: str) -> Dict[str, Any]:
    """
    Обновление фотографии профиля.
    """
    if not os.path.exists(photo_path):
        raise FileNotFoundError(f"Файл не найден: {photo_path}")

    # Проверка размера файла (макс 10 MB)
    file_size = os.path.getsize(photo_path)
    if file_size > 10 * 1024 * 1024:
        raise ValueError("Размер файла не должен превышать 10 MB")

    # Проверка типа файла
    mime_type, _ = mimetypes.guess_type(photo_path)
    if not mime_type or not mime_type.startswith('image/'):
        raise ValueError("Файл должен быть изображением")

    # Загрузка фото
    with open(photo_path, 'rb') as f:
        file_data = f.read()

    # Создаем файл для загрузки
    file = await client.upload_file(file_data, file_name=os.path.basename(photo_path))

    # Обновляем фото профиля
    result = await client(UploadProfilePhotoRequest(file=file))

    # Возвращаем информацию о новом фото
    if result and hasattr(result, 'photos') and result.photos:
        photo = result.photos[0]
        if isinstance(photo, types.Photo):
            return {
                'photo_id': photo.id,
                'access_hash': photo.access_hash,
                'file_reference': photo.file_reference,
            }

    return {'photo_updated': True}


async def _delete_profile_photo(client, photo_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Удаление фотографии профиля.
    Если photo_id не указан - удаляет текущую главную фото.
    """
    # Получаем текущие фото
    photos = await client.get_profile_photos('me')

    if not photos:
        return {'deleted': False, 'message': 'Нет фото для удаления'}

    if photo_id:
        # Удаляем конкретное фото
        for photo in photos:
            if photo.id == photo_id:
                await client(DeletePhotosRequest(id=[InputPhoto(
                    id=photo.id,
                    access_hash=photo.access_hash,
                    file_reference=photo.file_reference
                )]))
                return {'deleted': True, 'photo_id': photo_id}
        raise ValueError(f"Фото с ID {photo_id} не найдено")
    else:
        # Удаляем самое последнее (текущее) фото
        latest_photo = photos[0]
        await client(DeletePhotosRequest(id=[InputPhoto(
            id=latest_photo.id,
            access_hash=latest_photo.access_hash,
            file_reference=latest_photo.file_reference
        )]))
        return {'deleted': True, 'photo_id': latest_photo.id}


async def _update_privacy_settings(
    client,
    privacy_key: str,
    allow_all: Optional[bool] = None,
    allow_contacts: Optional[bool] = None,
    allow_users: Optional[list] = None,
    disallow_users: Optional[list] = None
) -> Dict[str, Any]:
    """
    Обновление настроек приватности.

    Поддерживаемые privacy_key:
    - 'status_timestamp' - время последнего посещения
    """
    # Маппинг ключей на соответствующие классы
    privacy_key_map = {
        'status_timestamp': InputPrivacyKeyStatusTimestamp(),
    }

    if privacy_key not in privacy_key_map:
        raise ValueError(f"Неподдерживаемый ключ приватности: {privacy_key}")

    input_key = privacy_key_map[privacy_key]
    rules = []

    # Добавляем правила в зависимости от параметров
    if allow_all:
        rules.append(InputPrivacyValueAllowAll())
    if allow_contacts:
        rules.append(InputPrivacyValueAllowContacts())
    if allow_users:
        # Преобразуем usernames/IDs в InputUser
        input_users = []
        for user in allow_users:
            try:
                entity = await client.get_entity(user)
                input_users.append(await client.get_input_entity(entity))
            except Exception as e:
                logger.warning(f"Не удалось получить пользователя {user}: {e}")
                continue

        if input_users:
            rules.append(InputPrivacyValueAllowUsers(input_users))

    if disallow_users:
        input_users = []
        for user in disallow_users:
            try:
                entity = await client.get_entity(user)
                input_users.append(await client.get_input_entity(entity))
            except Exception as e:
                logger.warning(f"Не удалось получить пользователя {user}: {e}")
                continue

        if input_users:
            rules.append(InputPrivacyValueDisallowUsers(input_users))

    # Если не заданы конкретные правила, устанавливаем разумные по умолчанию
    if not rules:
        rules.append(InputPrivacyValueAllowContacts())

    # Обновляем настройки приватности
    await client(SetPrivacyRequest(key=input_key, rules=rules))

    return {
        'privacy_key': privacy_key,
        'rules_applied': len(rules),
        'updated': True
    }


async def _get_current_profile(client) -> Dict[str, Any]:
    """
    Получение текущей информации профиля.
    """
    me = await client.get_me()
    full_info = await client(functions.users.GetFullUserRequest(me))

    # Получаем фото профиля
    photos = await client.get_profile_photos('me', limit=1)
    photo_info = None
    if photos:
        photo = photos[0]
        photo_info = {
            'id': photo.id,
            'date': photo.date.isoformat() if photo.date else None,
            'sizes': len(photo.sizes) if hasattr(photo, 'sizes') else 0
        }

    # Получаем настройки приватности статуса
    privacy_settings = {}
    try:
        privacy = await client(GetPrivacyRequest(key=InputPrivacyKeyStatusTimestamp()))
        privacy_settings['status'] = [
            type(rule).__name__ for rule in privacy.rules
        ]
    except Exception as e:
        logger.warning(f"Не удалось получить настройки приватности: {e}")

    return {
        'id': me.id,
        'first_name': me.first_name,
        'last_name': me.last_name,
        'username': me.username,
        'phone': me.phone,
        'is_bot': me.bot,
        'verified': me.verified,
        'restricted': me.restricted,
        'about': getattr(full_info.full_user, 'about', None),
        'photo': photo_info,
        'privacy_settings': privacy_settings,
        'common_chats_count': getattr(full_info.full_user, 'common_chats_count', 0),
        'can_pin_message': getattr(full_info.full_user, 'can_pin_message', False),
    }

# ==================== Основная view для обновления профиля ====================


@login_required
@csrf_exempt
def update_profile(request):
    """
    POST /api/update_profile

    Обновление различных параметров профиля.

    Поддерживаемые параметры:
    - first_name: Имя
    - last_name: Фамилия
    - about: Описание (био)
    - username: Username
    - photo_action: 'upload' или 'delete'
    - photo_path: Путь к файлу (для upload)
    - photo_id: ID фото для удаления (для delete)
    - privacy_settings: Словарь настроек приватности

    Примеры запросов:

    1. Изменить имя и описание:
    {
        "first_name": "Новое имя",
        "about": "Новое описание"
    }

    2. Изменить username:
    {
        "username": "newusername"
    }

    3. Загрузить новое фото:
    {
        "photo_action": "upload",
        "photo_path": "/path/to/photo.jpg"
    }

    4. Удалить фото:
    {
        "photo_action": "delete",
        "photo_id": 1234567890
    }

    5. Настройки приватности:
    {
        "privacy_settings": {
            "key": "status_timestamp",
            "allow_contacts": true,
            "allow_users": ["@username1", "@username2"]
        }
    }

    6. Получить текущий профиль:
    GET запрос без параметров
    """

    if request.method == 'GET':
        # Возвращаем текущую информацию профиля
        try:
            data = run_in_client(_get_current_profile)
            return JsonResponse({'success': True, 'profile': data})
        except Exception as e:
            logger.exception("Ошибка при получении профиля")
            return JsonResponse({
                'error': 'Не удалось получить информацию профиля',
                'details': str(e)
            }, status=500)

    elif request.method != 'POST':
        return JsonResponse({"error": "Only GET and POST allowed"}, status=405)

    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "Некорректный JSON"}, status=400)

    client = get_client()
    results = {}

    try:
        # --- Обновление основной информации ---
        first_name = body.get('first_name')
        last_name = body.get('last_name')
        about = body.get('about')

        if any([first_name is not None, last_name is not None, about is not None]):
            updates = run_in_client(
                _update_profile_info,
                first_name=first_name,
                last_name=last_name,
                about=about
            )
            results.update({'profile_info': updates})

        # --- Обновление username ---
        username = body.get('username')
        if username is not None:
            updates = run_in_client(_update_username, username=username)
            results.update({'username': updates})

        # --- Управление фото профиля ---
        photo_action = body.get('photo_action')
        if photo_action:
            if photo_action == 'upload':
                photo_path = body.get('photo_path')
                if not photo_path:
                    return JsonResponse({
                        'error': 'Для загрузки фото укажите photo_path'
                    }, status=400)

                updates = run_in_client(
                    _update_profile_photo, photo_path=photo_path)
                results.update({'photo_upload': updates})

            elif photo_action == 'delete':
                photo_id = body.get('photo_id')
                updates = run_in_client(
                    _delete_profile_photo, photo_id=photo_id)
                results.update({'photo_delete': updates})

            else:
                return JsonResponse({
                    'error': 'Неизвестное действие с фото. Используйте "upload" или "delete"'
                }, status=400)

        # --- Обновление настроек приватности ---
        privacy_settings = body.get('privacy_settings')
        if privacy_settings and isinstance(privacy_settings, dict):
            updates = run_in_client(
                _update_privacy_settings, **privacy_settings)
            results.update({'privacy': updates})

        # Если не было никаких изменений
        if not results:
            return JsonResponse({
                'success': True,
                'message': 'Нет изменений для применения'
            })

        return JsonResponse({
            'success': True,
            'message': 'Профиль успешно обновлен',
            'results': results
        })

    except FileNotFoundError as e:
        return JsonResponse({'error': str(e)}, status=404)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except PhotoInvalidError:
        return JsonResponse({'error': 'Некорректное изображение'}, status=400)
    except AboutTooLongError:
        return JsonResponse({'error': 'Описание слишком длинное (макс 70 символов)'}, status=400)
    except FirstNameInvalidError:
        return JsonResponse({'error': 'Некорректное имя'}, status=400)
    except Exception as e:
        if "last name" in str(e).lower():
            return JsonResponse({'error': 'Некорректная фамилия'}, status=400)
        logger.exception("Ошибка при обновлении профиля")
        return JsonResponse({
            'error': 'Внутренняя ошибка при обновлении профиля',
            'details': str(e)
        }, status=500)

# ==================== Дополнительные функции ====================


@login_required
@csrf_exempt
def update_profile_picture(request):
    """
    DEPRECATED: Используйте update_profile с photo_action='upload'
    """
    if request.method != 'POST':
        return JsonResponse({"error": "Only POST allowed"}, status=405)

    try:
        # Поддержка multipart/form-data для загрузки файлов
        if 'photo' in request.FILES:
            photo = request.FILES['photo']
            # Сохраняем временный файл
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(photo.name)[1]) as tmp:
                for chunk in photo.chunks():
                    tmp.write(chunk)
                tmp_path = tmp.name

            try:
                data = run_in_client(_update_profile_photo, tmp_path)
                return JsonResponse({
                    'success': True,
                    'message': 'Фото профиля обновлено',
                    'data': data
                })
            finally:
                # Удаляем временный файл
                try:
                    os.unlink(tmp_path)
                except:
                    pass
        else:
            return JsonResponse({'error': 'Фото не предоставлено'}, status=400)

    except Exception as e:
        logger.exception("Ошибка при обновлении фото профиля")
        return JsonResponse({'error': str(e)}, status=500)
