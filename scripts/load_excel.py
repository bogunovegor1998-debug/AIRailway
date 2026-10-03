"""
Скрипт загрузки данных из Excel-файлов в БД — v2.0.

Поддерживает два формата:
1. Лен_вокзал_*.xlsx — плоская таблица (дата, час, все метрики)
2. Данные по ПТВ.xlsx — даты по столбцам, станции по строкам

Запуск: python -m scripts.load_excel [путь_к_файлу.xlsx]
Если путь не указан — сканирует data/*.xlsx и загружает все.
"""
import sys
import os
import logging
from datetime import date, datetime

import pandas as pd

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db.session import SessionLocal, engine
from src.db.models import Base, HourlyData, PTVData, PTVHourlyData
from src.config import DATA_DIR

logger = logging.getLogger(__name__)

# ========================
# Парсер Лен_вокзал_*.xlsx (плоская таблица)
# ========================

# Сопоставление названий столбцов в разных вариантах написания
COLUMN_MAP = {
    "день недели": "date",
    "дата": "date",
    "час": "hour",
    "отправленные пассажиры": "passengers_departing",
    "отправленные поезда": "trains_departing",
    "прибывшие пассажиры": "passengers_arriving",
    "прибывшие поезда": "trains_arriving",
    "рамки для отправляющихся": "frames_working",
    "рамки": "frames_working",
    "турникеты для прибывающих": "turnstiles_working",
    "турникеты": "turnstiles_working",
}


def parse_flat_excel(filepath: str) -> list[dict]:
    """
    Парсит плоский формат Excel (Лен_вокзал_*.xlsx):

    Столбцы: день недели | час | отправленные пассажиры | отправленные поезда |
             прибывшие пассажиры | прибывшие поезда |
             рамки для отправляющихся | турникеты для прибывающих

    Часы: 0-23. Пассажиры: округляются до целых.
    """
    logger.info("Чтение плоского файла: %s", filepath)
    df = pd.read_excel(filepath, header=0, engine="openpyxl")

    # Приводим названия колонок к нижнему регистру
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Сопоставляем колонки
    col_mapping = {}
    for col in df.columns:
        for key, db_col in COLUMN_MAP.items():
            if key in col:
                col_mapping[col] = db_col
                break

    logger.info("Найдено колонок: %d из %d", len(col_mapping), len(df.columns))
    logger.info("Сопоставление: %s", col_mapping)

    df = df.rename(columns=col_mapping)

    # Оставляем только известные колонки
    needed = list(col_mapping.values())
    missing = [c for c in ["date", "hour"] if c not in df.columns]
    if missing:
        logger.error("Не найдены обязательные колонки: %s", missing)
        return []

    records = []
    for _, row in df.iterrows():
        try:
            # Дата
            dt = row["date"]
            if isinstance(dt, datetime):
                dt = dt.date()
            elif isinstance(dt, str):
                dt = pd.Timestamp(dt).date()
            else:
                dt = pd.Timestamp(dt).date()

            # Час
            h = int(row["hour"])

            # Пассажиры (округляем)
            p_arr = int(round(float(row.get("passengers_arriving", 0) or 0)))
            p_dep = int(round(float(row.get("passengers_departing", 0) or 0)))

            # Поезда
            t_arr = int(row.get("trains_arriving", 0) or 0)
            t_dep = int(row.get("trains_departing", 0) or 0)

            # Оборудование
            frames = int(row.get("frames_working", 0) or 0)
            turnstiles = int(row.get("turnstiles_working", 0) or 0)

            records.append({
                "date": dt,
                "hour": h,
                "passengers_arriving": p_arr,
                "passengers_departing": p_dep,
                "trains_arriving": t_arr,
                "trains_departing": t_dep,
                "frames_working": frames,
                "turnstiles_working": turnstiles,
                "source_file": os.path.basename(filepath),
            })
        except Exception as e:
            logger.warning("Пропущена строка (ошибка): %s — %s", row.to_dict(), e)

    logger.info(
        "Загружено записей из %s: %d (даты: %s .. %s)",
        os.path.basename(filepath),
        len(records),
        records[0]["date"] if records else "—",
        records[-1]["date"] if records else "—",
    )
    return records


# ========================
# Парсер Данные по ПТВ.xlsx (даты по столбцам)
# ========================

STATION_NAMES_MAP = {
    "комсомольская (сокольническая": "Комсомольская (Сокольническая линия)",
    "комсомольская (кольцевая": "Комсомольская (Кольцевая линия)",
    "комсомольская кл": "Комсомольская КЛ",
    "комсомольская (сокольническая)": "Комсомольская (Сокольническая линия)",
    "площадь трех вокзалов (д2)": "Площадь трех вокзалов (Д2)",
    "площадь трех вокзалов (д4)": "Площадь трех вокзалов (Д4)",
    "площадь трех вокзалов мцд-2": "Площадь трех вокзалов МЦД-2",
    "площадь трех вокзалов мцд-4": "Площадь трех вокзалов МЦД-4",
}


def parse_ptv_excel(filepath: str) -> list[dict]:
    """
    Парсит файл Данные по ПТВ.xlsx.

    Формат:
    - Строка 1: заголовки (первая колонка — название, остальные — даты)
    - Строки 2+: каждая — станция, значения в тыс. пасс./сутки по дням
    """
    logger.info("Чтение ПТВ-файла: %s", filepath)
    df = pd.read_excel(filepath, header=0, engine="openpyxl")

    # Первая колонка — названия станций (заголовок может быть разным)
    station_col = df.columns[0]
    date_cols = df.columns[1:]

    # Парсим даты из заголовков
    parsed_dates = []
    for c in date_cols:
        try:
            if isinstance(c, datetime):
                parsed_dates.append(c.date())
            elif isinstance(c, date):
                parsed_dates.append(c)
            else:
                parsed_dates.append(pd.Timestamp(c).date())
        except Exception:
            logger.warning("Не удалось распарсить дату из заголовка: %s", c)
            parsed_dates.append(None)

    records = []
    for _, row in df.iterrows():
        raw_name = str(row[station_col]).strip().lower()

        # Ищем соответствие
        station_name = None
        for key, display_name in STATION_NAMES_MAP.items():
            if key in raw_name:
                station_name = display_name
                break

        if station_name is None:
            logger.debug("Пропущена станция: %s", row[station_col])
            continue

        for col_idx, col_name in enumerate(date_cols):
            dt = parsed_dates[col_idx]
            if dt is None:
                continue

            val = row[col_name]
            if pd.isna(val):
                continue

            records.append({
                "date": dt,
                "station_name": station_name,
                "passengers_per_day": float(val),
            })

    logger.info(
        "Загружено ПТВ-записей из %s: %d (даты: %s .. %s, станций: %d)",
        os.path.basename(filepath),
        len(records),
        records[0]["date"] if records else "—",
        records[-1]["date"] if records else "—",
        len(set(r["station_name"] for r in records)),
    )
    return records


# ========================
# Парсер Данные по ПТВ_2.xlsx (почасовой + дни недели)
# ========================

def parse_ptv_hourly(filepath: str) -> list[dict]:
    """
    Парсит файл Данные по ПТВ_2.xlsx.

    Формат:
    - Столбцы: День недели | Час | Станция АСОП | Отправлено пассажиров по дням
    - День недели: 1–7 (1 = понедельник)
    - Час: 0–23
    - Станция АСОП: название (Комсомольская КЛ, Комсомольская (СОКОЛЬНИЧЕСКАЯ), ...)
    - Пассажиры: float (может быть дробным)
    """
    logger.info("Чтение почасового ПТВ-файла: %s", filepath)
    df = pd.read_excel(filepath, header=0, engine="openpyxl")

    # Приводим названия колонок к нижнему регистру
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Ищем нужные колонки
    weekday_col = None
    hour_col = None
    station_col = None
    passengers_col = None

    for col in df.columns:
        cl = col.lower()
        if "день недели" in cl:
            weekday_col = col
        elif "час" in cl and "пасс" not in cl:
            hour_col = col
        elif "станция" in cl or "асоп" in cl:
            station_col = col
        elif "отправлено" in cl or "пассажир" in cl:
            passengers_col = col

    missing = []
    if weekday_col is None:
        missing.append("день недели")
    if hour_col is None:
        missing.append("час")
    if station_col is None:
        missing.append("станция АСОП")
    if passengers_col is None:
        missing.append("пассажиры")
    if missing:
        logger.error("Не найдены обязательные колонки: %s", missing)
        return []

    logger.info("Найдены колонки: weekday=%s, hour=%s, station=%s, passengers=%s",
                weekday_col, hour_col, station_col, passengers_col)

    records = []
    for _, row in df.iterrows():
        try:
            weekday = int(row[weekday_col])
            hour = int(row[hour_col])
            raw_name = str(row[station_col]).strip().lower()
            passengers = float(row[passengers_col]) if pd.notna(row[passengers_col]) else 0.0

            if weekday < 1 or weekday > 7:
                continue
            if hour < 0 or hour > 23:
                continue

            # Ищем соответствие станции
            station_name = None
            for key, display_name in STATION_NAMES_MAP.items():
                if key in raw_name:
                    station_name = display_name
                    break

            if station_name is None:
                logger.debug("Пропущена станция (неизвестная): %s", raw_name)
                continue

            records.append({
                "weekday": weekday,
                "hour": hour,
                "station_name": station_name,
                "passengers": passengers,
            })
        except Exception as e:
            logger.warning("Пропущена строка (ошибка): %s — %s", row.to_dict(), e)

    logger.info(
        "Загружено почасовых ПТВ-записей из %s: %d (станций: %d)",
        os.path.basename(filepath),
        len(records),
        len(set(r["station_name"] for r in records)),
    )
    return records


# ========================
# Функции автоопределения формата и загрузки
# ========================

PTV_KEYWORDS = ["птв", "мцд", "комсомоль", "площадь трех вокзалов"]
PTV_HOURLY_KEYWORDS = ["станция асоп", "отправлено пассажиров по дням"]


def guess_format(filepath: str) -> str:
    """Определяет формат файла по содержимому первой строки."""
    filename = os.path.basename(filepath).lower()

    # Сначала проверяем первую строку содержимого (более надёжно)
    try:
        df = pd.read_excel(filepath, header=None, nrows=1, engine="openpyxl")
        first_row = " ".join(str(c).lower() for c in df.iloc[0].tolist() if pd.notna(c))

        # Почасовой ПТВ: колонки «станция асоп», «отправлено пассажиров по дням»
        for kw in PTV_HOURLY_KEYWORDS:
            if kw in first_row:
                return "ptv_hourly"

        for kw in PTV_KEYWORDS:
            if kw in first_row:
                return "ptv"
    except Exception:
        pass

    # Проверяем по имени файла
    if "птв_2" in filename:
        return "ptv_hourly"

    for kw in PTV_KEYWORDS:
        if kw in filename:
            return "ptv"

    return "flat"


def find_excel_files() -> list[str]:
    """Ищет все .xlsx файлы в data/."""
    data_dir = os.path.abspath(DATA_DIR)
    if not os.path.isdir(data_dir):
        logger.warning("Папка data/ не найдена: %s", data_dir)
        return []
    files = [
        os.path.join(data_dir, f)
        for f in os.listdir(data_dir)
        if f.endswith(".xlsx") and not f.startswith("~")
    ]
    logger.info("Найдено Excel-файлов: %d", len(files))
    return files


def upsert_hourly(records: list[dict]):
    """Вставляет или обновляет записи в hourly_data."""
    if not records:
        return
    session = SessionLocal()
    try:
        for rec in records:
            existing = (
                session.query(HourlyData)
                .filter(
                    HourlyData.date == rec["date"],
                    HourlyData.hour == rec["hour"],
                )
                .first()
            )

            if existing:
                for field in [
                    "passengers_arriving", "passengers_departing",
                    "trains_arriving", "trains_departing",
                    "frames_working", "turnstiles_working",
                ]:
                    setattr(existing, field, rec[field])
                existing.source_file = rec["source_file"]
            else:
                session.add(HourlyData(**rec))

        session.commit()
        logger.info("hourly_data: обновлено/вставлено %d записей", len(records))
    except Exception as e:
        session.rollback()
        logger.error("Ошибка при сохранении hourly_data: %s", e)
        raise
    finally:
        session.close()


def upsert_ptv(records: list[dict]):
    """Вставляет или обновляет записи в ptv_data."""
    if not records:
        return
    session = SessionLocal()
    try:
        for rec in records:
            existing = (
                session.query(PTVData)
                .filter(
                    PTVData.date == rec["date"],
                    PTVData.station_name == rec["station_name"],
                )
                .first()
            )

            if existing:
                existing.passengers_per_day = rec["passengers_per_day"]
            else:
                session.add(PTVData(**rec))

        session.commit()
        logger.info("ptv_data: обновлено/вставлено %d записей", len(records))
    except Exception as e:
        session.rollback()
        logger.error("Ошибка при сохранении ptv_data: %s", e)
        raise
    finally:
        session.close()


def upsert_ptv_hourly(records: list[dict]):
    """Вставляет или обновляет записи в ptv_hourly_data."""
    if not records:
        return
    session = SessionLocal()
    try:
        for rec in records:
            existing = (
                session.query(PTVHourlyData)
                .filter(
                    PTVHourlyData.weekday == rec["weekday"],
                    PTVHourlyData.hour == rec["hour"],
                    PTVHourlyData.station_name == rec["station_name"],
                )
                .first()
            )

            if existing:
                existing.passengers = rec["passengers"]
            else:
                session.add(PTVHourlyData(**rec))

        session.commit()
        logger.info("ptv_hourly_data: обновлено/вставлено %d записей", len(records))
    except Exception as e:
        session.rollback()
        logger.error("Ошибка при сохранении ptv_hourly_data: %s", e)
        raise
    finally:
        session.close()


def load_all():
    """Загружает все Excel-файлы из data/."""
    Base.metadata.create_all(bind=engine)
    files = find_excel_files()
    # Сортируем по дате изменения: новые файлы загружаются последними
    files.sort(key=lambda f: os.path.getmtime(f))

    total_hourly = 0
    total_ptv = 0
    total_ptv_hourly = 0

    for fp in files:
        try:
            fmt = guess_format(fp)
            logger.info("Файл %s → формат: %s", os.path.basename(fp), fmt)

            if fmt == "ptv_hourly":
                records = parse_ptv_hourly(fp)
                if records:
                    upsert_ptv_hourly(records)
                    total_ptv_hourly += len(records)
            elif fmt == "ptv":
                records = parse_ptv_excel(fp)
                if records:
                    upsert_ptv(records)
                    total_ptv += len(records)
            else:
                records = parse_flat_excel(fp)
                if records:
                    upsert_hourly(records)
                    total_hourly += len(records)
        except Exception as e:
            logger.error("Ошибка обработки %s: %s", fp, e)

    logger.info(
        "Загрузка завершена. hourly_data=%d, ptv_data=%d, ptv_hourly_data=%d",
        total_hourly, total_ptv, total_ptv_hourly,
    )
    return total_hourly + total_ptv + total_ptv_hourly


if __name__ == "__main__":
    # Настройка логирования при прямом запуске
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        logger.info("Загрузка указанного файла: %s", filepath)
        Base.metadata.create_all(bind=engine)

        fmt = guess_format(filepath)
        if fmt == "ptv_hourly":
            records = parse_ptv_hourly(filepath)
            if records:
                upsert_ptv_hourly(records)
        elif fmt == "ptv":
            records = parse_ptv_excel(filepath)
            if records:
                upsert_ptv(records)
        else:
            records = parse_flat_excel(filepath)
            if records:
                upsert_hourly(records)
        logger.info("Готово.")
    else:
        load_all()
