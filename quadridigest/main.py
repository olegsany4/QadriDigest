# -*- coding: utf-8 -*-
# quadridigest/main.py
"""
QuadriDigest — полная точка входа (drop-in 1-в-1, расширенная).
Цели:
  • Стабильная публикация коротких дайджестов в 4 канала (Политика / Трейдинг / Инфобез / Личные финансы).
  • Поддержка любых форм целевых каналов: @username / https://t.me/... / numeric id (через кэш access_hash).
  • Отсечение дублей, окно выборки, аккуратная оркестрация, дружелюбные логи.
  • Совместимость с существующими модулями: fetcher.py, digest.py, renderer.py, llm_client.py, dedupe.py.
  • Без reply/спойлеров — «только короткое» + ссылка на первоисточник.

Ключевые возможности:
  1) Автозагрузка .env (если установлен python-dotenv).
  2) Разбор channels.txt с секциями «# --- ... ---» и маппингом на категории.
  3) Окно WINDOW: «hours=N» или «since=ISO8601» (UTC).
  4) Сбор истории через fetcher.fetch_channel_history_safe (лимитер + FloodWait).
  5) Нормализация айтемов: source, text, url, category_hint (+ публичная ссылка при наличии username).
  6) Публикация только коротких анонсов, формируемых LLM (digest.build_posts_short_only).
  7) Антидубли: делегировано digest/dedupe; дополнительно включён простой локальный фильтр по (source+hash(text)).
  8) TARGET_* могут быть @username, t.me/..., или numeric id — резолвим через targets.resolve_target().
  9) DRY_RUN, метрики, информативные логи; планировщик (--schedule "hourly").
 10) Доп CLI: --window, --debug-source, --limit-per-channel, --max-items-per-bucket, --dry-run.
 11) Чёткая валидация API_ID/API_HASH и полезные сообщения об ошибках.

Примечания:
  • Этот файл намеренно «большой» и подробно документирован, чтобы его можно было использовать как единую «точку правды».
  • Все функции разбиты логически по секциям: конфиг, каналы, окно, сбор, публикация, планировщик, CLI.
  • Дополнительный локальный антидубль НЕ мешает логике digest/dedupe, а лишь фильтрует явные повторения в текущем прогоне.
"""

from __future__ import annotations

# ======= Стандартные библиотеки =======
import os
import sys
import re
import signal
import asyncio
import argparse
import logging
import hashlib
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Any, Iterable, Set
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ======= Ранняя загрузка .env (опционально) =======
def _autoload_env() -> None:
    """
    Пытаемся подгрузить .env из корня проекта, если установлен python-dotenv.
    Если пакет не установлен — тихо пропускаем.
    """
    try:
        from dotenv import load_dotenv  # type: ignore
        env_path = Path.cwd() / ".env"
        if env_path.exists():
            load_dotenv(dotenv_path=env_path)
    except Exception:
        # Зависимость не обязательная
        pass

_autoload_env()

# ======= Логирование и TZ =======
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("QuadriDigest")

_TZ = os.getenv("TZ", "UTC")
try:
    import time as _time
    os.environ["TZ"] = _TZ
    if hasattr(_time, "tzset"):
        _time.tzset()
    log.info("Timezone set for logs: %s", _TZ)
except Exception:
    # На Windows tzset может отсутствовать — не критично
    pass

# ======= Внешние зависимости =======
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, User

# ======= Локальные модули (совместимость с разными способами запуска) =======
try:
    from .fetcher import fetch_channel_history_safe
    from .digest import build_posts_short_only
    from .targets import resolve_target
except ImportError:
    # запуск как скрипт из каталога quadridigest
    from fetcher import fetch_channel_history_safe  # type: ignore
    from digest import build_posts_short_only       # type: ignore
    from targets import resolve_target              # type: ignore

# =============================================================================
#                                 УТИЛИТЫ ENV
# =============================================================================
def getenv_str(k: str, default: str = "") -> str:
    v = os.getenv(k)
    return v if v is not None else default

def getenv_int(k: str, default: int) -> int:
    try:
        return int(os.getenv(k, str(default)))
    except Exception:
        return default

def getenv_bool(k: str, default: bool = False) -> bool:
    v = os.getenv(k)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")

# =============================================================================
#                               КАТЕГОРИИ/КОНФИГ
# =============================================================================
CATEGORY_MAP = {
    "ПОЛИТИКА": "politics",
    "ТРЕЙДИНГ": "trading",
    "РЫНКИ": "trading",
    "ИНФОБЕЗ": "infosec",
    "ИНФОРМАЦИОННАЯ БЕЗОПАСНОСТЬ": "infosec",
    "ЛИЧНЫЕ ФИНАНСЫ": "personal",
    "ФИНАНСЫ": "personal",
}
BUCKETS: Tuple[str, ...] = ("politics", "trading", "infosec", "personal")

@dataclass
class AppConfig:
    """
    Конфигурация приложения. Загружается из окружения + CLI overrides.
    """
    api_id: int
    api_hash: str
    session_name: str
    window_expr: str
    max_per_channel: int
    max_items: int
    dry_run: bool

    target_politics: Optional[str]
    target_trading: Optional[str]
    target_infosec: Optional[str]
    target_personal: Optional[str]

    debug_source: Optional[str]

    @classmethod
    def load(cls) -> "AppConfig":
        """
        Загружает значения из .env/окружения. CLI-аргументы применяются позже.
        """
        return cls(
            api_id=getenv_int("API_ID", 0),
            api_hash=getenv_str("API_HASH", ""),
            session_name=getenv_str("SESSION_NAME", "quadridigest_session"),
            window_expr=getenv_str("WINDOW", "hours=1"),
            max_per_channel=getenv_int("MAX_PER_CHANNEL", 80),
            max_items=getenv_int("MAX_ITEMS", 10),
            dry_run=getenv_bool("DRY_RUN", False),
            target_politics=getenv_str("TARGET_POLITICS", "") or None,
            target_trading=getenv_str("TARGET_TRADING", "") or None,
            target_infosec=getenv_str("TARGET_INFOSEC", "") or None,
            target_personal=getenv_str("TARGET_PERSONAL", "") or None,
            debug_source=getenv_str("DEBUG_SOURCE", "") or None,
        )

# =============================================================================
#                            РАЗБОР channels.txt
# =============================================================================
SECTION_RE = re.compile(r"^#\s*---\s*(.*?)\s*---\s*$", re.IGNORECASE)

def parse_channels_file(path: str) -> List[Tuple[str, Optional[str]]]:
    """
    Формат файла:
        # --- СМИ / Политика ---
        rbc_news
        kommersant
        ...
        # --- Инфобез ---
        xaknet_live
        ...
    Возвращает список (идентификатор, category_hint|None).
    """
    cur_cat: Optional[str] = None
    out: List[Tuple[str, Optional[str]]] = []
    if not os.path.exists(path):
        log.warning("channels.txt не найден: %s", path)
        return out

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            s = raw.strip()
            if not s:
                continue
            if s.startswith("# ---"):
                # Пытаемся классифицировать секцию по заголовку
                title = s.strip("#- ").strip().upper()
                detected = None
                for key, val in CATEGORY_MAP.items():
                    if key in title:
                        detected = val
                        break
                cur_cat = detected
                continue
            if s.startswith("#"):
                continue
            ident = s.split("#", 1)[0].strip()
            if ident:
                out.append((ident, cur_cat))
    return out

# =============================================================================
#                               ОКНО ВЫБОРКИ
# =============================================================================
def parse_window(expr: str) -> datetime:
    """
    Превращает WINDOW-строку в UTC-дату сдвига:
      • 'hours=N'  -> now - N часов
      • 'since=ISO8601' -> конкретная дата (если без TZ — считаем UTC)
    """
    now = datetime.now(timezone.utc)
    s = (expr or "").strip()
    if s.startswith("hours="):
        try:
            hrs = int(s.split("=", 1)[1])
        except Exception:
            hrs = 1
        return now - timedelta(hours=hrs)
    if s.startswith("since="):
        iso = s.split("=", 1)[1]
        try:
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return now - timedelta(hours=1)
    return now - timedelta(hours=1)

# =============================================================================
#                         ПОЛЕЗНЫЕ ХЕЛПЕРЫ/ХЭШИ
# =============================================================================
def public_link(entity: Any, msg_id: int) -> Optional[str]:
    """
    Если канал публичный, строим https://t.me/<username>/<id>.
    """
    username = getattr(entity, "username", None)
    return f"https://t.me/{username}/{msg_id}" if username and msg_id else None

def item_fingerprint(source: str, text: str) -> str:
    """
    Локальный «грубый» отпечаток для антидубля в пределах одного прогона.
    """
    base = f"{source}\n{text.strip()}".encode("utf-8", "ignore")
    return hashlib.sha1(base).hexdigest()

async def _resolve_entity(client: TelegramClient, ident: str) -> Optional[Any]:
    """
    Унифицированный резолв источников «на вход»:
    поддержка '@username', 'username', numeric id.
    """
    try:
        return await client.get_entity(ident if ident.startswith("@") else f"@{ident}")
    except Exception:
        try:
            return await client.get_entity(ident)
        except Exception:
            return None

# =============================================================================
#                              СБОР И МАРШРУТИЗАЦИЯ
# =============================================================================
async def collect_items(
    client: TelegramClient,
    channels: List[Tuple[str, Optional[str]]],
    since_dt: datetime,
    max_per_channel: int,
    max_items: int,
    debug_source: Optional[str] = None,
) -> Dict[str, List[Dict]]:
    """
    Возвращает dict[category] = [items], где item = {source, text, url, category_hint}.
    Применяется «мягкий» локальный антидубль на уровне текущего прогона.
    """
    buckets: Dict[str, List[Dict]] = {k: [] for k in BUCKETS}

    # Если указан debug_source — собираем только один канал
    channels_iter = [(debug_source.lstrip("@"), None)] if debug_source else channels

    # Локальный антидубль
    seen: Set[str] = set()

    for ident, hint in channels_iter:
        ent = await _resolve_entity(client, ident)
        if not ent:
            log.debug("[skip] cannot resolve source: %s", ident)
            continue

        msgs = await fetch_channel_history_safe(
            client, ent,
            total_limit=max_per_channel,
            step=min(80, max_per_channel),
        )
        if not msgs:
            continue

        username = getattr(ent, "username", None)
        src = username or getattr(ent, "title", None) or str(ident)

        # Подбираем корзину по подсказке секции; по умолчанию — trading
        cat = (hint or "trading").strip().lower()
        if cat not in buckets:
            cat = "trading"
        bucket = buckets[cat]

        added = 0
        for m in msgs:
            dt = getattr(m, "date", None)
            if not dt:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt < since_dt:
                continue

            text = (getattr(m, "message", None) or "").strip()
            if not text:
                continue

            # Локальный антидубль внутри одного прогона
            fp = item_fingerprint(src, text)
            if fp in seen:
                continue
            seen.add(fp)

            url = public_link(ent, getattr(m, "id", 0)) or ""

            bucket.append({
                "source": src,
                "text": text,
                "url": url,
                "category_hint": cat,
            })
            added += 1
            if added >= max_items:
                break

    # Нормируем размер корзин (перестраховка)
    for k in list(buckets.keys()):
        if len(buckets[k]) > max_items:
            buckets[k] = buckets[k][:max_items]

    return buckets

# =============================================================================
#                                 ПУБЛИКАЦИЯ
# =============================================================================
def _choose_target_for_category(cfg: AppConfig, category: str) -> Optional[str]:
    """
    Подбирает целевой канал по категории.
    Значения берутся из .env: TARGET_POLITICS / TARGET_TRADING / TARGET_INFOSEC / TARGET_PERSONAL
    """
    return {
        "politics": cfg.target_politics,
        "trading": cfg.target_trading,
        "infosec": cfg.target_infosec,
        "personal": cfg.target_personal,
    }.get(category)

async def publish_digest(
    client: TelegramClient,
    cfg: AppConfig,
    by_cat: Dict[str, List[Dict]],
) -> Dict[str, int]:
    """
    Генерация коротких строк через LLM (digest.build_posts_short_only)
    и публикация «только короткое + ссылка» в целевые каналы.
    TARGET_* поддерживают @username / t.me/... / numeric id (через resolve_target()).
    """
    metrics = {k: 0 for k in BUCKETS}

    for cat in BUCKETS:
        items = by_cat.get(cat) or []
        if not items:
            continue

        target = _choose_target_for_category(cfg, cat)
        if not target:
            log.info("[info] target for %s не задан — пропуск публикации", cat)
            continue

        # Генерируем короткие анонсы. Ожидается, что digest.build_posts_short_only
        # вернёт последовательность пар (raw_item, short_text).
        posts = await build_posts_short_only(items)
        if not posts:
            continue

        # Резолвим цель один раз на корзину (эффективней и понятнее в логах).
        peer = await resolve_target(client, target)

        # Публикуем: без reply/спойлеров, просто одна короткая строка.
        for raw_item, short_text in posts:
            if cfg.dry_run:
                log.info("[dry] %s -> %s: %s", cat, target, short_text)
            else:
                await client.send_message(peer, short_text, link_preview=False)
            metrics[cat] += 1

    return metrics

# =============================================================================
#                              КОМАНДНАЯ СТРОКА
# =============================================================================
@dataclass
class RunParams:
    once: bool
    schedule: Optional[str]
    window_override: Optional[str]
    debug_source: Optional[str]
    limit_per_channel: Optional[int]
    max_items_per_bucket: Optional[int]
    dry_run_flag: Optional[bool]

def _parse_cli() -> RunParams:
    """
    Разбор CLI-параметров. Значения здесь могут переопределять .env.
    """
    p = argparse.ArgumentParser(description="QuadriDigest — сбор и публикация коротких дайджестов.")
    p.add_argument("--once", action="store_true", help="Единовременный прогон сейчас")
    p.add_argument("--schedule", type=str, help='Расписание, например "hourly"')
    p.add_argument("--window", type=str, help='Переопределить окно: "hours=N" или "since=ISO"')
    p.add_argument("--debug-source", type=str, help="Собрать только один канал (@username или id)")
    p.add_argument("--limit-per-channel", type=int, help="Предел сообщений на канал (override MAX_PER_CHANNEL)")
    p.add_argument("--max-items-per-bucket", type=int, help="Максимум элементов на ленту (override MAX_ITEMS)")
    p.add_argument("--dry-run", action="store_true", help="Не публиковать, только логировать")
    a = p.parse_args()
    return RunParams(
        once=a.once,
        schedule=a.schedule,
        window_override=a.window,
        debug_source=a.debug_source,
        limit_per_channel=a.limit_per_channel,
        max_items_per_bucket=a.max_items_per_bucket,
        dry_run_flag=a.dry_run,
    )

# =============================================================================
#                             ПЛАНИРОВЩИК/ЦИКЛЫ
# =============================================================================
class GracefulExit(Exception):
    """Сигнал на корректное завершение."""

def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    """
    Устанавливает обработчики SIGINT/SIGTERM, чтобы корректно завершать цикл.
    """
    def _raise(*_):
        raise GracefulExit()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _raise)
        except NotImplementedError:
            # Windows/embedded окружения
            pass

async def run_once(
    cfg: Optional[AppConfig] = None,
    window_override: Optional[str] = None,
    debug_source: Optional[str] = None,
    limit_per_channel: Optional[int] = None,
    max_items_per_bucket: Optional[int] = None,
    dry_run_override: Optional[bool] = None,
) -> Dict[str, int]:
    """
    Единичный прогон: сбор -> LLM короткие -> публикация.
    Возвращает метрики публикации.
    """
    cfg = cfg or AppConfig.load()

    # Валидация ключей Telegram API
    if not cfg.api_id or not cfg.api_hash:
        raise SystemExit(
            "API_ID/API_HASH не заданы. Заполните .env, например:\n"
            "API_ID=...\nAPI_HASH=...\nSESSION_NAME=quadridigest_session\n"
            "Получение ключей: https://my.telegram.org → API development tools"
        )

    # CLI переопределения
    if window_override:
        cfg.window_expr = window_override
    if limit_per_channel:
        cfg.max_per_channel = int(limit_per_channel)
    if max_items_per_bucket:
        cfg.max_items = int(max_items_per_bucket)
    if dry_run_override is True:
        cfg.dry_run = True

    since_dt = parse_window(cfg.window_expr)
    channels_path = os.path.join(os.getcwd(), "channels.txt")
    channels = parse_channels_file(channels_path)
    if not channels:
        log.warning("Список каналов пуст: %s", channels_path)

    # Основной цикл
    async with TelegramClient(cfg.session_name, cfg.api_id, cfg.api_hash) as client:
        by_cat = await collect_items(
            client, channels,
            since_dt=since_dt,
            max_per_channel=cfg.max_per_channel,
            max_items=cfg.max_items,
            debug_source=debug_source or cfg.debug_source,
        )
        metrics = await publish_digest(client, cfg, by_cat)
        log.info("Run OK: %s", metrics)
        return metrics

async def _run_hourly(params: RunParams) -> None:
    """
    Простой планировщик: выполняет run_once() раз в час, пока не придёт сигнал завершения.
    """
    cfg = AppConfig.load()
    while True:
        try:
            await run_once(
                cfg=cfg,
                window_override=params.window_override,
                debug_source=params.debug_source,
                limit_per_channel=params.limit_per_channel,
                max_items_per_bucket=params.max_items_per_bucket,
                dry_run_override=params.dry_run_flag,
            )
        except GracefulExit:
            log.info("Получен сигнал завершения — выходим из планировщика.")
            break
        except Exception as e:
            log.exception("Ошибка в планировщике: %s", e)
        await asyncio.sleep(3600)

# =============================================================================
#                                   MAIN
# =============================================================================
def main() -> None:
    """
    Точка входа. Поддерживает '--once' и '--schedule "hourly"'.
    """
    params = _parse_cli()

    # Немедленный прогон
    if params.once or not params.schedule:
        asyncio.run(run_once(
            window_override=params.window_override,
            debug_source=params.debug_source,
            limit_per_channel=params.limit_per_channel,
            max_items_per_bucket=params.max_items_per_bucket,
            dry_run_override=params.dry_run_flag,
        ))
        return

    # Планировщик
    if params.schedule.strip().lower() == "hourly":
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _install_signal_handlers(loop)
        try:
            loop.run_until_complete(_run_hourly(params))
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
        return

    # Если расписание неизвестно — делаем один прогон
    asyncio.run(run_once(
        window_override=params.window_override,
        debug_source=params.debug_source,
        limit_per_channel=params.limit_per_channel,
        max_items_per_bucket=params.max_items_per_bucket,
        dry_run_override=params.dry_run_flag,
    ))

if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------------
#                              ДОП ПРИМЕЧАНИЯ ДЛЯ РАЗРАБОТЧИКОВ
# ---------------------------------------------------------------------------------
# Этот раздел намеренно оставлен для заметок и последующего расширения.
# Здесь можно добавлять:
#   • Дополнительные режимы публикации (например, кнопки/inline-ссылки).
#   • Тонкую настройку LLM (температура, параметры, ограничения на длину).
#   • Дополнительные источники (RSS/HTTP/внешние API) с унификацией под общий формат item.
#   • Расширенную телеметрию: экспорт метрик в Prometheus/StatsD/лог-агрегатор.
#   • Экспорт состояния последней публикации и/или контрольные точки.
#   • Кеширование результирующих коротких анонсов для ускорения повторных прогонов.
#   • Обработку медиа-вложений (если вдруг понадобится).
# Все эти расширения должны сохранять принцип — короткое остаётся коротким и «верхним».
# ---------------------------------------------------------------------------------
