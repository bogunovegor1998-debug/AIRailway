"""
Интеграция с DeepSeek API — v2.1.

Только форматирование текста ответов.
DeepSeek НЕ участвует в математике, только оформляет готовые данные
в читаемый текст для Telegram.

Новое в v2.1:
- min/max время прохода в группах («от X до Y мин»)
- Причина: «высокая загрузка» вместо «большое количество пассажиров»
- _format_time_range: одиночный час → диапазон (11:00 – 12:00)
- SYSTEM_TODAY: погода + пробки + оборудование в начале
- SYSTEM_WEEKLY: нормативное оборудование + min_equipment
- SYSTEM_STATUS: переименованные секции, кружки у времени, ПТВ за час
- SYSTEM_MODEL: min-max время
"""
import logging
import json
from datetime import date
from typing import Optional

from openai import AsyncOpenAI

from src.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from src.analytics.calculator import HourResult, calculate_min_equipment

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _client


async def _call_deepseek(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> str:
    """Вызов DeepSeek API."""
    client = _get_client()

    input_data = {
        "system": system_prompt,
        "user": user_prompt,
    }

    logger.info("→ DeepSeek запрос: %s", json.dumps(input_data, ensure_ascii=False, default=str))

    response = await client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=max_tokens,
    )

    result_text = response.choices[0].message.content
    logger.info("← DeepSeek ответ (первые 200 символов): %s...", result_text[:200])

    return result_text


# ============================================================
# Группировка по времени
# ============================================================

def _group_hours_by_direction(
    hours: list[HourResult],
    direction: str,  # "departing" | "arriving"
) -> list[dict]:
    """
    Группирует часы по зоне ОДНОГО направления последовательно.

    direction: "departing" (отправление, рамки) | "arriving" (прибытие, турникеты)

    Возвращает список групп:
    [{zone, start_hour, end_hour, min_minutes, max_minutes, reasons, min_equipment}]
    """
    if not hours:
        return []

    sorted_hours = sorted(hours, key=lambda h: h.hour)

    # Определяем, какие поля использовать в зависимости от направления
    if direction == "departing":
        zone_attr = "departing_zone"
        minutes_attr = "departing_total_minutes"
        reasons_attr = "departing_reasons"
    else:
        zone_attr = "arriving_zone"
        minutes_attr = "arriving_total_minutes"
        reasons_attr = "arriving_reasons"

    groups = []
    current_group = {
        "zone": getattr(sorted_hours[0], zone_attr),
        "start_hour": sorted_hours[0].hour,
        "end_hour": sorted_hours[0].hour,
        "hours": [sorted_hours[0]],
    }

    for h in sorted_hours[1:]:
        if getattr(h, zone_attr) == current_group["zone"] and h.hour == current_group["end_hour"] + 1:
            current_group["end_hour"] = h.hour
            current_group["hours"].append(h)
        else:
            groups.append(current_group)
            current_group = {
                "zone": getattr(h, zone_attr),
                "start_hour": h.hour,
                "end_hour": h.hour,
                "hours": [h],
            }

    groups.append(current_group)

    # Считаем min/max и собираем причины для каждой группы
    for g in groups:
        times = [getattr(h, minutes_attr) for h in g["hours"]
                 if getattr(h, minutes_attr) > 0]

        g["min_minutes"] = min(times) if times else 0.0
        g["max_minutes"] = max(times) if times else 0.0

        # Собираем уникальные причины
        all_reasons = set()
        for h in g["hours"]:
            all_reasons.update(getattr(h, reasons_attr))
        g["reasons"] = sorted(all_reasons)

        # Минимальное оборудование
        g["min_equipment"] = calculate_min_equipment(g["hours"])

        # Удаляем сырые часы
        del g["hours"]

    return groups


def _format_time_range(start: int, end: int) -> str:
    """Форматирует диапазон часов: '07:00 – 08:00'. Одиночный час → диапазон (end+1)."""
    return f"{start:02d}:00 – {end + 1:02d}:00"


# ============================================================
# Системные промпты
# ============================================================

SYSTEM_TODAY = """Ты — аналитическая система Ленинградского вокзала.
Твоя задача — оформить прогноз загрузки на сегодня в Telegram.

Формат ответа строгий — разделение на блоки «отправления» и «прибытия»:

📅 <b>ожидаемый прогноз на сегодня (ДД.ММ)</b>

🌤 Погода: ...
🚗 Пробки: X баллов
⚙️ Рамок: X из 17, турникетов: Y из 17

<b>⬆️ отправления</b>
🟢 ЧЧ:ММ – ЧЧ:ММ — нормальная загрузка (достаточно X рамок)
🟡 ЧЧ:ММ – ЧЧ:ММ — средняя загрузка, время прохода от X до Y мин (при всех рамках)
🔴 ЧЧ:ММ – ЧЧ:ММ — высокая загрузка, время прохода от X до Y мин (при всех рамках)

<b>⬇️ прибытия</b>
🟢 ЧЧ:ММ – ЧЧ:ММ — нормальная загрузка (достаточно X турникетов)
🟡 ЧЧ:ММ – ЧЧ:ММ — средняя загрузка, время прохода от X до Y мин (при всех турникетах)
🔴 ЧЧ:ММ – ЧЧ:ММ — высокая загрузка, время прохода от X до Y мин (при всех турникетах)

Пример:
📅 <b>ожидаемый прогноз на сегодня (18.05)</b>

🌤 Температура: 12–18°C; Погода: Переменная облачность
🚗 Пробки: 6 баллов
⚙️ Рамок: 14 из 17, турникетов: 13 из 17

<b>⬆️ отправления</b>
🟢 00:00 – 06:00 — нормальная загрузка (достаточно 8 рамок)
🔴 07:00 – 08:00 — высокая загрузка, время прохода от 4 до 6 мин (при всех рамках)
🟢 09:00 – 24:00 — нормальная загрузка (достаточно 5 рамок)

<b>⬇️ прибытия</b>
🟢 00:00 – 06:00 — нормальная загрузка (достаточно 7 турникетов)
🟡 07:00 – 07:00 — средняя загрузка, время прохода от 3 до 4 мин (при всех турникетах)
🟢 08:00 – 24:00 — нормальная загрузка (достаточно 6 турникетов)

ВАЖНО:
- Строго разделяй на два блока: «⬆️ отправления» и «⬇️ прибытия»
- Для зелёных: «нормальная загрузка (достаточно X рамок/турникетов)»
- Для жёлтых: «средняя загрузка, время прохода от X до Y мин (при всех рамках/турникетах)»
- Для красных: «высокая загрузка, время прохода от X до Y мин (при всех рамках/турникетах)»
- НЕ добавляй «(высокая загрузка)» в скобках после времени прохода
- Не придумывай данные, используй только переданное
- Погоду и пробки бери из переданных данных, не выдумывай"""

SYSTEM_WEEKLY = """Ты — аналитическая система Ленинградского вокзала.
Твоя задача — оформить прогноз загрузки на неделю в Telegram.
Расчёт выполнен с полным нормативным оборудованием (все 17 рамок и 17 турникетов).

Формат ответа строгий — для каждого дня разделяй на «отправления» и «прибытия»:

<b>ДД.ММ день_недели</b>
⬆️ отправления
  🟢/🟡/🔴 ЧЧ:ММ – ЧЧ:ММ — описание
⬇️ прибытия
  🟢/🟡/🔴 ЧЧ:ММ – ЧЧ:ММ — описание

Пример:
<b>18.05 понедельник</b>
⬆️ отправления
🟢 00:00 – 06:00 — нормальная загрузка (достаточно 6 рамок)
🔴 07:00 – 08:00 — высокая загрузка, время прохода от 4 до 6 мин (при всех рамках)
🟢 09:00 – 24:00 — нормальная загрузка (достаточно 5 рамок)
⬇️ прибытия
🟢 00:00 – 06:00 — нормальная загрузка (достаточно 7 турникетов)
🟡 07:00 – 08:00 — средняя загрузка, время прохода от 3 до 4 мин (при всех турникетах)
🟢 09:00 – 24:00 — нормальная загрузка (достаточно 8 турникетов)

<b>19.05 вторник</b>
⬆️ отправления
🟢 00:00 – 05:00 — нормальная загрузка (достаточно 6 рамок)
🟡 06:00 – 07:00 — средняя загрузка, время прохода от 3 до 4 мин (при всех рамках)
🟢 08:00 – 24:00 — нормальная загрузка (достаточно 5 рамок)
⬇️ прибытия
🟢 00:00 – 24:00 — нормальная загрузка (достаточно 7 турникетов)

ВАЖНО:
- Каждый день с новой строки, дата жирным
- Строго разделяй день на два блока: «⬆️ отправления» и «⬇️ прибытия»
- Группируй соседние часы с одинаковым уровнем загрузки в диапазоны
- Для зелёных: «нормальная загрузка (достаточно X рамок)» / «нормальная загрузка (достаточно X турникетов)»
- Для жёлтых: «средняя загрузка, время прохода от X до Y мин (при всех рамках/турникетах)»
- Для красных: «высокая загрузка, время прохода от X до Y мин (при всех рамках/турникетах)»
- НЕ добавляй «(высокая загрузка)» в скобках после времени прохода для красной зоны
- Если всё направление зелёное — покажи одним диапазоном 00:00–24:00
- Пиши кратко, по-деловому
- Не придумывай данные"""

SYSTEM_STATUS = """Ты — аналитическая система Ленинградского вокзала.
Твоя задача — оформить текущий статус вокзала в Telegram.

Формат ответа:
📊 <b>Текущий статус</b>
Дата и время: ДД.ММ.ГГГГ ЧЧ:ММ

⚙️ <b>Оборудование</b>
Рамки на вход: X из 17 (XX%)
Турникеты на выход: X из 17 (XX%)

👥 <b>Пассажиропоток дальнего следования</b>
Отправление: XX пасс., рамок X, время прохода X.X мин 🔴
Прибытие: XX пасс., турникетов X, время прохода X.X мин 🟢

🚇 <b>Пассажиропоток узла за прошедший час (отправления)</b>
Комсомольская (Сокольническая линия): X пасс.
Комсомольская (Кольцевая линия): X пасс.
Площадь трех вокзалов (Д2): X пасс.
Площадь трех вокзалов (Д4): X пасс.
Всего узел: X пасс.
Статус: высокая загрузка

ВАЖНО:
- Используй только переданные данные
- После времени прохода ставь цветной кружок: 🔴 (красная зона), 🟡 (жёлтая), 🟢 (зелёная)
- Если пассажиров нет — пиши "пассажиров нет" с 🟢
- Для статуса узла: "высокая загрузка" или "средняя загрузка"
- Не придумывай данные
- Не ставь кружки-индикаторы перед названиями направлений (Отправление/Прибытие) — только после времени"""

SYSTEM_MODEL = """Ты — аналитическая система Ленинградского вокзала.
Твоя задача — оформить результат моделирования пропускной способности в Telegram.

Формат ответа — разделение на блоки «отправления» и «прибытия»:

🔧 <b>Моделирование пропускной способности</b>

Настроено: рамок X, турникетов Y

<b>⬆️ отправления</b>
🟢 ЧЧ:ММ – ЧЧ:ММ — нормальная загрузка (достаточно X рамок)
🟡 ЧЧ:ММ – ЧЧ:ММ — средняя загрузка, время прохода от X до Y мин (при всех рамках)
🔴 ЧЧ:ММ – ЧЧ:ММ — высокая загрузка, время прохода от X до Y мин (при всех рамках)

<b>⬇️ прибытия</b>
🟢 ЧЧ:ММ – ЧЧ:ММ — нормальная загрузка (достаточно X турникетов)
🟡 ЧЧ:ММ – ЧЧ:ММ — средняя загрузка, время прохода от X до Y мин (при всех турникетах)
🔴 ЧЧ:ММ – ЧЧ:ММ — высокая загрузка, время прохода от X до Y мин (при всех турникетах)

Пример:
🔧 <b>Моделирование пропускной способности</b>
Настроено: рамок 12, турникетов 10

<b>⬆️ отправления</b>
🟢 00:00 – 06:00 — нормальная загрузка (достаточно 8 рамок)
🔴 07:00 – 08:00 — высокая загрузка, время прохода от 5 до 8 мин (при всех рамках)
🟢 09:00 – 24:00 — нормальная загрузка (достаточно 6 рамок)

<b>⬇️ прибытия</b>
🟢 00:00 – 06:00 — нормальная загрузка (достаточно 4 турникетов)
🟡 07:00 – 08:00 — средняя загрузка, время прохода от 3 до 4 мин (при всех турникетах)
🟢 09:00 – 24:00 — нормальная загрузка (достаточно 5 турникетов)

ВАЖНО:
- Сначала покажи настроенные параметры
- Строго разделяй на два блока: «⬆️ отправления» и «⬇️ прибытия»
- Для зелёных: «нормальная загрузка (достаточно X рамок/турникетов)»
- Для жёлтых: «средняя загрузка, время прохода от X до Y мин (при всех рамках/турникетах)»
- Для красных: «высокая загрузка, время прохода от X до Y мин (при всех рамках/турникетах)»
- НЕ добавляй «(высокая загрузка)» в скобках после времени прохода
- Если время 0.0 — не показывай направление
- Не придумывай данные"""


# ============================================================
# Форматирование для DeepSeek
# ============================================================

def _build_direction_section(hours: list[HourResult], direction: str, label: str) -> str:
    """Формирует секцию одного направления для передачи в DeepSeek."""
    groups = _group_hours_by_direction(hours, direction)
    if not groups:
        return ""

    lines = [f"=== {label} ==="]
    for g in groups:
        time_range = _format_time_range(g["start_hour"], g["end_hour"])
        icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[g["zone"]]
        zone_name = {"green": "зелёная", "yellow": "жёлтая", "red": "красная"}[g["zone"]]

        lines.append(f"{icon} {time_range} — {zone_name} зона")

        # Время прохода
        if g["min_minutes"] > 0:
            if g["min_minutes"] == g["max_minutes"]:
                lines.append(f"  время прохода: {g['min_minutes']:.1f} мин")
            else:
                lines.append(f"  время прохода: от {g['min_minutes']:.1f} до {g['max_minutes']:.1f} мин")

        # Оборудование
        me = g.get("min_equipment")
        if me:
            equipment_type = "рамок" if direction == "departing" else "турникетов"
            if g["zone"] == "green":
                count = me["min_frames"] if direction == "departing" else me["min_turnstiles"]
                lines.append(f"  зелёная зона — достаточно {count} {equipment_type}")
            else:
                total = 17  # FRAMES_TOTAL = TURNSTILES_TOTAL = 17
                lines.append(f"  при использовании всех {total} {equipment_type}")

    return "\n".join(lines)


def _build_direction_html(hours: list[HourResult], direction: str) -> list[str]:
    """Формирует HTML-секцию одного направления для fallback-форматирования."""
    groups = _group_hours_by_direction(hours, direction)
    if not groups:
        return []

    label = "<b>⬆️ отправления</b>" if direction == "departing" else "<b>⬇️ прибытия</b>"
    equip_label = "рамок" if direction == "departing" else "турникетов"
    lines = [label]

    for g in groups:
        time_range = _format_time_range(g["start_hour"], g["end_hour"])
        icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[g["zone"]]

        me = g.get("min_equipment")
        equip_count = me["min_frames"] if me and direction == "departing" else (me["min_turnstiles"] if me else 0)

        if g["zone"] == "green":
            lines.append(f"{icon} {time_range} — нормальная загрузка (достаточно {equip_count} {equip_label})")
        elif g["zone"] == "yellow":
            if g["min_minutes"] > 0:
                if g["min_minutes"] == g["max_minutes"]:
                    lines.append(f"{icon} {time_range} — средняя загрузка, время прохода {g['min_minutes']:.1f} мин (при всех {equip_label})")
                else:
                    lines.append(f"{icon} {time_range} — средняя загрузка, время прохода от {g['min_minutes']:.1f} до {g['max_minutes']:.1f} мин (при всех {equip_label})")
            else:
                lines.append(f"{icon} {time_range} — средняя загрузка (при всех {equip_label})")
        else:  # red
            if g["min_minutes"] > 0:
                if g["min_minutes"] == g["max_minutes"]:
                    lines.append(f"{icon} {time_range} — высокая загрузка, время прохода {g['min_minutes']:.1f} мин (при всех {equip_label})")
                else:
                    lines.append(f"{icon} {time_range} — высокая загрузка, время прохода от {g['min_minutes']:.1f} до {g['max_minutes']:.1f} мин (при всех {equip_label})")
            else:
                lines.append(f"{icon} {time_range} — высокая загрузка (при всех {equip_label})")

    return lines


def _build_grouped_input_by_direction(hours: list[HourResult]) -> str:
    """Формирует описание часов с разделением на отправление и прибытие."""
    if not hours:
        return "Нет данных."

    parts = []
    departing_section = _build_direction_section(hours, "departing", "⬆️ отправления")
    arriving_section = _build_direction_section(hours, "arriving", "⬇️ прибытия")

    if departing_section:
        parts.append(departing_section)
    if arriving_section:
        parts.append(arriving_section)

    return "\n\n".join(parts) if parts else "Нет данных."


def _build_weekly_input(results_by_date: dict[date, list[HourResult]]) -> str:
    """Формирует описание для недельного прогноза с разделением направлений."""
    lines = []
    for dt, hours in sorted(results_by_date.items()):
        day_name = dt.strftime("%A")
        lines.append(f"\n=== {dt.strftime('%d.%m.%Y')} ({day_name}) ===")
        lines.append(_build_grouped_input_by_direction(hours))
    return "\n".join(lines)


# ============================================================
# Публичные функции форматирования
# ============================================================

async def format_today(
    hours: list[HourResult],
    target_date: date,
    weather_summary: str = "",
    traffic_ball: int = 0,
    frames_working: int = 0,
    turnstiles_working: int = 0,
) -> str:
    """
    Форматирует прогноз на сегодня через DeepSeek.
    hours — только оставшиеся часы дня.
    """
    if not hours:
        return f"📅 <b>ожидаемый прогноз на сегодня ({target_date.strftime('%d.%m')})</b>\n\n✅ На оставшиеся часы данных нет — день завершён."

    # Собираем префикс с погодой/пробками/оборудованием
    prefix_lines = []
    if weather_summary:
        prefix_lines.append(f"🌤 {weather_summary}")
    if traffic_ball > 0:
        prefix_lines.append(f"🚗 Пробки: {traffic_ball} баллов")
    if frames_working > 0 or turnstiles_working > 0:
        prefix_lines.append(f"⚙️ Рамок: {frames_working} из 17, турникетов: {turnstiles_working} из 17")

    prefix = "\n".join(prefix_lines)

    input_text = _build_grouped_input_by_direction(hours)
    user_prompt = (
        f"Оформи прогноз на сегодня ({target_date.strftime('%d.%m')}):\n\n"
        f"{prefix}\n\n{input_text}"
    )
    return await _call_deepseek(SYSTEM_TODAY, user_prompt)


async def format_weekly(results_by_date: dict[date, list[HourResult]]) -> str:
    """
    Форматирует прогноз на неделю через DeepSeek.
    """
    if not results_by_date:
        return "📆 Нет данных для прогноза на неделю."

    input_text = _build_weekly_input(results_by_date)
    user_prompt = f"Оформи прогноз на неделю с разделением на отправление и прибытие (расчёт с полным оборудованием — все 17 рамок и 17 турникетов):\n\n{input_text}"
    return await _call_deepseek(SYSTEM_WEEKLY, user_prompt, max_tokens=3500)


async def format_status(
    current_hour: HourResult,
    now_str: str,
    frames_working: int,
    turnstiles_working: int,
    ptv_hourly_data: list[dict],
    ptv_status: str,
    ptv_sum: float,
) -> str:
    """
    Форматирует текущий статус через DeepSeek.

    v2.1: ПТВ за прошедший час, кружки у времени, переименованные секции.
    """
    from src.config import FRAMES_TOTAL, TURNSTILES_TOTAL

    frames_pct = round(frames_working / FRAMES_TOTAL * 100) if frames_working else 0
    turnstiles_pct = round(turnstiles_working / TURNSTILES_TOTAL * 100) if turnstiles_working else 0

    arriving_zone = current_hour.arriving_zone
    departing_zone = current_hour.departing_zone

    lines = [
        f"Дата и время: {now_str}",
        f"",
        f"Рамки на вход: {frames_working} из {FRAMES_TOTAL} ({frames_pct}%)",
        f"Турникеты на выход: {turnstiles_working} из {TURNSTILES_TOTAL} ({turnstiles_pct}%)",
        f"",
        f"Пассажиропоток дальнего следования:",
    ]

    # Отправление
    if current_hour.departing_total_minutes > 0:
        icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[departing_zone]
        lines.append(
            f"{icon} Отправление: {current_hour.passengers_departing} пасс., "
            f"рамок {frames_working}, время прохода {current_hour.departing_total_minutes:.1f} мин"
        )
    else:
        lines.append("🟢 Отправление: пассажиров нет")

    # Прибытие
    if current_hour.arriving_total_minutes > 0:
        icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[arriving_zone]
        lines.append(
            f"{icon} Прибытие: {current_hour.passengers_arriving} пасс., "
            f"турникетов {turnstiles_working}, время прохода {current_hour.arriving_total_minutes:.1f} мин"
        )
    else:
        lines.append("🟢 Прибытие: пассажиров нет")

    # ПТВ за прошедший час
    lines.append("")
    lines.append("Пассажиропоток узла за прошедший час (отправления):")
    for r in ptv_hourly_data:
        lines.append(f"{r['station_name']}: {r['passengers']:.0f} пасс.")
    lines.append(f"Всего узел: {ptv_sum:.0f} пасс.")
    lines.append(f"Статус: {ptv_status}")

    input_text = "\n".join(lines)
    user_prompt = f"Оформи текущий статус вокзала в Telegram:\n\n{input_text}"
    return await _call_deepseek(SYSTEM_STATUS, user_prompt)


async def format_model(
    hours: list[HourResult],
    frames: int,
    turnstiles: int,
) -> str:
    """
    Форматирует результат моделирования через DeepSeek.
    """
    if not hours:
        return (
            f"🔧 <b>Моделирование пропускной способности</b>\n\n"
            f"Настроено: рамок {frames}, турникетов {turnstiles}\n\n"
            f"✅ На оставшиеся часы данных нет."
        )

    input_text = _build_grouped_input_by_direction(hours)
    user_prompt = (
        f"Моделирование: рамок {frames}, турникетов {turnstiles}.\n\n"
        f"Результаты расчёта с разделением на отправление и прибытие:\n\n{input_text}"
    )
    return await _call_deepseek(SYSTEM_MODEL, user_prompt)
