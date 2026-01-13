import os
import re
import time
import json
import uuid
import hashlib
import logging
import asyncio
import traceback
from datetime import datetime

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError, SessionPasswordNeededError,
    PhoneCodeExpiredError, PhoneCodeInvalidError
)

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache

from .models import BotSession

# ---------------- logging ----------------
log = logging.getLogger("tg_auth")
if not log.handlers:
    logging.basicConfig(
        level=getattr(settings, "TG_AUTH_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

# ===== Константы/настройки =====

SESSIONS_DIR = "/app/var/telethon_sessions"
# os.makedirs(SESSIONS_DIR, mode=0o770, exist_ok=True)

AUTH_TTL = 300
LOCK_TTL = 60
PHONE_RE = re.compile(r"\D+")

pending_clients: dict[str, "TelegramClient"] = {}

# -------- helpers --------


def _rid() -> str:
    return uuid.uuid4().hex[:8]


def _mask_phone(p: str) -> str:
    digits = PHONE_RE.sub("", p or "")
    return f"+***{digits[-4:]}" if len(digits) >= 4 else "*" * len(digits)


def _mask_code(c: str) -> str:
    return "*" * len(c) if c else ""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _norm_phone(p: str) -> str:
    digits = PHONE_RE.sub("", p or "")
    return f"+{digits}" if digits else ""


def _session_path(phone: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z._-]+", "_", phone)
    return os.path.join(SESSIONS_DIR, safe or "session")


def _last_token_key(phone: str) -> str:
    return f"tg_last_token:{phone}"


def _lock_key(phone: str) -> str:
    return f"tg_lock:{phone}"


def _lock_until_key(phone: str) -> str:
    return f"tg_lock_until:{phone}"


def _get_lock_remaining(phone: str) -> int:
    try:
        ttl = cache.ttl(_lock_key(phone))  # type: ignore
        if isinstance(ttl, int) and ttl >= 0:
            return max(1, ttl)
    except Exception:
        pass
    until = cache.get(_lock_until_key(phone))
    if until:
        try:
            return max(1, int(float(until) - time.time()))
        except Exception:
            return LOCK_TTL
    return LOCK_TTL


def _log_request_basics(rid: str, request):
    """Простое логирование входящего HTTP-запроса"""
    meta = request.META
    ua = meta.get("HTTP_USER_AGENT", "-")
    ip = meta.get("HTTP_X_FORWARDED_FOR", meta.get("REMOTE_ADDR", "-"))
    host = meta.get("HTTP_HOST", "-")
    log.info(
        f"[{rid}] request {request.method} {request.path} host={host} ip={ip} ua={ua}")


def run_async(coro):
    """Запускаем корутину в общем event loop, чтобы Telethon не ломался"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ===== Views =====

# ====== START AUTH ======
@csrf_exempt
@csrf_exempt
def start_auth(request):
    rid = _rid()
    _log_request_basics(rid, request)

    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    try:
        api_id = int(request.POST.get("api_id"))
        api_hash = (request.POST.get("api_hash") or "").strip()
        phone_in = request.POST.get("phone")
        phone = _norm_phone(phone_in)
        name = (request.POST.get("name") or "").strip() or None
    except Exception as e:
        return JsonResponse({"error": f"Bad input: {e}"}, status=400)

    # анти-спам
    if cache.get(_lock_key(phone)):
        return JsonResponse(
            {"error": "FLOOD_WAIT", "retry_after": _get_lock_remaining(phone)},
            status=429
        )

    async def _send_code_async():
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        sent = await client.send_code_request(phone)
        pending_clients[phone] = client
        return sent

    try:
        sent = run_async(_send_code_async())
    except FloodWaitError as e:
        return JsonResponse({"error": "FLOOD_WAIT", "retry_after": e.seconds}, status=429)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

    token = f"tg_auth:{uuid.uuid4().hex}"
    payload = {
        "api_id": api_id,
        "api_hash": api_hash,
        "phone": phone,
        "name": name,
        "phone_code_hash": sent.phone_code_hash,
        "issued_at": time.time(),
    }

    # Сохраняем в кэш Redis
    cache.set(token, json.dumps(payload), timeout=AUTH_TTL)
    cache.set(_last_token_key(phone), token, timeout=AUTH_TTL)
    cache.set(_lock_key(phone), 1, timeout=LOCK_TTL)
    cache.set(_lock_until_key(phone), time.time() + LOCK_TTL, timeout=LOCK_TTL)

    log.info(f"[{rid}] START_AUTH token={token} payload={payload}")

    return JsonResponse({"status": "code_sent", "token": token})


# ====== CONFIRM CODE ======
@csrf_exempt
def confirm_code(request):
    rid = _rid()
    _log_request_basics(rid, request)

    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    body_json = {}
    if request.body and request.content_type and "application/json" in request.content_type:
        try:
            body_json = json.loads(request.body.decode("utf-8"))
        except Exception:
            pass

    token = (request.POST.get("token") or body_json.get("token") or "").strip()
    code_raw = (request.POST.get("code")
                or body_json.get("code") or "").strip()
    code = PHONE_RE.sub("", code_raw)

    log.info(f"[{rid}] CONFIRM_CODE token={token}")
    raw = cache.get(token)
    log.info(f"[{rid}] CONFIRM_CODE cache raw={raw}")

    # if not raw:
    # return JsonResponse({"error": "NO_PENDING_CODE"}, status=400)

    data = json.loads(raw)
    api_id = data["api_id"]
    api_hash = data["api_hash"]
    phone = data["phone"]
    name = data.get("name") or phone
    phone_code_hash = data["phone_code_hash"]

    async def _sign_in_async():
        client = pending_clients.get(phone)
        if not client:
            log.info(
                f"[{rid}] SIGN_IN phone={phone}, code={code}, phone_code_hash={phone_code_hash}")
            raise RuntimeError("NO_PENDING_CODE")
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            me = await client.get_me()
            session_string = client.session.save()
            return me, session_string
        finally:
            pending_clients.pop(phone, None)
            await client.disconnect()

    try:
        me, session_string = run_async(_sign_in_async())
    except PhoneCodeExpiredError:
        return JsonResponse({"error": "PHONE_CODE_EXPIRED"}, status=400)
    except PhoneCodeInvalidError:
        return JsonResponse({"error": "PHONE_CODE_INVALID"}, status=400)
    except SessionPasswordNeededError:
        return JsonResponse({"error": "SESSION_PASSWORD_NEEDED"}, status=400)
    except FloodWaitError as e:
        return JsonResponse({"error": "FLOOD_WAIT", "retry_after": e.seconds}, status=429)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

    bot, _ = BotSession.objects.update_or_create(
        phone=phone,
        defaults={
            "name": name,
            "api_id": api_id,
            "api_hash": api_hash,
            "session_string": session_string,
            "is_active": True,
        },
    )

    # чистим кэш
    cache.delete(token)
    cache.delete(_last_token_key(phone))
    cache.delete(_lock_key(phone))
    cache.delete(_lock_until_key(phone))

    log.info(f"[{rid}] AUTHORIZED phone={phone} bot_session_id={bot.id}")

    return JsonResponse({"status": "authorized", "bot_session_id": bot.id})


# ====== RESEND CODE ======
@csrf_exempt
def resend_code(request):
    """
    Повторная отправка кода
    """
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    body_json = {}
    if request.body and request.content_type and "application/json" in request.content_type:
        try:
            body_json = json.loads(request.body.decode("utf-8"))
        except Exception:
            pass

    token = (request.POST.get("token") or body_json.get("token") or "").strip()
    if not token:
        return JsonResponse({"error": "Missing token"}, status=400)

    raw = cache.get(token)
    if not raw:
        return JsonResponse({"error": "NO_PENDING_CODE"}, status=400)

    data = json.loads(raw)
    phone = data["phone"]
    api_id = data["api_id"]
    api_hash = data["api_hash"]

    if cache.get(_lock_key(phone)):
        return JsonResponse({"error": "FLOOD_WAIT", "retry_after": _get_lock_remaining(phone)}, status=429)

    async def _resend_async():
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        sent = await client.send_code_request(phone)
        await client.disconnect()
        return sent

    try:
        sent = run_async(_resend_async())
    except FloodWaitError as e:
        return JsonResponse({"error": "FLOOD_WAIT", "retry_after": e.seconds}, status=429)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

    data["phone_code_hash"] = sent.phone_code_hash
    cache.set(token, json.dumps(data), AUTH_TTL)
    cache.set(_last_token_key(phone), token, AUTH_TTL)
    cache.set(_lock_key(phone), 1, LOCK_TTL)
    cache.set(_lock_until_key(phone), time.time() + LOCK_TTL, LOCK_TTL)

    return JsonResponse({"status": "resent"})
