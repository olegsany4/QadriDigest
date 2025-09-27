# quadridigest/fetch/sources.py
"""
Источники QadriDigest — расширенная, самодостаточная версия (500+ строк) с обратной совместимостью.

Цели:
  1) Полная совместимость со старым `main.py`:
     - `read_sources(window=...)` → возвращает объекты с `.text`;
     - дополнительные шымы: `topic_for(source)`, `iter_sources()`, `iter_topic_sources()`.
  2) Современная реализация загрузки/валидации источников и асинхронного сбора через Telethon.
  3) Dry-run «песочница» через `data/seed_messages.txt` с поддержкой тегов `[topic=...] [source=...]`.

Что внутри:
  • DATA_DIR: по умолчанию вычисляется как ../.. /data от этого файла; можно переопределить env `QD_DATA_DIR`.
  • Конфиги: data/sources.json (обяз.), data/sources_aliases.json (опц.), data/sources_bad.json (опц.).
  • Состояние: data/state.json — хранит last_id_per_source (атомарная запись).
  • Валидатор username/ID/URL; алиасы/blacklist; аккуратные JSON-утилиты.
  • Асинхронный пайплайн Telethon: resolve_source(), fetch_window(), iter_raw_posts().
  • CLI: --check, --repair, --print, --selftest, --explain.

ПРИМЕЧАНИЕ ПРО СОВМЕСТИМОСТЬ
----------------------------
Если ваш старый код ожидает ровно `for raw in read_sources(window=...)`, этот файл сохранит поведение
и вернёт список объектов, у которых есть `.text` (и дополнительные поля `.source`, `.message_id`).

Для dry-run без Telethon `read_sources()` использует `data/seed_messages.txt`. В нём можно ставить теги:
  [topic=trading] [source=bitkogan_hotline] Рубль укрепился...
Теги опциональны — если их нет, `(topic, source)` подставятся циклически из `data/sources.json`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple, Union

# ---------------------------------------------------------------------------
# ЛОГИРОВАНИЕ
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# ПУТИ И КОНСТАНТЫ
# ---------------------------------------------------------------------------
# Корневой DATA_DIR по умолчанию: ../.. /data (относительно этого файла).
DATA_DIR = Path(os.getenv("QD_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
SOURCES_PATH = DATA_DIR / "sources.json"
ALIASES_PATH = DATA_DIR / "sources_aliases.json"
BAD_PATH     = DATA_DIR / "sources_bad.json"
STATE_PATH   = DATA_DIR / "state.json"

USERNAME_RE = re.compile(r"^[A-Za-z][\w\d]{3,30}[A-Za-z\d]$")
URL_RE      = re.compile(r"(https?://[^\s)]+)")

# ---------------------------------------------------------------------------
# МОДЕЛИ
# ---------------------------------------------------------------------------
class MsgType(str, Enum):
    TEXT = "text"
    LINK = "link"
    # возможно расширение: PHOTO="photo", DOCUMENT="document", и т.д.

@dataclass
class RawPost:
    text: str
    source: str
    message_id: int
    url: Optional[str] = None
    topic: Optional[str] = None

@dataclass
class _LegacyRaw:
    # Минимальный набор для старых пайплайнов: .text обязателен.
    text: str
    source: str = ""
    message_id: int = 0
    url: Optional[str] = None

@dataclass
class SourcesConfig:
    topics: Dict[str, List[str]] = field(default_factory=dict)  # topic -> [sources]
    aliases: Dict[str, str]      = field(default_factory=dict)  # alias -> canonical
    bad: Set[str]                = field(default_factory=set)   # источники, которые надо игнорировать

    def all_sources(self) -> List[str]:
        acc: List[str] = []
        for lst in self.topics.values():
            acc.extend(lst)
        return acc

# ---------------------------------------------------------------------------
# JSON УТИЛИТЫ
# ---------------------------------------------------------------------------
def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read JSON {path}: {e}")
        return default

def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

# ---------------------------------------------------------------------------
# ВАЛИДАЦИЯ / ПРЕОБРАЗОВАНИЕ ИСТОЧНИКОВ
# ---------------------------------------------------------------------------
def is_plausible_username(s: str) -> bool:
    """
    Допускаем:
      - username: 'name' (или '@name', но '@' обрежется позже)
      - numeric id: '123456789'
      - ссылки: 'https://t.me/...', 'tg://...'
    """
    if not s:
        return False
    s = str(s).lstrip("@").strip()
    if s.isdigit():
        return True
    if s.startswith(("http://", "https://", "t.me/", "tg://")):
        return True
    return bool(USERNAME_RE.fullmatch(s))

def _load_aliases(path: Path = ALIASES_PATH) -> Dict[str, str]:
    data = _read_json(path, {})
    fixed: Dict[str, str] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, str):
            fixed[k.strip()] = v.strip()
    return fixed

def _load_bad(path: Path = BAD_PATH) -> Set[str]:
    data = _read_json(path, [])
    fixed: Set[str] = set()
    for x in data:
        try:
            fixed.add(str(x).lstrip("@").strip())
        except Exception:
            pass
    return fixed

def _apply_aliases_and_bad(topics: Dict[str, List[str]], aliases: Dict[str, str], bad: Set[str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for topic, lst in topics.items():
        if not isinstance(lst, list):
            continue
        acc: List[str] = []
        for src in lst:
            name = str(src).lstrip("@").strip()
            name = aliases.get(name, name)
            if name in bad:
                logger.warning(f"Skip bad source: {name}")
                continue
            acc.append(name)
        out[str(topic)] = acc
    return out

# ---------------------------------------------------------------------------
# ЗАГРУЗКА КОНФИГА / СОСТОЯНИЯ
# ---------------------------------------------------------------------------
def load_sources_config(sources_path: Path = SOURCES_PATH) -> SourcesConfig:
    """
    Возвращает объект конфигурации с уже применёнными алиасами и blacklist.
    """
    if not sources_path.exists():
        raise FileNotFoundError(f"Sources file not found: {sources_path}")
    raw = _read_json(sources_path, {})
    topics: Dict[str, List[str]] = {}
    for topic, lst in raw.items():
        if isinstance(lst, list):
            topics[topic] = [str(x).lstrip("@").strip() for x in lst if str(x).strip()]
    aliases = _load_aliases()
    bad = _load_bad()
    topics = _apply_aliases_and_bad(topics, aliases, bad)
    return SourcesConfig(topics=topics, aliases=aliases, bad=bad)

def load_sources(path: Path = SOURCES_PATH) -> Dict[str, List[str]]:
    """
    Старый контракт: просто словарь topic -> [sources].
    """
    return load_sources_config(path).topics

def load_state(path: Path = STATE_PATH) -> dict:
    state = _read_json(path, {"last_id_per_source": {}, "seen_hashes": []})
    state.setdefault("last_id_per_source", {})
    state.setdefault("seen_hashes", [])
    return state

def save_state(state: dict, path: Path = STATE_PATH) -> None:
    _write_json_atomic(path, state)

# ---------------------------------------------------------------------------
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ СОВМЕСТИМОСТИ (старый API может ожидать эти функции)
# ---------------------------------------------------------------------------
def topic_for(source: str, sources_map: Optional[Dict[str, List[str]]] = None) -> Optional[str]:
    """
    Возвращает тему для данного source согласно конфигу (последняя применённая).
    """
    if sources_map is None:
        sources_map = load_sources()
    name = str(source).lstrip("@").strip()
    for topic, lst in sources_map.items():
        if name in lst:
            return topic
    return None

def iter_sources(sources_map: Optional[Dict[str, List[str]]] = None) -> Iterator[str]:
    """
    Итератор по всем источникам плоским списком.
    """
    if sources_map is None:
        sources_map = load_sources()
    for lst in sources_map.values():
        for s in lst:
            yield s

def iter_topic_sources(sources_map: Optional[Dict[str, List[str]]] = None) -> Iterator[Tuple[str, str]]:
    """
    Итератор (topic, source) по конфигу.
    """
    if sources_map is None:
        sources_map = load_sources()
    for topic, lst in sources_map.items():
        for s in lst:
            yield (topic, s)

# ---------------------------------------------------------------------------
# TELETHON (ленивые импорты)
# ---------------------------------------------------------------------------
async def resolve_source(client, src: str):
    """
    @username / numeric ID / t.me/* / invite → entity | None
    """
    from telethon.errors import UsernameInvalidError, UsernameNotOccupiedError, UsernameOccupiedError, RpcError
    try:
        if str(src).isdigit():
            return await client.get_entity(int(src))
        if str(src).startswith(("http://", "https://")) or "t.me/" in str(src) or str(src).startswith("tg://"):
            return await client.get_entity(str(src))
        if not is_plausible_username(src):
            logger.warning(f"quadridigest.fetch.sources: Skip invalid-looking username: {src}")
            return None
        return await client.get_entity(str(src))
    except (UsernameInvalidError, UsernameNotOccupiedError, UsernameOccupiedError) as e:
        logger.warning(f"quadridigest.fetch.sources: Failed to fetch from {src}: {e}")
        return None
    except RpcError as e:
        logger.warning(f"quadridigest.fetch.sources: Telethon RPC error for {src}: {e}")
        return None
    except Exception as e:
        logger.warning(f"quadridigest.fetch.sources: Unexpected error for {src}: {e}")
        return None

async def fetch_window(client, entity, source_name: str, window: int, types: Tuple[str, ...]) -> List[RawPost]:
    """
    Забирает последние `window` сообщений у entity и фильтрует по типам ('text','link').
    """
    from telethon.tl.types import Message
    posts: List[RawPost] = []
    async for m in client.iter_messages(entity, limit=window):
        if not isinstance(m, Message):
            continue
        text = (m.message or "").strip()
        if not text:
            continue
        has_link = bool(URL_RE.search(text))
        if MsgType.TEXT.value in types and not has_link:
            posts.append(RawPost(text=text, source=source_name, message_id=m.id))
        if MsgType.LINK.value in types and has_link:
            u = URL_RE.search(text).group(1) if URL_RE.search(text) else None
            posts.append(RawPost(text=text, source=source_name, message_id=m.id, url=u))
    return posts

async def iter_raw_posts(client, window: int = 100, types: Tuple[str, ...] = (MsgType.TEXT.value, MsgType.LINK.value)) -> Iterable[RawPost]:
    """
    Итератор по источникам согласно конфигу, с учётом state.json (антидубли по last_id_per_source).
    """
    cfg = load_sources_config()
    state = load_state()
    last_id_map: Dict[str, int] = state.get("last_id_per_source", {})
    all_posts: List[RawPost] = []
    for topic, src_list in cfg.topics.items():
        logger.info(f"[iter] topic={topic} sources={len(src_list)}")
        for src in src_list:
            ent = await resolve_source(client, src)
            if not ent:
                continue
            try:
                posts = await fetch_window(client, ent, src, window=window, types=types)
            except Exception as e:
                logger.warning(f"[iter] fetch_window failed for {src}: {e}")
                continue
            last_id = int(last_id_map.get(src, 0))
            new_posts = [p for p in posts if p.message_id > last_id]
            if not new_posts:
                continue
            max_id = max(p.message_id for p in new_posts)
            last_id_map[src] = max_id
            for p in new_posts:
                p.topic = topic
            all_posts.extend(new_posts)
    state["last_id_per_source"] = last_id_map
    save_state(state)
    logger.info(f"[iter] produced={len(all_posts)}")
    return all_posts

# ---------------------------------------------------------------------------
# read_sources() — СОВМЕСТИМЫЙ ИСТОЧНИК ДЛЯ СТАРЫХ ПАЙПЛАЙНОВ (seed с тегами)
# ---------------------------------------------------------------------------
def _load_seed_messages(seed_path: Path) -> List[_LegacyRaw]:
    """
    Поддерживает теги в начале строки:
      [topic=trading] [source=bitkogan_hotline] Текст поста...
    Можно указывать один/оба. Если тегов нет — будут заполнены позже по конфигу.
    """
    msgs: List[_LegacyRaw] = []
    tag_re = re.compile(r"^\s*(?:\[(?P<k1>topic|source)=(?P<v1>[^\]]+)\]\s*)?(?:\[(?P<k2>topic|source)=(?P<v2>[^\]]+)\]\s*)?(?P<body>.*)$", re.I)
    try:
        if seed_path.exists():
            for line in seed_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                m = tag_re.match(line)
                if m:
                    k1, v1, k2, v2, body = m.group('k1','v1','k2','v2','body')
                    meta: Dict[str, str] = {}
                    if k1 and v1: meta[k1.lower()] = v1.strip()
                    if k2 and v2: meta[k2.lower()] = v2.strip()
                    post = _LegacyRaw(text=body.strip())
                    # временно прячем topic в url как topic://... (чтобы не ломать модель)
                    if 'topic' in meta:
                        post.url = f"topic://{meta['topic']}"
                    if 'source' in meta:
                        post.source = meta['source']
                    msgs.append(post)
                else:
                    msgs.append(_LegacyRaw(text=line))
    except Exception as e:
        logger.warning(f"read_sources: failed to read {seed_path}: {e}")
    return msgs

def read_sources(path: Union[str, Path, None] = None, **kwargs) -> List[_LegacyRaw]:
    """
    Старый контракт: возвращает список объектов с `.text`.
    Улучшения:
      • Теги в seed: [topic=...] [source=...] Текст...
      • Если тегов нет — пары (topic, source) берём циклически из data/sources.json,
        чтобы корректно отрабатывала маршрутизация по source→topic.
    Лишние kwargs (например, window) принимаются и игнорируются.
    """
    # Определяем каталог данных для seed
    if path is None:
        data_dir = DATA_DIR
    else:
        pth = Path(path)
        data_dir = pth.parent if pth.suffix else pth
    seed = data_dir / "seed_messages.txt"
    msgs = _load_seed_messages(seed)

    # Построим пары (topic, source) из конфигурации
    try:
        topics = load_sources(data_dir / "sources.json")
    except Exception:
        topics = {}
    pairs: List[Tuple[str, str]] = []
    for topic, srcs in topics.items():
        for s in srcs:
            pairs.append((topic, s))
    if not pairs:
        pairs = [("politics", "seed_channel")]  # безопасная заглушка

    # Расставляем topic/source для каждого сообщения
    out: List[_LegacyRaw] = []
    idx = 0
    for m in msgs:
        t_override = None
        if m.url and m.url.startswith("topic://"):
            t_override = m.url.split("://", 1)[1]
            m.url = None
        s_override = m.source.strip() if m.source else None

        topic, source = pairs[idx % len(pairs)]
        idx += 1
        if t_override:
            topic = t_override
        if s_override:
            source = s_override

        # прокинем source наружу — роутер его использует
        m.source = source
        m.message_id = idx or 1
        out.append(m)

    if out:
        logger.info(f"read_sources: loaded {len(out)} seed message(s) from {seed}")
    else:
        logger.warning(f"read_sources: no seed messages found at {seed} — returning empty list (dry-run may be silent)")
    return out

# ---------------------------------------------------------------------------
# CLI УТИЛИТЫ
# ---------------------------------------------------------------------------
def _cli_check(path: str) -> int:
    p = Path(path)
    topics = load_sources(p)
    bad = []
    for topic, lst in topics.items():
        for s in lst:
            if not is_plausible_username(s):
                bad.append((topic, s))
    if bad:
        print("Найдены источники с некорректными именами:")
        for topic, name in bad:
            print(f"  [{topic}] {name} — FAIL (не похоже на валидный username)")
        return 1
    print("OK: все имена выглядят валидно синтаксически.")
    return 0

def _cli_print() -> None:
    cfg = load_sources_config()
    print(json.dumps({
        "DATA_DIR": str(DATA_DIR),
        "SOURCES_PATH": str(SOURCES_PATH),
        "ALIASES_PATH": str(ALIASES_PATH),
        "BAD_PATH": str(BAD_PATH),
        "STATE_PATH": str(STATE_PATH),
        "aliases_count": len(cfg.aliases),
        "bad_count": len(cfg.bad),
        "topics": {k: len(v) for k, v in cfg.topics.items()},
        "sources_total": sum(len(v) for v in cfg.topics.values()),
    }, ensure_ascii=False, indent=2))

def _cli_repair(path: str) -> int:
    pin = Path(path)
    raw = _read_json(pin, {})
    if not raw:
        print(f"Не удалось прочитать {pin}")
        return 2
    aliases = _load_aliases()
    bad = _load_bad()
    fixed: Dict[str, List[str]] = {}
    dropped: List[str] = []
    for topic, lst in raw.items():
        if not isinstance(lst, list):
            continue
        acc: List[str] = []
        for x in lst:
            name = str(x).lstrip("@").strip()
            name = aliases.get(name, name)
            if name in bad or not is_plausible_username(name):
                dropped.append(name); continue
            acc.append(name)
        fixed[topic] = acc
    pout = pin.with_suffix(".repaired.json")
    _write_json_atomic(pout, fixed)
    print(f"Готово: {pout} (исключено {len(dropped)} элементов)")
    return 0

def _cli_selftest() -> int:
    # Синтаксис имён
    assert is_plausible_username("investfundsru")
    assert is_plausible_username("123456789")
    assert is_plausible_username("https://t.me/abc")
    assert not is_plausible_username("ab")
    assert not is_plausible_username("bad name")
    assert not is_plausible_username("bad!name")
    # Алиасы/blacklist
    raw = {"x": ["@alias1", "ok_name", "bad_one"]}
    aliases = {"alias1": "real1"}
    bad = {"bad_one"}
    fixed = _apply_aliases_and_bad(raw, aliases, bad)
    assert fixed["x"] == ["real1", "ok_name"]
    # Seed smoke
    msgs = read_sources()
    assert isinstance(msgs, list)
    for m in msgs:
        assert hasattr(m, "text")
    print("SELFTEST OK")
    return 0

def _cli_explain() -> None:
    print("""    CLI справка:
  --check <path>   — синтаксическая проверка sources.json
  --repair <path>  — ремонт и сохранение *.repaired.json
  --print          — вывести метаданные и счётчики
  --selftest       — быстрые проверки без Telethon
  --explain        — краткая справка по API
Ключевые функции:
  load_sources_config(), load_sources(), read_sources(), iter_raw_posts(), topic_for(), iter_sources()
""")

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser(description="QadriDigest Sources utility (extended)")
    ap.add_argument("--check", metavar="PATH", help="Проверить sources.json на валидность имён")
    ap.add_argument("--repair", metavar="PATH", help="Починить конфиг (сохранить *.repaired.json)")
    ap.add_argument("--print", dest="do_print", action="store_true", help="Вывести метаданные и счётчики")
    ap.add_argument("--selftest", action="store_true", help="Запустить self-tests (без Telethon)")
    ap.add_argument("--explain", action="store_true", help="Показать справку по API")
    args = ap.parse_args()
    try:
        if args.check:
            sys.exit(_cli_check(args.check))
        if args.repair:
            sys.exit(_cli_repair(args.repair))
        if args.do_print:
            _cli_print(); sys.exit(0)
        if args.selftest:
            sys.exit(_cli_selftest())
        if args.explain:
            _cli_explain(); sys.exit(0)
        print("Usage: python sources.py [--check PATH | --repair PATH | --print | --selftest | --explain]")
    except KeyboardInterrupt:
        print("Interrupted."); sys.exit(130)
