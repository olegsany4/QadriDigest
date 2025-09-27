# -*- coding: utf-8 -*-
"""
QadriDigest — dedupe.clusters
Кластеризация близких постов (перефразов) и сервис для интеграции.

Подход:
1) Грубая группировка по blake64 части (совпадение первых N hex-символов)
2) Проверка по Hamming distance для simhash64 (<= threshold)
3) Опционально — проверка fuzzy-метрикой (rapidfuzz) на нормализованном тексте

Функции:
- cluster_posts(items, ...) -> список кластеров (каждый — список индексов items)
- is_near_duplicate(a, b, ...) -> булево (пара почти-дублей)

Класс:
- ClusterService(engine, ...):
    - add_post_with_guard(channel, message_id, text, created_at=None) -> Optional[int]
      (вставляет запись, если не дубль; иначе None)
    - maybe_duplicate(channel, text) -> Optional[int]
      (ищет id похожего недавнего поста без вставки)
    - is_near_duplicate(a, b, ...) -> bool
"""
from __future__ import annotations

from typing import List, Dict, Tuple, Optional

try:
    from rapidfuzz import fuzz  # type: ignore
    _HAS_RAPIDFUZZ = True
except Exception:
    _HAS_RAPIDFUZZ = False

from .simhash import normalize_text, make_fingerprint

def _hamming64(a_hex: str, b_hex: str) -> int:
    a = int(a_hex, 16)
    b = int(b_hex, 16)
    x = a ^ b
    return x.bit_count()


def is_near_duplicate(
    text_a: str,
    text_b: str,
    simhash_threshold: int = 6,
    fuzzy_threshold: int = 90,
    use_fuzzy: Optional[bool] = None,
) -> bool:
    """
    Возвращает True, если тексты — перефразы одной новости.
    Правило: simhash Hamming <= simhash_threshold.
    Дополнительно: если rapidfuzz доступен и use_fuzzy is None/True —
    применять fuzz.token_set_ratio >= fuzzy_threshold на НОРМализованном тексте.
    """
    norm_a = normalize_text(text_a)
    norm_b = normalize_text(text_b)
    fp_a = make_fingerprint(norm_a)
    fp_b = make_fingerprint(norm_b)
    _, s64_a = fp_a.split(":")
    _, s64_b = fp_b.split(":")
    if _hamming64(s64_a, s64_b) <= simhash_threshold:
        if use_fuzzy is False:
            return True
        if _HAS_RAPIDFUZZ and (use_fuzzy is None or use_fuzzy is True):
            score = fuzz.token_set_ratio(norm_a, norm_b)
            return score >= fuzzy_threshold
        return True
    return False


def cluster_posts(
    items: List[Tuple[str, str]],
    # items: List[(post_id, text)]
    blake_prefix: int = 8,  # по умолчанию совпадение первых 8 hex-символов (32 бита)
    simhash_threshold: int = 6,
    fuzzy_threshold: int = 90,
    use_fuzzy: Optional[bool] = None,
) -> List[List[int]]:
    """
    Возвращает список кластеров, где кластер — список индексов входного массива items.
    Этапы:
    - считаем fingerprint "b64:s64" для каждого текста
    - группируем по префиксу b64[:blake_prefix]
    - внутри группы строим кластеры по simhash (и опционально fuzzy)
    """
    if not items:
        return []

    fps = []
    for (pid, text) in items:
        norm = normalize_text(text)
        fp = make_fingerprint(norm)
        b64, s64 = fp.split(":")
        fps.append((b64, s64, norm))

    buckets: Dict[str, List[int]] = {}
    for i, (b64, s64, norm) in enumerate(fps):
        key = b64[:blake_prefix]
        buckets.setdefault(key, []).append(i)

    clusters: List[List[int]] = []
    visited = set()

    for key, idxs in buckets.items():
        for i in idxs:
            if i in visited:
                continue
            cluster = [i]
            visited.add(i)
            b64_i, s64_i, norm_i = fps[i]
            for j in idxs:
                if j in visited:
                    continue
                b64_j, s64_j, norm_j = fps[j]
                hd = _hamming64(s64_i, s64_j)
                if hd <= simhash_threshold:
                    if _HAS_RAPIDFUZZ and (use_fuzzy is None or use_fuzzy):
                        if fuzz.token_set_ratio(norm_i, norm_j) < fuzzy_threshold:
                            continue
                    cluster.append(j)
                    visited.add(j)
            clusters.append(cluster)

    return clusters


# --------- Сервис-обёртка для совместимости с main.py ---------
class ClusterService:
    """
    Универсальная обёртка над репозиторием, чтобы удобно проверять дубликаты.
    Предоставляет стабильный импорт: `from quadridigest.dedupe.clusters import ClusterService`
    """
    def __init__(
        self,
        engine,
        *,
        use_fuzzy: Optional[bool] = None,
        simhash_threshold: int = 6,
        fuzzy_threshold: int = 90,
        lookback_hours: int = 72,
    ) -> None:
        from ..storage.repositories import PostsRepository  # локальный импорт
        self.repo = PostsRepository(engine)
        self.use_fuzzy = use_fuzzy
        self.simhash_threshold = simhash_threshold
        self.fuzzy_threshold = fuzzy_threshold
        self.lookback_hours = lookback_hours

    def is_duplicate(self, channel: str, text: str) -> bool:
        dup_id = self.repo.maybe_duplicate_by_similarity(
            channel=channel,
            text=text,
            use_fuzzy=self.use_fuzzy,
            fuzzy_threshold=self.fuzzy_threshold,
            simhash_threshold=self.simhash_threshold,
            lookback_hours=self.lookback_hours,
        )
        return dup_id is not None

    def add_post_with_guard(
        self,
        *,
        channel: str,
        message_id: int | None,
        text: str,
        created_at=None,
    ) -> int | None:
        return self.repo.add_post_with_similarity_guard(
            channel=channel,
            message_id=message_id,
            text=text,
            created_at=created_at,
            use_fuzzy=self.use_fuzzy,
            fuzzy_threshold=self.fuzzy_threshold,
            simhash_threshold=self.simhash_threshold,
            lookback_hours=self.lookback_hours,
        )

    def is_near_duplicate(
        self,
        text_a: str,
        text_b: str,
    ) -> bool:
        return is_near_duplicate(
            text_a, text_b,
            simhash_threshold=self.simhash_threshold,
            fuzzy_threshold=self.fuzzy_threshold,
            use_fuzzy=self.use_fuzzy,
        )
# === QadriDigest hotfix: ClusterService.engine -> optional ===
try:
    # локальный импорт, если пакетный
    from ..storage.db import init_db  # type: ignore
except Exception:
    # абсолютный, если модульный запуск
    from quadridigest.storage.db import init_db  # type: ignore

# Заворачиваем существующий __init__ так, чтобы engine по умолчанию брался из init_db()
if 'ClusterService' in globals() and hasattr(ClusterService, '__init__'):
    _qd_old_init = ClusterService.__init__

    def _qd_new_init(self, *args, **kwargs):
        # если engine не передан позиционно и не указан по имени — подставим из init_db()
        has_positional_engine = len(args) >= 1
        if not has_positional_engine and 'engine' not in kwargs:
            kwargs['engine'] = init_db()
        return _qd_old_init(self, *args, **kwargs)

    ClusterService.__init__ = _qd_new_init
# === /hotfix ===
# === QadriDigest hotfix: add ClusterService.fingerprint/normalize ===
try:
    from .simhash import normalize_text, make_fingerprint  # package-relative
except Exception:
    from quadridigest.dedupe.simhash import normalize_text, make_fingerprint  # absolute fallback

if 'ClusterService' in globals():
    # fingerprint(text) -> str: b64:s64 на нормализованном тексте
    if not hasattr(ClusterService, 'fingerprint'):
        def _qd_fingerprint(self, text: str) -> str:
            return make_fingerprint(normalize_text(text))
        ClusterService.fingerprint = _qd_fingerprint  # type: ignore[attr-defined]

    # normalize(text) -> str: чтобы main.py мог использовать явную нормализацию
    if not hasattr(ClusterService, 'normalize'):
        def _qd_normalize(self, text: str) -> str:
            return normalize_text(text)
        ClusterService.normalize = _qd_normalize  # type: ignore[attr-defined]
# === /hotfix ===
# === QadriDigest hotfix: ClusterService.is_duplicate accepts fingerprint or text ===
from datetime import datetime, timedelta
try:
    # пакетный импорт
    from ..storage.db import posts  # type: ignore
except Exception:
    # абсолютный импорт
    from quadridigest.storage.db import posts  # type: ignore

# вспомогалка: это fingerprint формата "b64:s64"?
def _qd_is_fingerprint(val: str) -> bool:
    if not isinstance(val, str) or ":" not in val:
        return False
    left, right = val.split(":", 1)
    if len(left) != 16 or len(right) != 16:
        return False
    hexchars = "0123456789abcdef"
    return all(c in hexchars for c in left.lower()) and all(c in hexchars for c in right.lower())

if 'ClusterService' in globals():
    _old_is_dup = getattr(ClusterService, 'is_duplicate', None)

    def _qd_is_duplicate(self, obj, *, channel: str | None = None, lookback_hours: int | None = None) -> bool:
        """
        Принимает либо текст, либо готовый fingerprint 'b64:s64'.
        Если передан fingerprint — проверяем по нему. Если текст — считаем fingerprint на нормализованном тексте.
        Если channel=None, проверяем по всем каналам; иначе — только в рамках канала.
        """
        # импорт нормализации/отпечатка
        try:
            from .simhash import normalize_text, make_fingerprint  # type: ignore
        except Exception:
            from quadridigest.dedupe.simhash import normalize_text, make_fingerprint  # type: ignore

        # определить fingerprint
        if isinstance(obj, str) and _qd_is_fingerprint(obj):
            fp = obj
        else:
            fp = make_fingerprint(normalize_text(str(obj)))

        # окно поиска
        lb_hours = lookback_hours if lookback_hours is not None else getattr(self, 'lookback_hours', 72)
        cutoff = datetime.utcnow() - timedelta(hours=lb_hours)

        # прямой запрос в БД
        from sqlalchemy import select, and_
        with self.repo.engine.begin() as conn:  # type: ignore[attr-defined]
            conds = [posts.c.fingerprint == fp, posts.c.created_at >= cutoff]
            if channel is not None:
                conds.append(posts.c.channel == channel)
            row = conn.execute(
                select(posts.c.id).where(and_(*conds)).limit(1)
            ).first()
            return row is not None

    # подменяем/добавляем метод
    ClusterService.is_duplicate = _qd_is_duplicate  # type: ignore[attr-defined]
# === /hotfix ===
# === QadriDigest hotfix: ClusterService.find_or_create(fp, channel) ===
from datetime import datetime, timedelta
try:
    from ..storage.db import posts  # type: ignore
except Exception:
    from quadridigest.storage.db import posts  # type: ignore

def _qd_is_fp(val: str) -> bool:
    if not isinstance(val, str) or ":" not in val:
        return False
    left, right = val.split(":", 1)
    if len(left) != 16 or len(right) != 16:
        return False
    hexchars = "0123456789abcdef"
    return all(c in hexchars for c in left.lower()) and all(c in hexchars for c in right.lower())

if 'ClusterService' in globals():
    if not hasattr(ClusterService, 'find_or_create'):
        def _qd_find_or_create(self, fingerprint: str, channel: str, *, lookback_hours: int | None = None):
            """
            Поиск записи с (channel, fingerprint). Возвращает id, если найдено.
            Ничего не создаёт (у нас нет текста для вставки) — только find.
            Если нужно создание — вызывающая сторона должна делать insert через репозиторий.
            """
            if not _qd_is_fp(fingerprint):
                # если пришёл текст — сконвертируем в fp
                try:
                    from .simhash import normalize_text, make_fingerprint  # type: ignore
                except Exception:
                    from quadridigest.dedupe.simhash import normalize_text, make_fingerprint  # type: ignore
                fingerprint = make_fingerprint(normalize_text(str(fingerprint)))

            lb_hours = lookback_hours if lookback_hours is not None else getattr(self, 'lookback_hours', 72)
            cutoff = datetime.utcnow() - timedelta(hours=lb_hours)

            from sqlalchemy import select, and_
            with self.repo.engine.begin() as conn:  # type: ignore[attr-defined]
                row = conn.execute(
                    select(posts.c.id).where(
                        and_(
                            posts.c.channel == channel,
                            posts.c.fingerprint == fingerprint,
                            posts.c.created_at >= cutoff,
                        )
                    ).limit(1)
                ).first()
                return row[0] if row else None

        ClusterService.find_or_create = _qd_find_or_create  # type: ignore[attr-defined]
# === /hotfix ===
# === QadriDigest hotfix: is_near_duplicate uses SIMHASH OR FUZZY ===
try:
    from rapidfuzz import fuzz  # type: ignore
    _QD_HAS_RAPIDFUZZ = True
except Exception:
    _QD_HAS_RAPIDFUZZ = False

def _qd_hamming64(a_hex: str, b_hex: str) -> int:
    a = int(a_hex, 16); b = int(b_hex, 16)
    return (a ^ b).bit_count()

def _qd_is_near_duplicate(
    text_a: str,
    text_b: str,
    simhash_threshold: int = 6,
    fuzzy_threshold: int = 90,
    use_fuzzy: bool | None = True,
) -> bool:
    # импорт из локального/абсолютного путей
    try:
        from .simhash import normalize_text, make_fingerprint  # type: ignore
    except Exception:
        from quadridigest.dedupe.simhash import normalize_text, make_fingerprint  # type: ignore

    norm_a = normalize_text(text_a or "")
    norm_b = normalize_text(text_b or "")
    _, s64_a = make_fingerprint(norm_a).split(":")
    _, s64_b = make_fingerprint(norm_b).split(":")

    # 1) SIMHASH близость
    if _qd_hamming64(s64_a, s64_b) <= simhash_threshold:
        return True

    # 2) FUZZY близость (ИЛИ-правило)
    if _QD_HAS_RAPIDFUZZ and (use_fuzzy is None or use_fuzzy is True):
        score = fuzz.token_set_ratio(norm_a, norm_b)
        if score >= fuzzy_threshold:
            return True

    return False

# Переопределяем модульную функцию (если была)
is_near_duplicate = _qd_is_near_duplicate  # type: ignore

# И метод сервиса — если класс уже определён
if 'ClusterService' in globals() and not getattr(ClusterService, '_qd_isdup_patched', False):
    def _qd_service_is_near(self, a: str, b: str) -> bool:
        return _qd_is_near_duplicate(
            a, b,
            simhash_threshold=getattr(self, 'simhash_threshold', 6),
            fuzzy_threshold=getattr(self, 'fuzzy_threshold', 90),
            use_fuzzy=getattr(self, 'use_fuzzy', True),
        )
    ClusterService.is_near_duplicate = _qd_service_is_near  # type: ignore
    ClusterService._qd_isdup_patched = True  # type: ignore
# === /hotfix ===
