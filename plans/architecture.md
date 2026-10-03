# AIRailway — Архитектурный план

## Контекст
Telegram-бот для начальника Ленинградского вокзала (Москва).
Ежедневно утром присылает прогноз нагрузки на следующий день с рекомендациями.
Также отвечает на запросы "как планировать ДД.ММ?"

---

## Технологический стек

| Компонент | Технология |
|---|---|
| Язык | Python 3.11+ |
| Telegram-бот | python-telegram-bot v20 |
| Планировщик | APScheduler |
| БД | PostgreSQL (локальная) |
| ORM / SQL | SQLAlchemy + psycopg2 |
| Векторное хранилище | pgvector (расширение PostgreSQL) |
| Эмбеддинги для документов | sentence-transformers |
| LLM для генерации ответов | DeepSeek API (openai-совместимый) |
| Погода | Open-Meteo API (бесплатный, без ключа) |
| Мероприятия | Kudago API (бесплатный, Москва) |
| Виртуальное окружение | venv |
| Конфигурация | python-dotenv + .env |

---

## Структура проекта

```
AIRailway/
├── .env                        # секреты (токены, DSN)
├── .env.example                # шаблон
├── requirements.txt
├── README.md
├── plans/
│   └── architecture.md
├── data/
│   ├── passenger_flows/        # CSV с пассажиропотоками
│   └── regulations/            # PDF/DOCX регламентные документы
├── src/
│   ├── __init__.py
│   ├── config.py               # загрузка настроек из .env
│   ├── db/
│   │   ├── __init__.py
│   │   ├── models.py           # SQLAlchemy модели
│   │   └── session.py          # фабрика сессий
│   ├── loaders/
│   │   ├── __init__.py
│   │   ├── csv_loader.py       # загрузка CSV пассажиропотоков в PostgreSQL
│   │   └── doc_loader.py       # загрузка документов → векторизация → pgvector
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── weather.py          # Open-Meteo API
│   │   └── events.py           # Kudago API (мероприятия Москвы)
│   ├── analytics/
│   │   ├── __init__.py
│   │   ├── aggregator.py       # сбор всех данных в единый контекст
│   │   └── rag.py              # поиск по векторному хранилищу (регламенты)
│   ├── llm/
│   │   ├── __init__.py
│   │   └── deepseek.py         # запрос к DeepSeek API, формирование промпта
│   └── bot/
│       ├── __init__.py
│       ├── main.py             # точка входа, запуск бота + планировщика
│       ├── scheduler.py        # APScheduler: ежедневный прогноз
│       └── handlers.py         # обработчики команд и сообщений
└── scripts/
    ├── load_csv.py             # скрипт ручной загрузки CSV
    └── load_docs.py            # скрипт ручной загрузки документов
```

---

## Схема базы данных (PostgreSQL)

```sql
-- Пассажиропотоки
CREATE TABLE passenger_flows (
    id          SERIAL PRIMARY KEY,
    date        DATE NOT NULL,
    hour        SMALLINT NOT NULL,        -- 0-23
    direction   VARCHAR(10) NOT NULL,     -- 'in' / 'out'
    count       INTEGER NOT NULL,
    source      VARCHAR(100),             -- имя исходного файла
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Регламентные документы (для RAG)
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE regulation_chunks (
    id          SERIAL PRIMARY KEY,
    doc_name    VARCHAR(255) NOT NULL,
    chunk_text  TEXT NOT NULL,
    embedding   vector(384),             -- размерность sentence-transformers
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Лог прогнозов (история)
CREATE TABLE forecast_log (
    id           SERIAL PRIMARY KEY,
    forecast_date DATE NOT NULL,
    generated_at  TIMESTAMPTZ DEFAULT NOW(),
    context_json  JSONB,                 -- что было на входе
    response_text TEXT                   -- что ответил DeepSeek
);
```

---

## Поток данных (диаграмма)

```mermaid
flowchart TD
    A[APScheduler 07:00] --> B[aggregator.py]
    B --> C1[PostgreSQL: passenger_flows]
    B --> C2[weather.py: Open-Meteo]
    B --> C3[events.py: Kudago]
    B --> C4[rag.py: pgvector регламенты]
    C1 & C2 & C3 & C4 --> D[Единый контекст JSON]
    D --> E[deepseek.py: промпт + API]
    E --> F[Текст прогноза]
    F --> G[Telegram Bot: рассылка начальнику]

    H[Вопрос от начальника] --> I[handlers.py]
    I --> B
    B --> D --> E --> F --> J[Ответ в чат]
```

---

## Логика ежедневного прогноза

1. **aggregator.py** собирает данные на завтрашний день:
   - Исторические пассажиропотоки по часам за аналогичные дни (той же недели за прошлые периоды)
   - Прогноз погоды (температура, осадки, ветер)
   - Мероприятия в радиусе 3 км от Ленинградского вокзала (55.7762°N, 37.6555°E)
   - RAG-поиск по регламентам: релевантные нормы по нагрузке

2. **deepseek.py** формирует промпт:
   - Системный: роль аналитика железнодорожного вокзала, знающего регламенты
   - Пользовательский: структурированный контекст (погода, события, история потоков, нормы)
   - Инструкция: вернуть справку в заданном формате (ожидания + рекомендации)

3. Ответ сохраняется в `forecast_log` и отправляется в Telegram

---

## Формат ответа бота (шаблон промпта)

```
Дата: {date}

ПРОГНОЗ:
- [факторы, влияющие на пассажиропоток]

ОЖИДАЕМАЯ НАГРУЗКА:
- [по временным окнам]

РЕКОМЕНДАЦИИ:
- [конкретные действия с временными диапазонами]
```

---

## Команды Telegram-бота

| Команда | Действие |
|---|---|
| `/start` | Приветствие, регистрация chat_id |
| `/forecast` | Запросить прогноз на завтра прямо сейчас |
| `как планировать ДД.ММ?` | Прогноз на конкретную дату |
| `/status` | Статус системы (последний запуск, ошибки) |

---

## Виртуальное окружение

```bash
python -m venv .venv
.venv\Scripts\activate       # Windows
pip install -r requirements.txt
```

`.gitignore` исключает `.venv/` и `.env` — другие проекты не затрагиваются.

---

## Ключевые зависимости (requirements.txt)

```
python-telegram-bot==20.*
apscheduler==3.*
sqlalchemy==2.*
psycopg2-binary
pgvector
sentence-transformers
openai          # DeepSeek совместим с openai SDK
httpx
python-dotenv
pandas
pypdf2
python-docx
```

---

## .env (шаблон)

```env
# Telegram
TELEGRAM_TOKEN=ваш_токен_бота
TELEGRAM_CHAT_ID=ваш_chat_id

# PostgreSQL
DATABASE_URL=postgresql://user:password@localhost:5432/airailway

# DeepSeek
DEEPSEEK_API_KEY=ваш_ключ
DEEPSEEK_BASE_URL=https://api.deepseek.com

# Расписание
DAILY_FORECAST_HOUR=7
DAILY_FORECAST_MINUTE=0

# Геолокация вокзала
STATION_LAT=55.7762
STATION_LON=37.6555
STATION_NAME=Ленинградский вокзал
```

---

## Порядок разработки

1. Инициализация: структура папок, `.env`, `requirements.txt`, `venv`
2. БД: подключение, создание таблиц (`models.py`)
3. Загрузчики: `csv_loader.py` → `doc_loader.py`
4. Парсеры: `weather.py` → `events.py`
5. Аналитика: `aggregator.py` + `rag.py`
6. LLM: `deepseek.py` с промптом
7. Бот: `handlers.py` + `scheduler.py` + `main.py`
8. Тестирование полного цикла
