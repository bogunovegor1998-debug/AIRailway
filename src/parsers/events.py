"""
Парсер городских мероприятий через Kudago API (бесплатно, Москва).
Ищет события рядом с Ленинградским вокзалом.
"""

from datetime import date, datetime, time, timezone
import httpx
from src.config import STATION_LAT, STATION_LON, EVENTS_RADIUS_M

KUDAGO_EVENTS_URL = "https://kudago.com/public-api/v1.4/events/"


async def get_events(target_date: date) -> list[dict]:
    """
    Возвращает список мероприятий Москвы на target_date:
    [{"title": "...", "place": "...", "start_time": "20:00", "end_time": "22:00",
      "expected_visitors": None, "url": "..."}, ...]
    """
    # Kudago принимает unix-время
    dt_start = datetime.combine(target_date, time(0, 0), tzinfo=timezone.utc)
    dt_end = datetime.combine(target_date, time(23, 59), tzinfo=timezone.utc)

    params = {
        "lang": "ru",
        "fields": "id,title,place,dates,price,site_url",
        "expand": "place,dates",
        "location": "msk",
        "actual_since": int(dt_start.timestamp()),
        "actual_until": int(dt_end.timestamp()),
        "page_size": 50,
        "order_by": "publication_date",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(KUDAGO_EVENTS_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    events = []
    for item in data.get("results", []):
        place = item.get("place") or {}
        place_title = place.get("title", "неизвестно")
        place_lat = place.get("coords", {}).get("lat")
        place_lon = place.get("coords", {}).get("lon")

        # Фильтр по расстоянию, если координаты известны
        if place_lat and place_lon:
            dist = _haversine(STATION_LAT, STATION_LON, float(place_lat), float(place_lon))
            if dist > EVENTS_RADIUS_M:
                continue

        # Берём первый временной слот
        dates = item.get("dates", [])
        start_str = end_str = None
        if dates:
            start_ts = dates[0].get("start")
            end_ts = dates[0].get("end")
            if start_ts:
                start_str = datetime.fromtimestamp(start_ts).strftime("%H:%M")
            if end_ts:
                end_str = datetime.fromtimestamp(end_ts).strftime("%H:%M")

        events.append({
            "title": item.get("title", ""),
            "place": place_title,
            "start_time": start_str,
            "end_time": end_str,
            "url": item.get("site_url", ""),
        })

    return events


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя точками в метрах."""
    import math
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def format_events(events: list[dict]) -> str:
    """Текстовое резюме мероприятий."""
    if not events:
        return "Крупных мероприятий поблизости не обнаружено."
    lines = []
    for e in events:
        time_str = ""
        if e["start_time"]:
            time_str = f" {e['start_time']}"
            if e["end_time"]:
                time_str += f"–{e['end_time']}"
        lines.append(f"• {e['title']} ({e['place']}){time_str}")
    return "\n".join(lines)
