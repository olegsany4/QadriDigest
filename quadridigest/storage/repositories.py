# -*- coding: utf-8 -*-
"""
QadriDigest — storage.repositories
Репозиторий для постов: idempotent-вставка с учётом отпечатка и дедуп.
Добавлен класс-совместимость PostsRepo (историческое имя).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import select, insert, text, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..dedupe.simhash import make_fingerprint, normalize_text
from ..dedupe.clusters import is_near_duplicate
from .db import posts, ensure_indexes


class PostsRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
        ensure_indexes(self.engine)

    def add_post_if_new(
        self,
        *,
        channel: str,
        message_id: Optional[int],
        text: str,
        created_at: Optional[dt.datetime] = None,
        use_fuzzy: Optional[bool] = None,
        fuzzy_threshold: int = 90,
        simhash_threshold: int = 6,
        lookback_hours: int = 72,
    ) -> Optional[int]:
        """
        Быстрая попытка вставки по (channel, fingerprint). Возвращает id или None при дубле.
        """
        norm = normalize_text(text)
        fingerprint = make_fingerprint(norm)
        created_at = created_at or dt.datetime.utcnow()

        with Session(self.engine) as s:
            try:
                res = s.execute(
                    insert(posts)
                    .values(
                        channel=channel,
                        message_id=message_id,
                        text=text,
                        norm_text=norm,
                        fingerprint=fingerprint,
                        created_at=created_at,
                    )
                    .returning(posts.c.id)
                )
                new_id = res.scalar_one()
                s.commit()
                return new_id
            except IntegrityError:
                s.rollback()
                return None

    def maybe_duplicate_by_similarity(
        self,
        *,
        channel: str,
        text: str,
        use_fuzzy: Optional[bool] = None,
        fuzzy_threshold: int = 90,
        simhash_threshold: int = 6,
        lookback_hours: int = 72,
    ) -> Optional[int]:
        """
        Ищем среди последних lookback_hours часов по каналу похожий пост.
        Возвращает id дубля или None.
        """
        norm = normalize_text(text)
        cutoff = dt.datetime.utcnow() - dt.timedelta(hours=lookback_hours)
        with Session(self.engine) as s:
            rows = s.execute(
                select(posts.c.id, posts.c.norm_text)
                .where(
                    and_(
                        posts.c.channel == channel,
                        posts.c.created_at >= cutoff,
                    )
                )
                .order_by(posts.c.id.desc())
                .limit(500)
            ).all()

        for pid, norm_existing in rows:
            if is_near_duplicate(
                norm, norm_existing,
                simhash_threshold=simhash_threshold,
                fuzzy_threshold=fuzzy_threshold,
                use_fuzzy=use_fuzzy
            ):
                return pid
        return None

    def add_post_with_similarity_guard(
        self,
        *,
        channel: str,
        message_id: Optional[int],
        text: str,
        created_at: Optional[dt.datetime] = None,
        use_fuzzy: Optional[bool] = None,
        fuzzy_threshold: int = 90,
        simhash_threshold: int = 6,
        lookback_hours: int = 72,
    ) -> Optional[int]:
        """
        1) Вставляем по уникальному индексу (channel, fingerprint).
        2) Если найдём близкий недавний пост — откатываем вставку и возвращаем None.
        """
        norm = normalize_text(text)
        fingerprint = make_fingerprint(norm)
        created_at = created_at or dt.datetime.utcnow()

        with Session(self.engine) as s:
            try:
                res = s.execute(
                    insert(posts)
                    .values(
                        channel=channel,
                        message_id=message_id,
                        text=text,
                        norm_text=norm,
                        fingerprint=fingerprint,
                        created_at=created_at,
                    )
                    .returning(posts.c.id)
                )
                new_id = res.scalar_one()

                cutoff = created_at - dt.timedelta(hours=lookback_hours)
                near = s.execute(
                    select(posts.c.id, posts.c.norm_text)
                    .where(
                        and_(
                            posts.c.channel == channel,
                            posts.c.created_at >= cutoff,
                            posts.c.id != new_id,
                        )
                    )
                    .order_by(posts.c.id.desc())
                    .limit(500)
                ).all()

                for pid, norm_existing in near:
                    if is_near_duplicate(
                        norm, norm_existing,
                        simhash_threshold=simhash_threshold,
                        fuzzy_threshold=fuzzy_threshold,
                        use_fuzzy=use_fuzzy
                    ):
                        s.execute(text("DELETE FROM posts WHERE id = :id"), {"id": new_id})
                        s.commit()
                        return None

                s.commit()
                return new_id
            except IntegrityError:
                s.rollback()
                return None


# ---- Совместимость: историческое имя ----
class PostsRepo(PostsRepository):
    """
    Совместимость со старыми импортами:
    from quadridigest.storage.repositories import PostsRepo
    Методические синонимы для старых вызовов:
      - add(...) -> add_post_with_similarity_guard(...)
      - add_if_new(...) -> add_post_if_new(...)
      - maybe_duplicate(...) -> maybe_duplicate_by_similarity(...)
    """
    # Синонимы методов (на случай устаревших вызовов в коде)
    def add(self, *, channel: str, message_id: int | None, text: str, created_at: dt.datetime | None = None,
            use_fuzzy: Optional[bool] = None, fuzzy_threshold: int = 90, simhash_threshold: int = 6, lookback_hours: int = 72) -> Optional[int]:
        return self.add_post_with_similarity_guard(
            channel=channel,
            message_id=message_id,
            text=text,
            created_at=created_at,
            use_fuzzy=use_fuzzy,
            fuzzy_threshold=fuzzy_threshold,
            simhash_threshold=simhash_threshold,
            lookback_hours=lookback_hours,
        )

    def add_if_new(self, *, channel: str, message_id: int | None, text: str, created_at: dt.datetime | None = None,
                   use_fuzzy: Optional[bool] = None, fuzzy_threshold: int = 90, simhash_threshold: int = 6, lookback_hours: int = 72) -> Optional[int]:
        return self.add_post_if_new(
            channel=channel,
            message_id=message_id,
            text=text,
            created_at=created_at,
            use_fuzzy=use_fuzzy,
            fuzzy_threshold=fuzzy_threshold,
            simhash_threshold=simhash_threshold,
            lookback_hours=lookback_hours,
        )

    def maybe_duplicate(self, *, channel: str, text: str, use_fuzzy: Optional[bool] = None,
                        fuzzy_threshold: int = 90, simhash_threshold: int = 6, lookback_hours: int = 72) -> Optional[int]:
        return self.maybe_duplicate_by_similarity(
            channel=channel,
            text=text,
            use_fuzzy=use_fuzzy,
            fuzzy_threshold=fuzzy_threshold,
            simhash_threshold=simhash_threshold,
            lookback_hours=lookback_hours,
        )
# === QadriDigest hotfix: PostsRepository/PostsRepo.engine -> optional ===
try:
    from .db import init_db  # package-relative
except Exception:
    from quadridigest.storage.db import init_db  # absolute fallback

# Подмена __init__ у базового репозитория: если engine не передан — берём init_db()
if 'PostsRepository' in globals() and hasattr(PostsRepository, '__init__'):
    _qd_old_repo_init = PostsRepository.__init__

    def _qd_repo_init(self, engine=None, *args, **kwargs):
        if engine is None:
            engine = init_db()
        return _qd_old_repo_init(self, engine, *args, **kwargs)

    PostsRepository.__init__ = _qd_repo_init

# Если существует алиас/наследник PostsRepo, тоже сделаем безопасный конструктор
if 'PostsRepo' in globals():
    _qd_old_postsrepo_init = getattr(PostsRepo, '__init__', None)

    def _qd_postsrepo_init(self, engine=None, *args, **kwargs):
        if engine is None:
            engine = init_db()
        # если был свой __init__, используем его; иначе — родительский
        if callable(_qd_old_postsrepo_init):
            return _qd_old_postsrepo_init(self, engine, *args, **kwargs)
        return PostsRepository.__init__(self, engine, *args, **kwargs)

    PostsRepo.__init__ = _qd_postsrepo_init
# === /hotfix ===
# === QadriDigest hotfix: add PostsRepository.upsert(...) (+ PostsRepo proxy) ===
from datetime import datetime
from typing import Any

try:
    # локальный импорт
    from ..dedupe.simhash import normalize_text, make_fingerprint  # type: ignore
except Exception:
    # абсолютный импорт
    from quadridigest.dedupe.simhash import normalize_text, make_fingerprint  # type: ignore

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

def _qd_extract_post_fields(obj: Any) -> dict:
    """
    Гибко извлекаем поля из dataclass/NamedTuple/obj/dict.
    Принимаем как минимум: channel, text, [message_id], [created_at], [fingerprint], [norm_text].
    Если fingerprint нет — считаем по text. Если norm_text нет — нормализуем text.
    """
    # удобные геттеры
    def get(name, default=None):
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    channel = get("channel")
    text = get("text", "")
    message_id = get("message_id", None)
    created_at = get("created_at", None)
    fingerprint = get("fingerprint", None)
    norm_text = get("norm_text", None)

    if created_at is None:
        created_at = datetime.utcnow()

    if norm_text is None:
        norm_text = normalize_text(text or "")

    if fingerprint is None:
        fingerprint = make_fingerprint(norm_text)

    return {
        "channel": channel,
        "message_id": message_id,
        "text": text or "",
        "norm_text": norm_text,
        "fingerprint": fingerprint,
        "created_at": created_at,
    }

# Добавляем метод в базовый репозиторий, если его ещё нет
if 'PostsRepository' in globals() and not hasattr(PostsRepository, 'upsert'):
    def _qd_upsert(self, record: Any) -> int | None:
        """
        Идемпотентная вставка по уникальному индексу (channel, fingerprint).
        Если запись уже есть — возвращаем её id. Если новая — вставляем и возвращаем новый id.
        При ошибках уникальности используем select, чтобы вернуть существующий id.
        """
        data = _qd_extract_post_fields(record)
        if not data.get("channel") or not data.get("fingerprint"):
            # Недостаточно данных — безопасно вернуть None
            return None

        with Session(self.engine) as s:  # type: ignore[name-defined]
            try:
                res = s.execute(
                    insert(posts)  # type: ignore[name-defined]
                    .values(**data)
                    .returning(posts.c.id)  # type: ignore[attr-defined]
                )
                new_id = res.scalar_one()
                s.commit()
                return new_id
            except IntegrityError:
                s.rollback()
                # Уже есть — найдём id
                row = s.execute(
                    select(posts.c.id)  # type: ignore[attr-defined]
                    .where(
                        (posts.c.channel == data["channel"]) &  # type: ignore[attr-defined]
                        (posts.c.fingerprint == data["fingerprint"])  # type: ignore[attr-defined]
                    )
                    .limit(1)
                ).first()
                return row[0] if row else None

    PostsRepository.upsert = _qd_upsert  # type: ignore[attr-defined]

# Прокси-метод для PostsRepo, если класс присутствует
if 'PostsRepo' in globals() and not hasattr(PostsRepo, 'upsert'):
    def _qd_postsrepo_upsert(self, record: Any) -> int | None:
        return PostsRepository.upsert(self, record)  # type: ignore[misc]
    PostsRepo.upsert = _qd_postsrepo_upsert  # type: ignore[attr-defined]
# === /hotfix ===
# === QadriDigest hotfix: smarter upsert with similarity guard ===
from datetime import datetime, timedelta
from typing import Any

try:
    from ..dedupe.simhash import normalize_text, make_fingerprint  # type: ignore
except Exception:
    from quadridigest.dedupe.simhash import normalize_text, make_fingerprint  # type: ignore

from sqlalchemy import select, and_
from sqlalchemy.exc import IntegrityError

def _qd_extract_post_fields(obj: Any) -> dict:
    def get(name, default=None):
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)
    channel = get("channel")
    text = get("text", "") or ""
    message_id = get("message_id", None)
    created_at = get("created_at", None) or datetime.utcnow()
    norm_text = get("norm_text", None) or normalize_text(text)
    fingerprint = get("fingerprint", None) or make_fingerprint(norm_text)
    return {
        "channel": channel,
        "message_id": message_id,
        "text": text,
        "norm_text": norm_text,
        "fingerprint": fingerprint,
        "created_at": created_at,
    }

if 'PostsRepository' in globals():
    def _qd_upsert_smart(self, record: Any,
                         *, use_fuzzy: bool | None = None,
                         fuzzy_threshold: int = 90,
                         simhash_threshold: int = 6,
                         lookback_hours: int = 72) -> int | None:
        """
        Алгоритм:
        1) Если точный fingerprint уже есть в (channel, fingerprint) — вернуть id.
        2) Иначе — проверить недавние посты канала на перефраз (simhash + опц. rapidfuzz).
           При совпадении — вернуть найденный id (не вставлять новый).
        3) Иначе — вставить новую запись и вернуть её id.
        """
        data = _qd_extract_post_fields(record)
        if not data.get("channel") or not data.get("fingerprint"):
            return None

        cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)

        with Session(self.engine) as s:  # type: ignore[name-defined]
            # 1) точное совпадение fingerprint
            row = s.execute(
                select(posts.c.id)  # type: ignore[attr-defined]
                .where(and_(posts.c.channel == data["channel"],
                            posts.c.fingerprint == data["fingerprint"]))
                .limit(1)
            ).first()
            if row:
                return row[0]

            # 2) перефраз среди недавних
            rows = s.execute(
                select(posts.c.id, posts.c.norm_text)  # type: ignore[attr-defined]
                .where(and_(posts.c.channel == data["channel"],
                            posts.c.created_at >= cutoff))
                .order_by(posts.c.id.desc())
                .limit(500)
            ).all()
            for pid, norm_existing in rows:
                if is_near_duplicate(  # type: ignore[name-defined]
                    data["norm_text"], norm_existing,
                    simhash_threshold=simhash_threshold,
                    fuzzy_threshold=fuzzy_threshold,
                    use_fuzzy=use_fuzzy,
                ):
                    return pid  # считаем дублем, не вставляем

            # 3) вставка новой
            try:
                res = s.execute(
                    insert(posts)  # type: ignore[name-defined]
                    .values(**data)
                    .returning(posts.c.id)  # type: ignore[attr-defined]
                )
                new_id = res.scalar_one()
                s.commit()
                return new_id
            except IntegrityError:
                s.rollback()
                # параллельная гонка — вернём существующую
                row = s.execute(
                    select(posts.c.id)  # type: ignore[attr-defined]
                    .where(and_(posts.c.channel == data["channel"],
                                posts.c.fingerprint == data["fingerprint"]))
                    .limit(1)
                ).first()
                return row[0] if row else None

    # заменить/добавить метод
    PostsRepository.upsert = _qd_upsert_smart  # type: ignore[attr-defined]

# Прокси для PostsRepo, если требуется
if 'PostsRepo' in globals():
    def _qd_postsrepo_upsert_smart(self, record: Any, **kwargs) -> int | None:
        return PostsRepository.upsert(self, record, **kwargs)  # type: ignore[misc]
    PostsRepo.upsert = _qd_postsrepo_upsert_smart  # type: ignore[attr-defined]
# === /hotfix ===
# === QadriDigest hotfix: smarter upsert (similarity guard) ===
from datetime import datetime, timedelta
from typing import Any
from sqlalchemy import select, and_, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
try:
    from ..dedupe.simhash import normalize_text, make_fingerprint
    from ..dedupe.clusters import is_near_duplicate
    from .db import posts
except Exception:
    from quadridigest.dedupe.simhash import normalize_text, make_fingerprint
    from quadridigest.dedupe.clusters import is_near_duplicate
    from quadridigest.storage.db import posts

def _qd_extract_post_fields(obj: Any) -> dict:
    def get(name, default=None):
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)
    channel = get("channel")
    text = (get("text", "") or "")
    message_id = get("message_id", None)
    created_at = get("created_at", None) or datetime.utcnow()
    norm_text = get("norm_text", None) or normalize_text(text)
    fingerprint = get("fingerprint", None) or make_fingerprint(norm_text)
    return {
        "channel": channel, "message_id": message_id, "text": text,
        "norm_text": norm_text, "fingerprint": fingerprint, "created_at": created_at,
    }

def _qd_upsert_smart(self, record: Any, *,
                     use_fuzzy: bool | None = True,  # включаем fuzzy по умолчанию
                     fuzzy_threshold: int = 90,
                     simhash_threshold: int = 6,
                     lookback_hours: int = 72) -> int | None:
    data = _qd_extract_post_fields(record)
    if not data.get("channel") or not data.get("fingerprint"):
        return None

    cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
    with Session(self.engine) as s:
        # 1) точный отпечаток уже есть?
        row = s.execute(
            select(posts.c.id).where(and_(posts.c.channel == data["channel"],
                                          posts.c.fingerprint == data["fingerprint"])).limit(1)
        ).first()
        if row:
            return row[0]

        # 2) похожий за последнее окно?
        near = s.execute(
            select(posts.c.id, posts.c.norm_text)
            .where(and_(posts.c.channel == data["channel"], posts.c.created_at >= cutoff))
            .order_by(posts.c.id.desc()).limit(500)
        ).all()
        for pid, norm_existing in near:
            if is_near_duplicate(data["norm_text"], norm_existing,
                                 simhash_threshold=simhash_threshold,
                                 fuzzy_threshold=fuzzy_threshold,
                                 use_fuzzy=use_fuzzy):
                return pid  # дубль — возвращаем найденный id

        # 3) новая вставка
        try:
            res = s.execute(insert(posts).values(**data).returning(posts.c.id))
            new_id = res.scalar_one()
            s.commit()
            return new_id
        except IntegrityError:
            s.rollback()
            row = s.execute(
                select(posts.c.id).where(and_(posts.c.channel == data["channel"],
                                              posts.c.fingerprint == data["fingerprint"])).limit(1)
            ).first()
            return row[0] if row else None

# Переприсваиваем в обоих классах
if 'PostsRepository' in globals():
    PostsRepository.upsert = _qd_upsert_smart  # type: ignore[attr-defined]
if 'PostsRepo' in globals():
    PostsRepo.upsert = _qd_upsert_smart       # type: ignore[attr-defined]
# === /hotfix ===
# === QadriDigest hotfix: smarter upsert (similarity guard) ===
from datetime import datetime, timedelta
from sqlalchemy import select, and_, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
try:
    from ..dedupe.simhash import normalize_text, make_fingerprint
    from ..dedupe.clusters import is_near_duplicate
    from .db import posts
except Exception:
    from quadridigest.dedupe.simhash import normalize_text, make_fingerprint
    from quadridigest.dedupe.clusters import is_near_duplicate
    from quadridigest.storage.db import posts

def _qd_extract_post_fields(obj):
    def g(n, d=None):
        return obj.get(n,d) if isinstance(obj, dict) else getattr(obj, n, d)
    text = (g("text","") or "")
    norm = g("norm_text") or normalize_text(text)
    fp = g("fingerprint") or make_fingerprint(norm)
    return {
        "channel": g("channel"),
        "message_id": g("message_id"),
        "text": text,
        "norm_text": norm,
        "fingerprint": fp,
        "created_at": g("created_at") or datetime.utcnow(),
    }

def _qd_upsert_smart(self, rec, *, use_fuzzy=True, fuzzy_threshold=90, simhash_threshold=6, lookback_hours=72):
    data = _qd_extract_post_fields(rec)
    if not data["channel"] or not data["fingerprint"]:
        return None
    cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
    with Session(self.engine) as s:  # type: ignore
        row = s.execute(select(posts.c.id).where(and_(posts.c.channel==data["channel"],
                                                      posts.c.fingerprint==data["fingerprint"])).limit(1)).first()
        if row: return row[0]
        near = s.execute(select(posts.c.id, posts.c.norm_text)
                         .where(and_(posts.c.channel==data["channel"], posts.c.created_at>=cutoff))
                         .order_by(posts.c.id.desc()).limit(500)).all()
        for pid, norm_existing in near:
            if is_near_duplicate(data["norm_text"], norm_existing,
                                 simhash_threshold=simhash_threshold,
                                 fuzzy_threshold=fuzzy_threshold,
                                 use_fuzzy=use_fuzzy):
                return pid
        try:
            rid = s.execute(insert(posts).values(**data).returning(posts.c.id)).scalar_one()
            s.commit()
            return rid
        except IntegrityError:
            s.rollback()
            row = s.execute(select(posts.c.id).where(and_(posts.c.channel==data["channel"],
                                                          posts.c.fingerprint==data["fingerprint"])).limit(1)).first()
            return row[0] if row else None

# Переприсвоение методов
if 'PostsRepository' in globals(): PostsRepository.upsert = _qd_upsert_smart  # type: ignore
if 'PostsRepo' in globals():       PostsRepo.upsert       = _qd_upsert_smart  # type: ignore
# === /hotfix ===
# === QadriDigest hotfix: smarter upsert (similarity guard) ===
from datetime import datetime, timedelta
from sqlalchemy import select, and_, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
try:
    from ..dedupe.simhash import normalize_text, make_fingerprint
    from ..dedupe.clusters import is_near_duplicate
    from .db import posts
except Exception:
    from quadridigest.dedupe.simhash import normalize_text, make_fingerprint
    from quadridigest.dedupe.clusters import is_near_duplicate
    from quadridigest.storage.db import posts

def _qd_extract_post_fields(obj):
    def g(n, d=None):
        return obj.get(n, d) if isinstance(obj, dict) else getattr(obj, n, d)
    text = (g("text", "") or "")
    norm = g("norm_text") or normalize_text(text)
    fp = g("fingerprint") or make_fingerprint(norm)
    return {
        "channel": g("channel"),
        "message_id": g("message_id"),
        "text": text,
        "norm_text": norm,
        "fingerprint": fp,
        "created_at": g("created_at") or datetime.utcnow(),
    }

def _qd_upsert_smart(self, rec, *, use_fuzzy=True, fuzzy_threshold=90, simhash_threshold=6, lookback_hours=72):
    data = _qd_extract_post_fields(rec)
    if not data["channel"] or not data["fingerprint"]:
        return None
    cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
    with Session(self.engine) as s:  # type: ignore
        # 1) точное совпадение fingerprint уже есть?
        row = s.execute(
            select(posts.c.id)
            .where(and_(posts.c.channel == data["channel"], posts.c.fingerprint == data["fingerprint"]))
            .limit(1)
        ).first()
        if row:
            return row[0]
        # 2) перефраз среди недавних
        near = s.execute(
            select(posts.c.id, posts.c.norm_text)
            .where(and_(posts.c.channel == data["channel"], posts.c.created_at >= cutoff))
            .order_by(posts.c.id.desc())
            .limit(500)
        ).all()
        for pid, norm_existing in near:
            if is_near_duplicate(
                data["norm_text"], norm_existing,
                simhash_threshold=simhash_threshold,
                fuzzy_threshold=fuzzy_threshold,
                use_fuzzy=use_fuzzy,
            ):
                return pid  # дубль — возвращаем найденный id
        # 3) вставка новой
        try:
            rid = s.execute(insert(posts).values(**data).returning(posts.c.id)).scalar_one()
            s.commit()
            return rid
        except IntegrityError:
            s.rollback()
            row = s.execute(
                select(posts.c.id)
                .where(and_(posts.c.channel == data["channel"], posts.c.fingerprint == data["fingerprint"]))
                .limit(1)
            ).first()
            return row[0] if row else None

# Применяем к обоим классам
if 'PostsRepository' in globals(): PostsRepository.upsert = _qd_upsert_smart  # type: ignore
if 'PostsRepo' in globals():       PostsRepo.upsert       = _qd_upsert_smart  # type: ignore
# === /hotfix ===

# === QadriDigest hotfix: smarter upsert (similarity guard) ===
from datetime import datetime, timedelta
from sqlalchemy import select, and_, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
try:
    from ..dedupe.simhash import normalize_text, make_fingerprint
    from ..dedupe.clusters import is_near_duplicate
    from .db import posts
except Exception:
    from quadridigest.dedupe.simhash import normalize_text, make_fingerprint
    from quadridigest.dedupe.clusters import is_near_duplicate
    from quadridigest.storage.db import posts

def _qd_extract_post_fields(obj):
    def g(n, d=None):
        return obj.get(n, d) if isinstance(obj, dict) else getattr(obj, n, d)
    text = (g("text", "") or "")
    norm = g("norm_text") or normalize_text(text)
    fp = g("fingerprint") or make_fingerprint(norm)
    return {
        "channel": g("channel"),
        "message_id": g("message_id"),
        "text": text,
        "norm_text": norm,
        "fingerprint": fp,
        "created_at": g("created_at") or datetime.utcnow(),
    }

def _qd_upsert_smart(self, rec, *, use_fuzzy=True, fuzzy_threshold=90, simhash_threshold=6, lookback_hours=72):
    data = _qd_extract_post_fields(rec)
    if not data["channel"] or not data["fingerprint"]:
        return None
    cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
    with Session(self.engine) as s:  # type: ignore
        # 1) точный отпечаток уже есть?
        row = s.execute(
            select(posts.c.id)
            .where(and_(posts.c.channel == data["channel"], posts.c.fingerprint == data["fingerprint"]))
            .limit(1)
        ).first()
        if row:
            return row[0]
        # 2) перефраз среди недавних
        near = s.execute(
            select(posts.c.id, posts.c.norm_text)
            .where(and_(posts.c.channel == data["channel"], posts.c.created_at >= cutoff))
            .order_by(posts.c.id.desc())
            .limit(500)
        ).all()
        for pid, norm_existing in near:
            if is_near_duplicate(
                data["norm_text"], norm_existing,
                simhash_threshold=simhash_threshold,
                fuzzy_threshold=fuzzy_threshold,
                use_fuzzy=use_fuzzy,
            ):
                return pid  # дубль — возвращаем найденный id
        # 3) вставка новой
        try:
            rid = s.execute(insert(posts).values(**data).returning(posts.c.id)).scalar_one()
            s.commit()
            return rid
        except IntegrityError:
            s.rollback()
            row = s.execute(
                select(posts.c.id)
                .where(and_(posts.c.channel == data["channel"], posts.c.fingerprint == data["fingerprint"]))
                .limit(1)
            ).first()
            return row[0] if row else None

# Применяем к обоим классам
if 'PostsRepository' in globals(): PostsRepository.upsert = _qd_upsert_smart  # type: ignore
if 'PostsRepo' in globals():       PostsRepo.upsert       = _qd_upsert_smart  # type: ignore
# === /hotfix ===
