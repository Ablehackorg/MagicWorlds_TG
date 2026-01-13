# currency_post.py

import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta, time
from typing import Optional, List, Dict

import pytz
import requests
from bs4 import BeautifulSoup as bs
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from telethon import TelegramClient
from telethon.errors import RPCError, FloodWaitError

from utils.db_utils import get_session
from telegram_client import init_user_client
from entity_resolver import ensure_peer
from models import (
    CurrencyGlobals, CurrencyLocation, CurrencyPair,
    CurrencyRateHistory, BotSession, MainEntity
)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("currency_post")

logging.getLogger('telethon').setLevel(logging.WARNING)

CHECK_INTERVAL = int(os.getenv("CURRENCY_CHECK_INTERVAL", "600"))
TZ = pytz.timezone(os.getenv("TZ", "Europe/Moscow"))
UTC_TZ = pytz.UTC


def _utcnow() -> datetime:
    return datetime.now(UTC_TZ)


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return TZ.localize(dt).astimezone(UTC_TZ)
    return dt.astimezone(UTC_TZ)


def get_month_name(month_num: int) -> str:
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря"
    ]
    if 1 <= month_num <= 12:
        return months[month_num - 1]
    return ""


def _should_publish_today(location: CurrencyLocation, now: datetime, pub_time: time) -> bool:
    if location.last_published:
        last_pub = _ensure_utc(location.last_published)
        if last_pub and last_pub.date() == now.date():
            log.debug(f"Location #{location.id} ({location.name}): already published today")
            return False

    if now.time() < pub_time:
        log.debug(f"Location #{location.id} ({location.name}): publication time not reached ({now.time()} < {pub_time})")
        return False

    return True


async def fetch_rate(session, pair: CurrencyPair) -> Optional[str]:
    url = f"https://www.xe.com/currencyconverter/convert/?Amount=1&From={pair.from_code}&To={pair.to_code}"
    
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        html = bs(response.content, 'html.parser')
        
        el = html.select(".result__BigRate-sc-1bsijpp-1")
        if not el:
            el = html.select('p[class*="result__BigRate"]')
        
        if not el:
            log.warning(f"Rate not found for {pair.from_code}/{pair.to_code} on XE.com")
            return None
        
        text = el[0].text.split(" ")[0].replace(",", "")
        try:
            rate = float(text[:6])
        except ValueError:
            log.error(f"Failed to parse rate for {pair.from_code}/{pair.to_code}: {text}")
            return None
        
        trend = ""
        if pair.last_rate:
            if rate > pair.last_rate:
                trend = "+"
            elif rate < pair.last_rate:
                trend = "-"
        
        pair.last_rate = rate
        pair.last_trend = trend
        
        session.add(CurrencyRateHistory(pair_id=pair.id, rate=rate))
        
        log.debug(f"Rate {pair.from_code}/{pair.to_code}: {rate} {trend}")
        return f"<b>1</b> {pair.from_code} = <b>{rate}</b> {pair.to_code} {trend}"
        
    except requests.RequestException as e:
        log.error(f"Request error to XE.com for {pair.from_code}/{pair.to_code}: {e}")
    except Exception as e:
        log.error(f"Unexpected error fetching rate {pair.from_code}/{pair.to_code}: {e}", exc_info=True)
    
    return None


async def unpin_previous_posts(client: TelegramClient, chat_entity) -> None:
    try:
        unpinned_count = 0
        async for message in client.iter_messages(chat_entity, limit=20):
            if message.pinned:
                try:
                    await client.unpin_message(chat_entity, message.id)
                    unpinned_count += 1
                    log.debug(f"Unpinned message {message.id}")
                except Exception as e:
                    log.warning(f"Failed to unpin message {message.id}: {e}")
        
        if unpinned_count > 0:
            log.info(f"Unpinned {unpinned_count} previous messages")
    except Exception as e:
        log.warning(f"Error unpinning messages: {e}")


def _build_post_text(location: CurrencyLocation, rates_text: List[str], now: datetime) -> str:
    month_name = get_month_name(now.month)
    time_str = now.strftime("%H:%M")
    
    google_link = (
        f"<a href='{location.google_rate_url}'>Google</a>"
        if location.google_rate_url else "Google"
    )
    xe_link = (
        f"<a href='{location.xe_rate_url}'>Xe</a>"
        if location.xe_rate_url else "Xe"
    )
    
    location_name = location.country.name if location.country else location.name
    
    text = (
        f"{location.hashtag} {location.emoji} {location_name}\n\n"
        f"<b>ПОМЕНЯТЬ ВАЛЮТУ</b> можно:\n"
        f"в проверенных обменниках в чате "
        f"<a href='{location.main_chat.link if location.main_chat else '#'}'>"
        f"Деньги Обмен валюты</a>\n"
        f"в отделениях банков (безопасно)\n"
        f"в обменниках <a href='{location.google_rate_url or '#'}'>по стране</a>\n\n\n"
        f"<b>КУРС ВАЛЮТ</b> {google_link}, {xe_link} на сегодня, "
        f"<b>{now.day} {month_name}</b> на <b>{time_str}</b>:\n\n"
        + "\n\n".join(rates_text) + "\n\n\n"
        f"Текущий <b>КУРС НА САЙТАХ</b> банков:\n"
    )
    
    if location.bank_1_url:
        text += f"<a href='{location.bank_1_url}'>Банк-1</a>\n"
    if location.bank_2_url:
        text += f"<a href='{location.bank_2_url}'>Банк-2</a>\n"
    if location.bank_3_url:
        text += f"<a href='{location.bank_3_url}'>Банк-3</a>\n"
    
    if location.safe_exchange:
        text += f"\n<a href='{location.safe_exchange.link}'>{location.name}</a>"
    
    return text


async def _send_message_with_cover(
    client: TelegramClient,
    peer,
    text: str,
    cover_path: Optional[str]
) -> Optional:
    if cover_path and os.path.exists(cover_path):
        try:
            message = await client.send_file(peer, cover_path, caption=text, parse_mode='html')
            log.debug(f"Sent message with cover")
            return message
        except Exception as e:
            log.warning(f"Failed to send with cover, sending without: {e}")
    
    try:
        message = await client.send_message(peer, text, parse_mode='html')
        log.debug(f"Sent text message")
        return message
    except Exception as e:
        log.error(f"Error sending message: {e}")
        return None


async def process_location(
    session,
    client: TelegramClient,
    location: CurrencyLocation,
    globals_obj: CurrencyGlobals
) -> None:
    try:
        now = datetime.now(TZ)
        pub_time = globals_obj.publication_time
        
        if not _should_publish_today(location, now, pub_time):
            return
        
        log.info(f"Processing location #{location.id}: {location.name}")
        
        rates_text = []
        for pair in location.pairs:
            if pair.is_active:
                rate_str = await fetch_rate(session, pair)
                if rate_str:
                    rates_text.append(f"      {rate_str}")
        
        if not rates_text:
            log.warning(f"Location #{location.id} ({location.name}): no rates fetched")
            return
        
        log.info(f"Location #{location.id}: fetched {len(rates_text)} rates")
        
        text = _build_post_text(location, rates_text, now)
        
        try:
            main_chat_peer = await ensure_peer(client, telegram_id=location.main_chat.telegram_id)
        except Exception as e:
            log.error(f"Location #{location.id}: failed to get main chat peer: {e}")
            raise
        
        cover_path = None
        if globals_obj.cover:
            cover_path = f"/app/media/{globals_obj.cover}"
        
        message = await _send_message_with_cover(client, main_chat_peer, text, cover_path)
        
        if not message:
            log.error(f"Location #{location.id}: failed to send message")
            raise Exception("Failed to send message")
        
        log.info(f"Location #{location.id}: message sent (ID: {message.id})")
        
        try:
            await unpin_previous_posts(client, main_chat_peer)
            if globals_obj.pin_main_chat > 0:
                await client.pin_message(main_chat_peer, message.id)
                log.info(f"Location #{location.id}: message pinned")
        except Exception as e:
            log.warning(f"Location #{location.id}: error pinning: {e}")
        
        if location.safe_exchange_id and location.safe_exchange:
            try:
                safe_chat_peer = await ensure_peer(
                    client,
                    telegram_id=location.safe_exchange.telegram_id
                )
                await client.send_message(safe_chat_peer, text, parse_mode='html')
                log.info(f"Location #{location.id}: message sent to safe exchange")
            except Exception as e:
                log.warning(f"Location #{location.id}: error sending to safe exchange: {e}")
        
        location.last_status = 'success'
        location.last_published = now
        location.error_count = 0
        session.commit()
        
        log.info(f"Location #{location.id} ({location.name}): processed successfully")
        
    except FloodWaitError as e:
        log.warning(f"Location #{location.id}: FloodWait {e.seconds} seconds")
        location.last_status = 'error'
        location.error_count += 1
        session.commit()
    except RPCError as e:
        log.error(f"Location #{location.id}: Telegram RPC error: {e}")
        location.last_status = 'error'
        location.error_count += 1
        session.commit()
    except Exception as e:
        log.error(f"Location #{location.id} ({location.name}): processing error: {e}", exc_info=True)
        location.last_status = 'error'
        location.error_count += 1
        session.commit()


async def process_once() -> None:
    try:
        with get_session() as s:
            globals_obj = s.get(CurrencyGlobals, 1)
            if not globals_obj:
                log.info("Plugin not configured. Create CurrencyGlobals record in admin panel.")
                return
            
            if not globals_obj.is_active:
                log.debug("Plugin disabled")
                return
            
            locations = s.execute(
                select(CurrencyLocation).where(CurrencyLocation.is_active == True)
            ).scalars().all()
        
        if not locations:
            log.debug("No active locations to process")
            return
        
        log.info(f"Found {len(locations)} active locations")
        
        bot_groups: Dict[int, List[CurrencyLocation]] = {}
        for loc in locations:
            bot_id = loc.bot_id
            if bot_id not in bot_groups:
                bot_groups[bot_id] = []
            bot_groups[bot_id].append(loc)
        
        log.info(f"Processing {len(bot_groups)} bots")
        
        for bot_id, locs in bot_groups.items():
            with get_session() as s:
                bot_db = s.get(BotSession, bot_id)
                if not bot_db:
                    log.warning(f"Bot #{bot_id} not found, skipping {len(locs)} locations")
                    continue
                
                if not bot_db.is_active:
                    log.debug(f"Bot #{bot_id} inactive, skipping")
                    continue
                
                try:
                    client = init_user_client(bot_db)
                    await client.start()
                    
                    if not await client.is_user_authorized():
                        log.error(f"Bot #{bot_id} not authorized")
                        await client.disconnect()
                        continue
                    
                    log.info(f"Bot #{bot_id} ready")
                    
                    for loc in locs:
                        stmt = select(CurrencyLocation).options(
                            selectinload(CurrencyLocation.pairs),
                            selectinload(CurrencyLocation.main_chat),
                            selectinload(CurrencyLocation.safe_exchange),
                            selectinload(CurrencyLocation.country),
                        ).where(CurrencyLocation.id == loc.id)
                        loc_db = s.execute(stmt).scalar_one_or_none()
                        
                        if loc_db:
                            await process_location(s, client, loc_db, globals_obj)
                        else:
                            log.warning(f"Location #{loc.id} not found in DB")
                    
                    await client.disconnect()
                    log.info(f"Bot #{bot_id}: processing completed")
                    
                except Exception as e:
                    log.error(f"Error working with bot #{bot_id}: {e}", exc_info=True)
                    try:
                        await client.disconnect()
                    except:
                        pass
        
        log.info("Processing cycle completed")
        
    except Exception as e:
        log.error(f"Critical error in process_once: {e}", exc_info=True)


async def run_currency_service() -> None:
    log.info("Currency posting service started")
    
    while True:
        try:
            await process_once()
        except KeyboardInterrupt:
            log.info("Stopped by Ctrl+C")
            break
        except Exception as e:
            log.error(f"Error in main loop: {e}", exc_info=True)
        
        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(run_currency_service())
