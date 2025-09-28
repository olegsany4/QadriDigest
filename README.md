
# QadriDigest (Refactor Skeleton)

Дата сборки: 2025-09-25T05:19:34.187425 UTC

Это референсная структура проекта для новостного агрегатора с дедупликацией, LLM-аннотацией
и публикацией в телеграм-каналы в формате «коротко → подробнее».

## Ключевые фичи
- Модульные слои: fetch/normalize/dedupe/llm/format/publish/storage.
- UX публикации переключаем через `UI_VARIANT=reply|edit`.
- Строгий JSON-контракт между пайплайнами и LLM (Pydantic-модели).
- Идемпотентность публикаций по fingerprint.
- Логи, .env.example, templates.yaml, tests заготовки.

## Быстрый старт
1) `python -m venv .venv && source .venv/bin/activate`
2) `pip install -r requirements.txt`
3) Скопируй `.env.example` → `.env` и заполни.
4) Запусти один раз для проверки окружения:
   ```
   python -m quadridigest.main --dry-run
   ```

## Структура
```
quadridigest/
  config.py          # Env, настройки
  logging.py         # Логи
  types.py           # Pydantic DTO
  utils.py           # Утилиты
  main.py            # Оркестратор
  scheduler.py       # Планировщик (заготовка)

  fetch/
    sources.py
    normalize.py

  dedupe/
    simhash.py
    clusters.py

  llm/
    client.py
    prompts.py
    cache.py

  format/
    templates.yaml
    render.py

  publish/
    telethon_client.py
    publisher.py

  storage/
    db.py
    repositories.py
    migrations/   (пусто)
```

## Примечания
- Для режима `edit` бот должен быть админом в канале.
- В `requirements.txt` нет «тяжёлых» моделей — LLM-клиент по умолчанию моковый,
  замени на свой вызов API (OpenAI/Gemini/и т.п.) в `llm/client.py`.

---

## Anchor → NEXT_CHAT

### CONTEXT_LATEST
Выполнен п.2: источники перенесены на реальный fetch через Telethon.

### DECISIONS
- Используем Telethon user-session для fetch.
- Храним `last_id` в `data/state.json`.
- Выходные данные стандартизированы в `RawPost`.

### TODO_NEXT
3) Реализовать модуль **dedupe**:
   - Алгоритм simhash/кластеризация.
   - Исключение повторов между источниками.
   - DoD: при dry-run видно, что одинаковые новости не дублируются в разных каналах.

---

## Статус выполнения этапов

- ✅ 3) Нормализация — clean_text удаляет сигнатуры/UTM, нормализует кавычки/пробелы, определяет язык; doctest/selftest в файле; 10 unit-тестов зелёные.

---

<a name="NEXT_CHAT_4"></a>
### NEXT_CHAT_4
Следующий этап: **4) Дедуп: кластеры и отпечатки**

Файлы: `dedupe/simhash.py`, `dedupe/clusters.py`, `storage/repositories.py`, `storage/db.py`.

1. быстрый отпечаток (blake/simhash) для предварительного отсева;
2. уникальный индекс на `(channel, fingerprint)`:
   ```sql
   create unique index if not exists uq_posts_channel_fp on posts(channel, fingerprint);

# QadriDigest (Refactor Skeleton)

Дата сборки: 2025-09-27

Это референсная структура проекта для новостного агрегатора с дедупликацией, LLM-аннотацией
и публикацией в телеграм-каналы в формате «коротко → подробнее».

## Ключевые фичи
- Модульные слои: fetch / normalize / dedupe / llm / format / publish / storage.
- UX публикации переключаем через `UI_VARIANT=reply|edit`.
- **Строгий JSON-контракт между пайплайном и LLM** (Pydantic-модели, ретрай при невалидном JSON).
- **Реальный LLM-клиент** (OpenAI / Gemini / Ollama), кэширование ответов (in-memory, TTL).
- Идемпотентность публикаций по `fingerprint`.
- Логи, `.env.example`, `templates.yaml`, тестовые заготовки.

## Быстрый старт
1) `python -m venv .venv && source .venv/bin/activate`  
2) `pip install -r requirements.txt`  
3) Скопируй `.env.example` → `.env` и заполни:
   ```env
   # включение LLM-режима
   PUBLISH_MODE=llm
   LLM_ENABLED=true

   # провайдер: ollama | openai | gemini
   LLM_PROVIDER=ollama
   LLM_MODEL=llama3.1:8b
   OLLAMA_BASE_URL=http://localhost:11434

   # для OpenAI/Gemini — ключи:
   # OPENAI_API_KEY=...
   # GEMINI_API_KEY=...

   # опционально: TTL кэша в секундах
   # LLM_CACHE_TTL_SEC=3600

   # варианты UI: reply | edit
   UI_VARIANT=reply
