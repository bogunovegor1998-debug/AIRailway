"""
Загрузчик пассажиропотоков из CSV в PostgreSQL.

Ожидаемый формат CSV:
    date,hour,direction,count
    2024-01-15,8,in,1250
    2024-01-15,8,out,340

Запуск:
    python scripts/load_csv.py data/passenger_flows/2024-01.csv
"""

import pandas as pd
from pathlib import Path
from sqlalchemy.orm import Session
from src.db.session import SessionLocal
from src.db.models import PassengerFlow


REQUIRED_COLUMNS = {"date", "hour", "direction", "count"}


def load_csv(filepath: str | Path) -> int:
    """Загружает CSV с пассажиропотоками в БД. Возвращает кол-во загруженных строк."""
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Файл не найден: {filepath}")

    df = pd.read_csv(filepath)
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"В CSV отсутствуют колонки: {missing}")

    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["direction"] = df["direction"].str.strip().str.lower()
    df = df[df["direction"].isin(["in", "out"])]

    source_name = filepath.name
    rows = [
        PassengerFlow(
            date=row["date"],
            hour=int(row["hour"]),
            direction=row["direction"],
            count=int(row["count"]),
            source=source_name,
        )
        for _, row in df.iterrows()
    ]

    session: Session = SessionLocal()
    try:
        session.bulk_save_objects(rows)
        session.commit()
        return len(rows)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
