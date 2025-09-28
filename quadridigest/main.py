
# --- compat helper ---
def _qd_to_full_text_str(val) -> str:
    if isinstance(val, str):
        return val
    for attr in ('full_text','text','content','body'):
        if hasattr(val, attr):
            v = getattr(val, attr)
            if isinstance(v, str):
                return v
    try:
        return str(val)
    except Exception:
        return ''

# --- local compat: enforce 2-arg route_topic_by_source ---
try:
    from quadridigest.llm.client import normalize_topic as _norm_topic
except Exception:
    def _norm_topic(x):
        try:
            s=(x or '').lower()
        except Exception:
            s=''
        if any(k in s for k in ('rbc','bloomberg','finam','bcs','moex','nasdaq','rts','ofz','bond','oil','gas')):
            return 'trading'
        if any(k in s for k in ('kii','ib','infosec','soc','fstec','fsb','ics','ot','кии','асу тп')):
            return 'infosec'
        if any(k in s for k in ('t—ж','т—ж','tinkoff','банк','bank','pf','personal')):
            return 'pf'
        return 'politics'
try:
    from quadridigest.compat_route import route_topic_by_source as _route2
except Exception:
    _route2 = None
try:
    from quadridigest.utils import route_topic_by_source as _legacy_route
except Exception:
    _legacy_route = None

# --- compat: route_topic_by_source supports both (source) and (source, topic) ---
try:
    from quadridigest.utils import route_topic_by_source as _rt_source
except Exception:
    _rt_source = None

from quadridigest.llm.client import normalize_topic as _qd_norm_topic

def route_topic_by_source(source, topic=None):
    """
    Compatibility shim.
    Tries calling underlying implementation with 2 args, then 1 arg.
    Always returns canonical topic in {politics,trading,pf,infosec}.
    """
    if _rt_source:
        # prefer 2-arg call
        try:
            return _qd_norm_topic(_rt_source(source, topic))
        except TypeError:
            # fallback to legacy single-arg signature
            try:
                return _qd_norm_topic(_rt_source(source))
            except Exception:
                pass
        except Exception:
            pass
    # ultimate fallback: return provided topic (normalized) or 'pf'
    return _qd_norm_topic(topic)


# --- compat: safe repo.exists wrapper (in-file) ---


# ----------------- Persistence helper -----------------
def repo_save(repo, rec):
    """
    Try multiple method names to persist a PostRecord without knowing exact repo API.
    Includes explicit handling for common 'upsert' signatures.
    """
    import logging, inspect
    tried = []

    # Prepare dict data if possible (Pydantic or dataclass)
    data = None
    try:
        data = rec.model_dump()
    except Exception:
        try:
            data = dict(rec)
        except Exception:
            data = None

    # 0) Explicit handling for UPsert signatures: (channel, message_id, **fields) or (**data)
    if hasattr(repo, "upsert"):
        tried.append("upsert")
        fn = getattr(repo, "upsert")
        # a) upsert(**data)
        if isinstance(data, dict):
            try:
                return fn(**data)
            except TypeError:
                pass
        # b) upsert(channel, message_id, **rest)
        if isinstance(data, dict) and "channel" in data and "message_id" in data:
            rest = {k: v for k, v in data.items() if k not in ("channel", "message_id")}
            try:
                return fn(data["channel"], data["message_id"], **rest)
            except TypeError:
                pass
            try:
                return fn(channel=data["channel"], message_id=data["message_id"], **rest)
            except TypeError:
                pass
        # c) upsert(rec) as a single object
        try:
            return fn(rec)
        except TypeError:
            pass

    # 1) Generic set of method names
    for name in ("save", "save_post", "add", "add_post", "create", "create_post",
                 "insert", "put", "write", "persist", "store"):
        if hasattr(repo, name):
            tried.append(name)
            fn = getattr(repo, name)
            # 1) object form
            try:
                return fn(rec)
            except TypeError:
                # 2) kwargs form
                if isinstance(data, dict):
                    try:
                        return fn(**data)
                    except Exception:
                        pass
            except Exception:
                pass

    logging.getLogger("QuadriDigest").warning(
        "PostsRepo has no compatible save method (tried: %s) — skipping persist", tried
    )
    return None
def repo_exists(repo, **kwargs) -> bool:
    if hasattr(repo, 'exists'):
        try:
            return bool(repo_exists(repo, **kwargs))
        except Exception:
            pass
    if 'fingerprint' in kwargs and hasattr(repo, 'exists_any_fingerprint'):
        try:
            return bool(repo.exists_any_fingerprint(kwargs['fingerprint']))
        except Exception:
            pass
    if 'fingerprint' in kwargs and hasattr(repo, 'exists_by_fingerprint'):
        try:
            return bool(repo.exists_by_fingerprint(kwargs['fingerprint']))
        except Exception:
            pass
    if 'fingerprint' in kwargs and hasattr(repo, 'get_by_fingerprint'):
        try:
            return repo.get_by_fingerprint(kwargs['fingerprint']) is not None
        except Exception:
            pass
    return False
# ---- .env support ----
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

import argparse
import asyncio
import logging
from quadridigest.compat_route import route_topic_by_source  # compat 2-arg wrapper
from typing import Dict, Iterable, Optional, Tuple

from quadridigest.config import settings

# ---------------- Topic → Channel map (Stage 6) ----------------
import os as _os

TOPIC_TO_CHANNEL = {
    "politics": getattr(settings, "target_politics", None) or _os.getenv("TARGET_POLITICS"),
    "trading":  getattr(settings, "target_trading",  None) or _os.getenv("TARGET_TRADING"),
    "pf":       getattr(settings, "target_pf",       None) or _os.getenv("TARGET_PERSONAL"),
    "infosec":  getattr(settings, "target_infosec",  None) or _os.getenv("TARGET_INFOSEC"),
}

def resolve_channel_for_topic(topic: str) -> str | None:
    ch = TOPIC_TO_CHANNEL.get(topic)
    if ch and isinstance(ch, str) and ch.strip():
        return ch.strip()
    return None
# ----------------------------------------------------------------
from quadridigest.logging import setup_logging
from quadridigest.fetch.sources import read_sources  # expected: Iterable[RawPost]
from quadridigest.fetch.normalize import clean_text
from quadridigest.dedupe.clusters import ClusterService
from quadridigest.llm.client import LLMClient
from quadridigest.format.render import TemplateRenderer
from quadridigest.publish.telethon_client import build_client, start_bot
from quadridigest.publish.publisher import Publisher
from quadridigest.storage.db import init_db
from quadridigest.storage.repositories import PostsRepo
from quadridigest.types import PostRecord
from quadridigest.route.router import route_topic_by_source as route_topic_by_source_router# optional source→topic override
from quadridigest.storage import repositories_patch  # noqa: F401


# ----------------- CLI -----------------

def parse_args():
    ap = argparse.ArgumentParser(description="QadriDigest runner")
    ap.add_argument("--once", action="store_true", help="single run (default behavior)")
    ap.add_argument("--dry-run", action="store_true", help="no telegram publish, log only")
    ap.add_argument("--window", type=int, default=20, help="how many last items to fetch per source")
    ap.add_argument(
        "--stay-alive",
        type=int,
        default=0,
        help="seconds to keep bot online after publish to handle inline buttons (0 = disconnect immediately)",
    )
    return ap.parse_args()


# ----------------- Helpers -----------------

def build_channel_map() -> Dict[str, str]:
    """Topic→channel mapping from settings/.env.
    Supports both TARGET_PF and TARGET_PERSONAL (alias).
    """
    # Some deployments use TARGET_PERSONAL instead of TARGET_PF
    target_pf = getattr(settings, "target_pf", None) or getattr(settings, "target_personal", None) or ""
    mapping: Dict[str, str] = {
        "politics": getattr(settings, "target_politics", ""),
        "trading": getattr(settings, "target_trading", ""),
        "pf": target_pf,
        "infosec": getattr(settings, "target_infosec", ""),
    }
    # Log empty targets
    for k, v in mapping.items():
        if not v:
            logging.warning("TARGET for %s is not set — messages of this topic will be skipped.", k)
    return mapping


def should_skip(repo: PostsRepo, channel: str, fingerprint: str) -> bool:
    if not channel:
        return True
    try:
        if repo_exists(repo, channel=channel, fingerprint=fingerprint):
            logging.info("Dedup: already published fp=%s", fingerprint)
            return True
    except Exception as e:
        logging.warning("Dedup check error (continue): %s", e)
    return False


# ----------------- Pipeline -----------------

async def run_once(dry: bool, window: int = 20, stay_alive: int = 0):
    """One pipeline pass:
      fetch (user session) -> clean -> dedupe -> LLM -> render -> route -> publish (bot)
    Optionally keep bot online for `stay_alive` seconds to handle 'Подробнее' callbacks.
    """
    # Init services
    cluster = ClusterService()
    llm = LLMClient(data_dir=settings.data_dir, prompt_version=settings.prompt_version)
    renderer = TemplateRenderer()
    repo = PostsRepo()

    client = None
    publisher: Optional[Publisher] = None
    channel_map: Optional[Dict[str, str]] = None

    if not dry:
        # Start bot client for publishing
        client = build_client()
        await start_bot(client)
        publisher = Publisher(client)

        # Direct mapping to @usernames from .env (recommended)
        channel_map = build_channel_map()

    try:
        # Read posts via user session fetcher (implemented inside read_sources)
        posts = list(read_sources(window=window))
        logging.info("[fetch] %d posts fetched", len(posts))

        # Sort newest-first if RawPost has 'date' (optional)
        try:
            posts.sort(key=lambda p: getattr(p, "date", 0), reverse=True)  # type: ignore[attr-defined]
        except Exception:
            pass

        published = 0
        for raw in posts:
            source = getattr(raw, "source", "")
            raw_text = getattr(raw, "text", "")
            if not raw_text or len(raw_text.strip()) < 3:
                continue

            # Clean/normalize text
            norm = clean_text(raw_text)

            # Fast pre-dedupe by cluster fingerprint (simhash/blake etc.)
            fp = cluster.fingerprint(norm)
            if repo.exists_any_fingerprint(fp):  # optional convenience
                logging.info("Pre-dedup (cluster fp) skip: %s", fp)
                continue

            # LLM annotation (strict JSON). Model may propose topic; source may override.
            ann = llm.summarize(norm)
            topic = ann.topic
            # optional: override by source profile
            topic = route_topic_by_source(source, topic)

            # Expand to full text (can be used for 'Подробнее')
            full = llm.full(norm, evidence=source)

            # make full_text robust: support both object and string returns

            full_text = full if isinstance(full, str) else getattr(full, 'full_text', str(full) if full is not None else '')
            full = _qd_to_full_text_str(full)

            # Render messages
            short_msg = renderer.render_short(
                topic=topic,
                headline=ann.headline,
                summary=ann.summary,
                evidence=(getattr(ann, "evidence", None) or source or ann.summary),
            )
            full_msg = renderer.render_full(
                topic=topic,
                full_text=full_text,
                evidence=(getattr(ann, "evidence", None) or source or ann.summary),
            )

            # Log the routing decision
            logging.info("Route: source=%s → topic=%s", source, topic)

            if dry:
                logging.info("[DRY] %s → %s", topic, (short_msg.splitlines()[0] if short_msg else ""))
                continue

            assert publisher is not None and channel_map is not None
            channel = channel_map.get(topic, "")

            if should_skip(repo, channel, ann.fingerprint):
                continue

            logging.info("Publish → %s (%s)", topic, channel)
            msg_id = await publisher.post_short(
                channel=channel,
                topic=topic,
                headline=ann.headline,
                summary=ann.summary,
                full_text=full_msg,
                fingerprint=fp,  # use local fp for idempotency
            )

            # Persist record
            rec = PostRecord(
                channel=channel,
                source=source,
                topic=topic,
                headline=ann.headline,
                summary=ann.summary,
                full_text=full_msg,
                fingerprint=ann.fingerprint,
                message_id=str(msg_id),
            )
            repo_save(repo, rec)
            published += 1

        logging.info("Done. published=%d (dry=%s)", published, dry)

        # keep bot online for callbacks if requested
        if not dry and stay_alive > 0 and client is not None:
            try:
                logging.info("Stay alive for %s seconds to handle callbacks…", stay_alive)
                await asyncio.sleep(stay_alive)
            except asyncio.CancelledError:
                pass

    finally:
        if not dry and client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass


def main():
    args = parse_args()
    setup_logging(settings.log_level, settings.data_dir)
    init_db()
    asyncio.run(run_once(dry=args.dry_run, window=args.window, stay_alive=args.stay_alive))


if __name__ == "__main__":
    main()
# === APPEND-ONLY FIX: route_topic_by_source 1-arg/2-arg compatibility ===
# Не трогаем существующие импорты и код выше. Переопределяем имя после всех импортов.
try:
    from quadridigest.llm.client import normalize_topic as _qd_norm_topic
except Exception:
    def _qd_norm_topic(x):
        x = (x or "").strip().lower()
        return x if x in {"politics", "trading", "pf", "infosec"} else "pf"

try:
    # Сохраняем оригинальную реализацию, если она есть
    from quadridigest.utils import route_topic_by_source as _orig_route_topic_by_source
except Exception:
    _orig_route_topic_by_source = None  # type: ignore

def route_topic_by_source(source, topic=None):
    """
    Совместимая обёртка, объявленная В КОНЦЕ ФАЙЛА, чтобы ничто её не перетёрло.
    Поддерживает обе сигнатуры: (source, topic) и (source).
    Всегда возвращает canonical topic ∈ {politics,trading,pf,infosec}.
    """
    if _orig_route_topic_by_source:
        # Пытаемся новой сигнатурой
        try:
            return _qd_norm_topic(_orig_route_topic_by_source(source, topic))
        except TypeError:
            # Легаси: только source
            try:
                return _qd_norm_topic(_orig_route_topic_by_source(source))
            except Exception:
                pass
        except Exception:
            pass
    # Фолбэк: нормализуем переданный topic (может быть None -> станет 'pf')
    return _qd_norm_topic(topic)
# === /APPEND-ONLY FIX ===
# === APPEND-ONLY: safe wrapper for route_topic_by_source ===
def _route_topic_safe(source, topic=None):
    from quadridigest.llm.client import normalize_topic as _norm
    try:
        from quadridigest.utils import route_topic_by_source as _rt
    except Exception:
        _rt = None
    if _rt:
        try:
            return _norm(_rt(source, topic))  # новая сигнатура
        except TypeError:
            try:
                return _norm(_rt(source))      # легаси: только source
            except Exception:
                pass
        except Exception:
            pass
    return _norm(topic)  # фолбэк
# === /APPEND-ONLY ===


# Normalize topic using client helper when available (fallback local)
try:
    from quadridigest.llm.client import normalize_topic as _normalize_topic
except Exception:
    def _normalize_topic(t, source=None):
        t = (t or "").lower().strip()
        if t in {"politics","trading","pf","infosec"}:
            return t
        if "trade" in t or "market" in t or "бирж" in t:
            return "trading"
        if "info" in t or "cyber" in t or "кибер" in t:
            return "infosec"
        if "pf" in t or "personal" in t or "личн" in t or "финанс" in t:
            return "pf"
        return "politics"
