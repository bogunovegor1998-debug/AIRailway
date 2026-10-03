"""
Парсер прогноза погоды через wttr.in (бесплатно, без API-ключа).
Возвращает прогноз на сегодня для указанного города.
Описание погоды переводится на русский через DeepSeek.
"""
import logging
from datetime import date

import httpx

from src.config import WEATHER_CITY

logger = logging.getLogger(__name__)


async def _translate_weather(text_en: str) -> str:
    """Переводит описание погоды с английского на русский через DeepSeek."""
    if not text_en or not text_en.strip():
        return text_en

    try:
        from src.llm.deepseek import _call_deepseek

        prompt = (
            "Переведи на русский язык следующее описание погоды. "
            "Верни ТОЛЬКО перевод, без пояснений, без кавычек:\n\n"
            f"{text_en}"
        )
        result = await _call_deepseek(prompt, prompt)
        return result.strip()
    except Exception as e:
        logger.warning("Не удалось перевести описание погоды: %s", e)
        return text_en


async def get_weather_forecast(target_date: date | None = None) -> dict | None:
    """
    Получает прогноз погоды на сегодня (или target_date) через wttr.in.

    Возвращает словарь:
    {
        "temp_min": float,      # минимальная температура °C
        "temp_max": float,      # максимальная температура °C
        "description": str,     # описание на русском
        "precip_mm": float,     # осадки мм
        "wind_kmh": float,      # максимальная скорость ветра км/ч
        "humidity": int,        # влажность %
        "summary": str,         # готовая строка для вставки в сообщение
    }
    или None при ошибке.
    """
    if target_date is None:
        target_date = date.today()

    target_str = target_date.strftime("%Y-%m-%d")

    try:
        url = f"https://wttr.in/{WEATHER_CITY}?format=j1"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        # Ищем нужный день в прогнозе
        weather_days = data.get("weather", [])
        day_data = None
        for wd in weather_days:
            if wd.get("date") == target_str:
                day_data = wd
                break

        if day_data is None:
            logger.warning("Дата %s не найдена в прогнозе для %s", target_str, WEATHER_CITY)
            return None

        temp_min = float(day_data.get("mintempC", 0))
        temp_max = float(day_data.get("maxtempC", 0))

        # Берём описание из часового прогноза около полудня (индекс 2-4 из 8)
        hourly = day_data.get("hourly", [])
        description_en = "—"
        precip_mm = 0.0
        wind_kmh = 0.0
        humidity = 0

        if hourly:
            # Выбираем час ближе к полудню или первый доступный
            midday_idx = min(len(hourly) // 2, len(hourly) - 1)
            midday_hour = hourly[midday_idx]

            desc_list = midday_hour.get("weatherDesc", [])
            if desc_list:
                description_en = desc_list[0].get("value", "—").strip()

            precip_mm = float(midday_hour.get("precipMM", 0))
            wind_kmh = float(midday_hour.get("windspeedKmph", 0))
            humidity = int(float(midday_hour.get("humidity", 0)))

        # Переводим описание на русский
        description = await _translate_weather(description_en) if description_en != "—" else "—"

        summary = _build_summary(temp_min, temp_max, description, precip_mm, wind_kmh, humidity)

        return {
            "temp_min": temp_min,
            "temp_max": temp_max,
            "description": description,
            "precip_mm": precip_mm,
            "wind_kmh": wind_kmh,
            "humidity": humidity,
            "summary": summary,
        }

    except httpx.HTTPError as e:
        logger.warning("Ошибка HTTP при запросе погоды: %s", e)
        return None
    except Exception as e:
        logger.warning("Не удалось получить прогноз погоды: %s", e)
        return None


def _build_summary(
    temp_min: float,
    temp_max: float,
    description: str,
    precip_mm: float,
    wind_kmh: float,
    humidity: int,
) -> str:
    """Формирует краткую текстовую сводку погоды (описание уже на русском)."""
    parts = []

    if temp_min == temp_max:
        parts.append(f"🌡 {temp_min:.0f}°C")
    else:
        parts.append(f"🌡 {temp_min:.0f}–{temp_max:.0f}°C")

    if description and description != "—":
        parts.append(f"☁ {description}")

    if humidity > 0:
        parts.append(f"💧 {humidity}%")

    if precip_mm > 1:
        parts.append(f"🌧 {precip_mm:.1f} мм")

    if wind_kmh > 30:
        parts.append(f"💨 {wind_kmh:.0f} км/ч")

    return " | ".join(parts)
