"""
Планировщик ежедневного дайджеста на неделю вперёд (в 7:00 МСК) — v2.1.

Изменения:
- Нормативное оборудование (все 17 рамок, 17 турникетов)
- 6 дней вперёд (без сегодня)
- Переиспользование данных для будущих дней с правильными датами
- Fallback с min_equipment
"""
import logging
from datetime import date, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application

from src.analytics.calculator import calculate_day_range_normative, get_available_dates, HourResult
from src.llm.deepseek import format_weekly
from src.db.session import SessionLocal
from src.db.models import ForecastLog
from src.config import TELEGRAM_CHAT_ID, DAILY_DIGEST_HOUR, DAILY_DIGEST_MINUTE, FRAMES_TOTAL, TURNSTILES_TOTAL

logger = logging.getLogger(__name__)

RUS_WEEKDAYS = {
    0: "понедельник", 1: "вторник", 2: "среда", 3: "четверг",
    4: "пятница", 5: "суббота", 6: "воскресенье",
}


async def send_weekly_digest(app: Application):
    """Задача планировщика: формирует и отправляет дайджест на 6 дней вперёд."""
    logger.info("Планировщик: запуск ежедневного дайджеста")
    today = date.today()

    try:
        available = get_available_dates()
        if not available:
            logger.warning("Нет данных для дайджеста")
            await app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text="⚠️ Нет данных для формирования дайджеста. Загрузите Excel-файл.",
            )
            return

        # Следующие 6 дней (без сегодня)
        weekdays_needed = []
        for i in range(1, 7):
            d = today + timedelta(days=i)
            weekdays_needed.append(d)

        # Для каждой будущей даты ищем данные того же дня недели
        results_by_date: dict[date, list[HourResult]] = {}
        for future_date in weekdays_needed:
            future_weekday = future_date.weekday()

            source_date = None
            for d in available:
                if d.weekday() == future_weekday:
                    source_date = d
                    break

            if source_date is None:
                logger.warning("Нет данных для дня недели %d (%s)", future_weekday, future_date)
                continue

            # Рассчитываем с нормативным оборудованием (17/17)
            hours = calculate_day_range_normative(source_date, source_date)
            results_by_date[future_date] = hours

        if not results_by_date:
            logger.warning("Нет данных ни для одного дня недели")
            await app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text="⚠️ Нет данных для формирования дайджеста на неделю.",
            )
            return

        # Форматируем через DeepSeek
        try:
            formatted = await format_weekly(results_by_date)
            final_text = f"📆 <b>ожидаемые прогнозы на неделю</b>\n\n{formatted}"
        except Exception as e:
            logger.warning("DeepSeek недоступен, используем fallback: %s", e)
            final_text = _build_fallback_digest(results_by_date)

        # Лог
        session = SessionLocal()
        try:
            log = ForecastLog(
                request_type="weekly",
                input_json={
                    "date_from": str(min(results_by_date.keys())),
                    "date_to": str(max(results_by_date.keys())),
                    "normative": True,
                },
                response_text=final_text,
            )
            session.add(log)
            session.commit()
        finally:
            session.close()

        await _send_multi(app.bot, TELEGRAM_CHAT_ID, final_text)
        logger.info(
            "Ежедневный дайджест отправлен (%s – %s)",
            min(results_by_date.keys()), max(results_by_date.keys()),
        )

    except Exception as e:
        logger.exception("Ошибка в ежедневном дайджесте")
        await app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"⚠️ Ошибка при формировании ежедневного дайджеста: {e}",
        )


def _build_fallback_digest(results_by_date: dict) -> str:
    """Fallback-текст дайджеста без DeepSeek (v2.2: разделение направлений)."""
    from src.llm.deepseek import _build_direction_html

    lines = ["📆 <b>ожидаемые прогнозы на неделю</b>"]

    for dt, hours in sorted(results_by_date.items()):
        weekday = RUS_WEEKDAYS.get(dt.weekday(), "?")
        lines.append(f"\n<b>{dt.strftime('%d.%m')} {weekday}</b>")

        # Отправления (рамки)
        departing_lines = _build_direction_html(hours, "departing")
        if departing_lines:
            lines.extend(departing_lines)

        # Прибытия (турникеты)
        arriving_lines = _build_direction_html(hours, "arriving")
        if arriving_lines:
            lines.extend(arriving_lines)

    return "\n".join(lines)


def _split_html(text: str, max_len: int = 4000) -> list[str]:
    """Разбивает HTML-текст на части по границам абзацев для Telegram."""
    if len(text) <= max_len:
        return [text]

    parts = []
    paragraphs = text.split("\n")
    current = ""

    for p in paragraphs:
        if len(current) + len(p) + 1 <= max_len:
            current = (current + "\n" + p) if current else p
        else:
            if current:
                parts.append(current)
            if len(p) > max_len:
                for i in range(0, len(p), max_len):
                    parts.append(p[i:i + max_len])
                current = ""
            else:
                current = p

    if current:
        parts.append(current)

    return parts


async def _send_multi(bot, chat_id: int, text: str):
    """Отправляет текст одним или несколькими сообщениями."""
    parts = _split_html(text)
    for part in parts:
        await bot.send_message(chat_id=chat_id, text=part, parse_mode="HTML")


def setup_scheduler(app: Application) -> AsyncIOScheduler:
    """Создаёт и настраивает планировщик."""
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(
        send_weekly_digest,
        trigger="cron",
        hour=DAILY_DIGEST_HOUR,
        minute=DAILY_DIGEST_MINUTE,
        kwargs={"app": app},
    )
    logger.info(
        "Планировщик настроен: дайджест в %02d:%02d МСК (норматив %d рамок/%d турникетов, 6 дней вперёд)",
        DAILY_DIGEST_HOUR, DAILY_DIGEST_MINUTE,
        FRAMES_TOTAL, TURNSTILES_TOTAL,
    )
    return scheduler
