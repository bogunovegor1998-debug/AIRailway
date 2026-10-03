"""
Агрегатор: собирает все источники данных в единый контекст для LLM.
"""

import asyncio
from datetime import date, timedelta

from sqlalchemy import text

from src.db.session import SessionLocal
from src.parsers.weather import get_weather_forecast, summarize_weather
from src.parsers.events import get_events, format_events
from src.analytics.rag import search_regulations
from src.config import STATION_NAME


def _get_historical_flows(target_date: date, weeks_back: int = 4) -> list[dict]:
    """
    Возвращает средние почасовые потоки за аналогичные дни недели
    за последние weeks_back недель.
    """
    weekday = target_date.weekday()  # 0=пн, 6=вс
    # Похожие даты: тот же день недели за прошлые периоды
    similar_dates = [
        target_date - timedelta(weeks=w)
        for w in range(1, weeks_back + 1)
    ]
    date_strs = [d.isoformat() for d in similar_dates]

    sql = text("""
        SELECT hour, direction, ROUND(AVG(count)) AS avg_count
        FROM passenger_flows
        WHERE date = ANY(:dates)
        GROUP BY hour, direction
        ORDER BY hour, direction
    """)

    session = SessionLocal()
    try:
        rows = session.execute(sql, {"dates": date_strs}).fetchall()
        return [{"hour": r[0], "direction": r[1], "avg_count": int(r[2])} for r in rows]
    finally:
        session.close()


def _format_flows(flows: list[dict]) -> str:
    """Текстовая таблица пассажиропотоков по часам."""
    if not flows:
        return "Исторические данные о пассажиропотоке отсутствуют."

    by_hour: dict[int, dict] = {}
    for f in flows:
        h = f["hour"]
        if h not in by_hour:
            by_hour[h] = {"in": 0, "out": 0}
        by_hour[h][f["direction"]] = f["avg_count"]

    lines = ["Час | Вход | Выход"]
    for h in sorted(by_hour):
        lines.append(f"{h:02d}:00 | {by_hour[h]['in']} | {by_hour[h]['out']}")
    return "\n".join(lines)


async def build_context(target_date: date) -> dict:
    """
    Собирает полный контекст для генерации прогноза.
    Возвращает словарь с ключами: date, weather, events, flows, regulations
    """
    # Параллельно запрашиваем погоду и мероприятия
    weather_task = asyncio.create_task(get_weather_forecast(target_date))
    events_task = asyncio.create_task(get_events(target_date))

    hourly_weather, events = await asyncio.gather(weather_task, events_task)

    # Исторические потоки (синхронно, из БД)
    flows = _get_historical_flows(target_date)

    # RAG: ищем релевантные регламенты
    rag_query = (
        f"нормы пассажиропотока, управление турникетами, персонал, кассы, ЦОМП"
    )
    regulations = search_regulations(rag_query)

    return {
        "date": target_date.isoformat(),
        "station": STATION_NAME,
        "weather_summary": summarize_weather(hourly_weather),
        "weather_hourly": hourly_weather,
        "events_summary": format_events(events),
        "events": events,
        "flows_summary": _format_flows(flows),
        "flows": flows,
        "regulations": regulations,
    }
