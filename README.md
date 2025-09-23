
# QuadriDigest — дайджест по 4 темам (Политика / Трейдинг / Инфобез / Личные финансы)

Мульти-канальный бот, который собирает сообщения из Telegram-источников и публикует:

* краткий дайджест (по 1 строке на событие)
* «раскрывашки» (полные тексты) **ответом (reply) на краткий пост**, чтобы короткий пост оставался наверху ленты.

## 1) Быстрый старт

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# заполните .env (см. секцию ниже)
cp .env.example .env && nano .env

# Разовый прогон
python -m quadridigest.main --once

# Ежечасно (встроенный планировщик)
python -m quadridigest.main --schedule "hourly"
# или через cron:
# 0 * * * * /path/to/venv/bin/python -m quadridigest.main --once >> /var/log/quadridigest.log 2>&1
```

## 2) Настройка `.env`

Минимум (обязательно):

```
API_ID=29695146
API_HASH=9fc2bf793baa36c04d88c8cb04ca6f48
SESSION_NAME=quadridigest_session
TZ=Europe/Prague
```

Куда публиковать (любой из форматов на канал):

* `TARGET_*=@username_канала`  (рекомендуется)
* `TARGET_*=https://t.me/username_канала`
* `TARGET_*=numeric_id` **ТОЛЬКО если код внутри предварительно разрешает id -> entity** (см. «Разбор ошибок» ниже)

```
TARGET_POLITICS=@qd_politics
TARGET_TRADING=@qd_trading
TARGET_INFOSEC=@qd_infosec
TARGET_PERSONAL=@qd_personal
```

Ограничения и окно:

```
MAX_ITEMS=10
WINDOW=hours=1           # hours=1 | hours=24 | since=YYYY-MM-DDTHH:MM
```

Режим публикации раскрывашек:

```
DETAILS_MODE=spoiler    # spoiler | off
DETAILS_MAX=10
```

Опционально (debug/скорость):

```
DEBUG_FETCH=true
MAX_PER_CHANNEL=120
PER_CHANNEL_TIMEOUT=20
FETCH_CONCURRENCY=6
```

### Про LLM (нейросеть)

```
LLM_ENABLED=true
OLLAMA_HOST=http://127.0.0.1:11434
LLM_MODEL=llama3.1:8b
LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=800
PUBLISH_MODE=bullets      # bullets | llm  (llm — генерировать краткий текст ИИ)
LLM_WEIGHT=1.2
LLM_CACHE_DB=llm_cache.sqlite3
LLM_CACHE_TTL_DAYS=7
LLM_TRACE_ENABLED=true
LLM_TRACE_PATH=llm_traces.log
```

> Если хотите использовать ИИ-краткое резюме — поставьте `PUBLISH_MODE=llm` и убедитесь, что **в коде реализованы** методы LLM-клиента, см. «Текущий статус ИИ» ниже.

## 3) Источники (`channels.txt`)

* По одному на строку: `username` **без `@`** или **числовой id**.
* Комментарии после `#`, пустые строки игнорируются.

Пример:

```
# --- СМИ / Политика ---
kommersant
vedomosti
rbc_news
interfax_news
...

# --- Трейдинг / Рынки ---
markettwits
if_market_news
...

# --- Паблики без username (id) ---
1202159807   # Банки, деньги, два офшора
...
```

## 4) Что сейчас работает (по вашим скринам/логам)

* Короткие пункты публикуются первыми; длинные версии идут **reply** к ним. Это верно — короткий пост остаётся «шапкой», а «раскрывашка» не выталкивает ленту.
* Источники читаются пачками; `MAX_PER_CHANNEL` и таймауты работают.
* Флуд-wait от Telethon корректно ловится (в логах видны «Sleeping for … on GetHistoryRequest flood wait»).

## 5) Разбор ошибок из логов

### A) `resolve error for -997...: Could not find the input entity for PeerChannel(...)`

* Это происходит, когда в `TARGET_*` задан **сырой числовой id** канала (например, `1002836805154`) и код пытается «разрешить» его напрямую через `get_entity(id)`.
* Для каналов **Telethon не может по одному цифр. id получить entity**, ему нужен либо:

  1. `username` (самый простой путь — используйте `@username` в TARGET\_\*), или
  2. `InputPeerChannel(channel_id, access_hash)` — но тогда вы должны заранее иметь `access_hash`. Обычно его получают из `get_dialogs()` и кешируют (id+access\_hash).

**Что делать прямо сейчас (без изменения кода):**

* В `TARGET_*` укажите **usernames** ваших четырёх каналов (например, `@qd_politics`).
  Так вы мгновенно избавитесь от `resolve error`.

**Что сделать в коде (улучшение):**

* Добавить слой «резолва таргета»:

  * если `target` начинается с `@`/`https://t.me/` — `get_entity(target)` работает сразу;
  * если это цифр. `id`, сначала ищем в локальном кеше (id→InputPeerChannel), а при отсутствии — **1 раз** пробегаемся по `client.get_dialogs()` и сохраняем `access_hash`. После этого шлём через `client.send_message(InputPeerChannel(id, access_hash), ...)`.

### B) `LLM annotate error: 'LLMClient' object has no attribute 'annotate_news'`

* В вашем дереве кода **метод не реализован** или класс инстанцируется не той версией. Поэтому фактически ИИ-аннотации не работают.
* Сейчас дайджест собирается без LLM (даже при `LLM_ENABLED=true` в .env).

**Что делать:**

* Либо временно выключить: `LLM_ENABLED=false`/`PUBLISH_MODE=bullets`,
* Либо **реализовать** в `LLMClient`:

  * `annotate_news(items) -> List[AnnotatedItem]` (короткие тезисы + категория/теги/скор),
  * `summarize_thread(long_text) -> str`,
  * кеширование запросов (sqlite) + трейс-лог.

## 6) Скрины и требование «короткое сверху, длинное по клику»

* На ваших скринах всё ок: короткий пункт, под ним `<spoiler>…</spoiler>` в реплае.
* Если хотите вообще **в одном сообщении**: краткая строка + ссылка «развернуть», — оставляйте `DETAILS_MODE=spoiler`: Telegram рендерит спойлеры скрытыми до клика.

## 7) Чек-лист перед запуском

1. **API**: `API_ID`, `API_HASH`, `SESSION_NAME` заполнены.
2. **Таргеты**: `TARGET_POLITICS`, `TARGET_TRADING`, `TARGET_INFOSEC`, `TARGET_PERSONAL` — **лучше usernames** (например, `@qd_politics`).
   Если принципиально нужны цифровые id — внедрите кеш `id+access_hash` (см. план работ ниже).
3. **Окно**: `WINDOW=hours=1`, `MAX_ITEMS=10` — не душите ленту.
4. **Список источников**: `channels.txt` отформатирован (каждый источник на новой строке, `#` — комментарий).
5. **LLM**:

   * если нужен — `ollama serve`, `ollama pull llama3.1:8b`, и **реализованные методы LLMClient**;
   * если пока без LLM — `PUBLISH_MODE=bullets`.

## 8) План улучшений кода (пошагово)

### 8.1 Резолв и кеш целей (исправляет `resolve error`)

* Добавить модуль `targets.py`:

  * `resolve_target(target_str) -> Union[str, InputPeerChannel]`
  * Локальный sqlite-кеш: таблица `peers(id INTEGER PRIMARY KEY, access_hash INTEGER)`;
    при первом запуске делаем `get_dialogs()` → сохраняем пары `channel_id`/`access_hash`.
  * В `main.post_to_target(...)` принимать результат `resolve_target(...)` и слать `send_message()` уже на корректный peer.

### 8.2 Детект и дедупликат новостей

* Хешируем контент сообщения (title+url+source+date) → bloom filter / sqlite (`digests(hash TEXT PK, created_at)`).
* Перед публикацией пропускаем дубликаты внутри текущего окна и между запусками.

### 8.3 Раскладка «короткое + длинное»

* Уже есть публикация reply. Зафиксируйте логику:

  1. делаем внутренний батч: публикуем 1 короткий пост,
  2. к нему цепочкой публикуем раскрывашки `reply_to=message_id_short`,
  3. следующая короткая карточка — только **после** завершения цепочки предыдущей (не параллелить).

### 8.4 LLM-клиент (восстановить полноценную работу ИИ)

* Интерфейс:

  * `annotate_news(items) -> List[Annotated]` (score, short, hashtags, category),
  * `summarize(item) -> long_text` (для спойлера),
  * `summarize_batch(items) -> string` (если `PUBLISH_MODE=llm`).
* Кешировать промпты (sqlite), включить трассировку (`LLM_TRACE_*`).
* Ошибки LLM не должны ронять процесс — только предупреждения.

### 8.5 Троттлинг и flood-wait

* Уже частично есть. Добавить пулы/семафоры:

  * `FETCH_CONCURRENCY` на сбор,
  * авто-повторы при `FloodWaitError` с `await asyncio.sleep(e.seconds)`.

### 8.6 Улучшение «спойлеров»

* Настраиваемая длина аннотации до спойлера (`SUMMARY_LEN`),
* Авто-обрезка + «читать далее» ссылкой на оригинал.

### 8.7 Диагностика

* `--dry-run` (ничего не постит, только лог),
* `--debug-source=NAME` (собрать только по одному источнику),
* Счётчики: собрал/отфильтровал/опубликовано.

## 9) Ответы на ваши вопросы

**«Сейчас используется нейросеть как было предусмотрено?»**
По логам — **нет**. Видна серия ошибок `'LLMClient' object has no attribute 'annotate_news'`. Значит, текущая версия `LLMClient` не содержит ожидаемых методов, и публикация идёт в режиме «bullets» (без ИИ), даже если `LLM_ENABLED=true`.

**«Скрины не соответствуют требованиям»**
Теперь поведение соответствует требованиям: короткое сообщение публикуется первым, длинное — **в ответ** и не уезжает вверх. Если хотите ещё плотнее: можно публиковать **только** короткие, а длинные давать по кнопке/ссылке на оригинал — включите `DETAILS_MODE=off`.

**«Почему не постится в мои 4 канала (числовые id)?»**
Потому что Telethon не умеет из «голого числа» получить `entity`. Используйте **usernames таргет-каналов** или реализуйте кеш `id+access_hash` как в пункте 8.1.





# QuadriDigest — Политика • Трейдинг • ИБ • Личные финансы

Мульти-канальный бот: публикует дайджесты **каждый час** по 4 темам:
- Политика — @qd_politics
- Трейдинг — @qd_trading
- Инфобез — @qd_infosec
- Личные финансы — @qd_personal

## Установка
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Запуск
```bash
python -m quadridigest.main --once
python -m quadridigest.main --schedule "hourly"
```


python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env   # проставь API_ID/API_HASH и таргеты
ollama pull llama3.1:8b && ollama serve

# Разовый прогон (окно по умолчанию — 1 час)
python -m quadridigest.main --once

# Ежечасно через встроенный планировщик
python -m quadridigest.main --schedule "hourly"
# или cron:
# 0 * * * * /path/to/venv/bin/python -m quadridigest.main --once >> /var/log/quadridigest.log 2>&1




Description: Hourly digest bot


App configuration

App api_id:
29695146
App api_hash:
9fc2bf793baa36c04d88c8cb04ca6f48
App title:
Short name:
alphanumeric, 5-32 characters

FCM credentials  Update  

Available MTProto servers

Test configuration:
149.154.167.40:443
DC 2

Public keys:
-----BEGIN RSA PUBLIC KEY-----
MIIBCgKCAQEAyMEdY1aR+sCR3ZSJrtztKTKqigvO/vBfqACJLZtS7QMgCGXJ6XIR
yy7mx66W0/sOFa7/1mAZtEoIokDP3ShoqF4fVNb6XeqgQfaUHd8wJpDWHcR2OFwv
plUUI1PLTktZ9uW2WE23b+ixNwJjJGwBDJPQEQFBE+vfmH0JP503wr5INS1poWg/
j25sIWeYPHYeOrFp/eXaqhISP6G+q2IeTaWTXpwZj4LzXq5YOpk4bYEQ6mvRq7D1
aHWfYmlEGepfaYR8Q0YqvvhYtMte3ITnuSJs171+GDqpdKcSwHnd6FudwGO4pcCO
j4WcDuXc2CTHgH8gFTNhp/Y8/SpDOhvn9QIDAQAB
-----END RSA PUBLIC KEY-----
Production configuration:
149.154.167.50:443
DC 2

Public keys:
-----BEGIN RSA PUBLIC KEY-----
MIIBCgKCAQEA6LszBcC1LGzyr992NzE0ieY+BSaOW622Aa9Bd4ZHLl+TuFQ4lo4g
5nKaMBwK/BIb9xUfg0Q29/2mgIR6Zr9krM7HjuIcCzFvDtr+L0GQjae9H0pRB2OO
62cECs5HKhT5DZ98K33vmWiLowc621dQuwKWSQKjWf50XYFw42h21P2KXUGyp2y/
+aEyZ+uVgLLQbRA1dEjSDZ2iGRy12Mk5gpYc397aYp438fsJoHIgJ2lgMv5h7WY9
t6N/byY9Nw9p21Og3AoXSL2q/2IJ1WRUhebgAdGVMlV1fkuOQoEzR7EdpqtQD9Cs
5+bfo3Nhmcyvk5ftB0WkJ9z6bNZ7yxr



QUADRIDIGEST PATCH (multi-targets + reply long)
-----------------------------------------------

1) Скопируй содержимое папки quadridigest в свой проект с заменой одноимённых файлов.
2) В .env добавь цели (username без @ ИЛИ числовой id из list_dialogs.py):
   TARGET_POLITICS=1002836805154
   TARGET_TRADING=1002705126061
   TARGET_INFOSEC=1002976011029
   TARGET_PERSONAL=1002643357319

   (Можно указать только нужные, остальные будут пропущены.
    Альтернатива: единичный TARGET_CHANNEL_ID=... для обратной совместимости.)

3) Список источников — в channels.txt (username или числовые id, комментарии после #).

4) Запуск:
   source .venv/bin/activate
   python -m quadridigest.main --once

Поведение:
- Складывает короткий дайджест (по одной строке на событие) и публикует его в каждый целевой канал.
- Затем публикует ПОЛНУЮ версию как ответ (reply) на короткий пост — длинное не «выталкивает» короткое вверх.
