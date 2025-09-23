# -*- coding: utf-8 -*-
# quadridigest/fetcher.py
"""
Безопасный сбор истории Telegram-каналов с троттлингом и обработкой FloodWait.
Файл самодостаточный и готов к замене 1-в-1.

Ключевые моменты:
- Отдельный семафор для вызовов истории (GetHistory / get_messages / iter_messages).
- Корректная обработка FloodWaitError с джиттером и верхней отсечкой ожидания.
- НИКАКИХ лямбд в await (исправляет "object function can't be used in 'await' expression").
- Таймаут и ретраи на канал поверх порционной выборки.
- Очередь с понижением приоритета (опционально) для большого числа каналов.

Публичный API (совместим с предыдущими версиями):
    async def fetch_messages_chunk(client, entity, *, limit=80, offset_id=0) -> Sequence
    async def fetch_channel_history_safe(client, entity, *, total_limit=120, step=80,
                                         per_channel_timeout=None, retries=2) -> Sequence
    async def fetch_many_channels_queued(client, entities, *, total_limit_per_channel=120,
                                         step=80, flood_threshold=15) -> Sequence[Tuple[entity, msgs]]
    async def safe_get_messages(client, entity, limit=80, offset_id=0) -> Sequence   # алиас
"""
from __future__ import annotations

import os
import asyncio
import random
import logging
from typing import Any, Sequence, Tuple, Optional

# ---------------------------
# Логгер
# ---------------------------
try:
    from .log import logger  # type: ignore
except Exception:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger("QuadriDigest")

# ---------------------------
# Telethon
# ---------------------------
from telethon import TelegramClient
from telethon.errors import FloodWaitError

# ---------------------------
# ENV helpers
# ---------------------------
def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except Exception:
        return default

def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")

# ---------------------------
# Параметры
# ---------------------------
HISTORY_CONCURRENCY = max(1, _env_int("HISTORY_CONCURRENCY", 2))
FLOOD_CAP_SECONDS   = _env_int("FLOOD_CAP_SECONDS", 60)
PER_CHANNEL_TIMEOUT = _env_int("PER_CHANNEL_TIMEOUT", 60)
PER_CHANNEL_RETRIES = _env_int("PER_CHANNEL_RETRIES", 2)
FLOOD_JITTER_MAX    = _env_int("FLOOD_JITTER_MAX", 3)

_history_sem = asyncio.Semaphore(HISTORY_CONCURRENCY)

# ---------------------------
# FloodWait helper
# ---------------------------
async def _with_floodwait(coro_func, *, cap_seconds: int = FLOOD_CAP_SECONDS):
    """
    Принять async callable без аргументов (возвращает корутину), выполнить и,
    если поймали FloodWaitError, подождать и повторить один раз.
    """
    try:
        return await coro_func()
    except FloodWaitError as e:
        seconds = int(getattr(e, "seconds", 1))
        delay = min(seconds, cap_seconds) + random.randint(1, FLOOD_JITTER_MAX)
        logger.info(f"FloodWait {seconds}s -> sleep {delay}s")
        await asyncio.sleep(delay)
        return await coro_func()

# ---------------------------
# Порционная выборка
# ---------------------------
async def fetch_messages_chunk(
    client: TelegramClient,
    entity: Any,
    *,
    limit: int = 80,
    offset_id: int = 0,
) -> Sequence[Any]:
    """
    Получить одну порцию сообщений (шаг истории) под семафором истории и с защитой от FLOOD.
    """
    async def _call():
        return await client.get_messages(entity, limit=limit, add_offset=0, offset_id=offset_id)

    async with _history_sem:
        return await _with_floodwait(_call)

# ---------------------------
# Полная история (до total_limit)
# ---------------------------
async def fetch_channel_history_safe(
    client: TelegramClient,
    entity: Any,
    *,
    total_limit: int = 120,
    step: int = 80,
    per_channel_timeout: Optional[int] = None,
    retries: int = PER_CHANNEL_RETRIES,
) -> Sequence[Any]:
    """
    Собрать историю канала порциями (step) до total_limit.
    На весь канал действует таймаут и число ретраев.
    """
    per_channel_timeout = per_channel_timeout or PER_CHANNEL_TIMEOUT
    step = max(1, min(step, total_limit))

    async def _do():
        out = []
        offset_id = 0
        while len(out) < total_limit:
            batch = await fetch_messages_chunk(
                client, entity,
                limit=min(step, total_limit - len(out)),
                offset_id=offset_id
            )
            if not batch:
                break
            out.extend(batch)
            last = batch[-1]
            offset_id = getattr(last, "id", 0) or 0
            if offset_id <= 0:
                break
        return out

    attempt = 0
    while True:
        try:
            return await asyncio.wait_for(_do(), timeout=per_channel_timeout)
        except asyncio.TimeoutError:
            attempt += 1
            logger.warning(f"[fetch] timeout on {getattr(entity, 'username', entity)} (attempt {attempt}/{retries})")
            if attempt > retries:
                return []
        except FloodWaitError as e:
            # подстрахуемся: редко, но может проскочить извне
            seconds = int(getattr(e, "seconds", 1))
            delay = min(seconds, FLOOD_CAP_SECONDS) + random.randint(1, FLOOD_JITTER_MAX)
            logger.info(f"[fetch] FloodWait {seconds}s on {getattr(entity, 'username', entity)} -> sleep {delay}s")
            await asyncio.sleep(delay)
        except Exception as e:
            attempt += 1
            logger.warning(f"[fetch] error on {getattr(entity, 'username', entity)}: {e} (attempt {attempt}/{retries})")
            if attempt > retries:
                return []

# ---------------------------
# Очередь каналов (опционально)
# ---------------------------
async def fetch_many_channels_queued(
    client: TelegramClient,
    entities: Sequence[Any],
    *,
    total_limit_per_channel: int = 120,
    step: int = 80,
    flood_threshold: int = 15,
) -> Sequence[Tuple[Any, Sequence[Any]]]:
    """
    Очередь каналов с понижением приоритета при большом FloodWait.
    Возвращает список (entity, messages).
    """
    from collections import deque
    q = deque(entities)
    results = []

    while q:
        entity = q.popleft()
        try:
            msgs = await fetch_channel_history_safe(
                client, entity,
                total_limit=total_limit_per_channel,
                step=step,
            )
            results.append((entity, msgs or []))
        except FloodWaitError as e:
            seconds = int(getattr(e, "seconds", 1))
            if seconds > flood_threshold:
                logger.info(f"FloodWait {seconds}s on {getattr(entity, 'username', entity)} -> requeue")
                q.append(entity)
            else:
                delay = min(seconds, FLOOD_CAP_SECONDS) + random.randint(1, FLOOD_JITTER_MAX)
                logger.info(f"FloodWait {seconds}s on {getattr(entity, 'username', entity)} -> sleep {delay}s")
                await asyncio.sleep(delay)
        except Exception as e:
            logger.warning(f"[fetch] unexpected error on {getattr(entity, 'username', entity)}: {e}")
    return results

# ---------------------------
# Совместимость
# ---------------------------
async def safe_get_messages(client: TelegramClient, entity: Any, limit: int = 80, offset_id: int = 0):
    """Алиас для совместимости со старым кодом."""
    return await fetch_messages_chunk(client, entity, limit=limit, offset_id=offset_id)
