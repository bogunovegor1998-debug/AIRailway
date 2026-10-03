"""
Обработчики команд и кнопок Telegram-бота — v2.4.

4 кнопки:
1. Текущий статус — оборудование, пасс. дальнего следования, ПТВ за прошедший час
2. Прогноз на сегодня — сегодняшний день недели, оставшиеся часы + погода + пробки
3. Прогноз на неделю — 6 дней вперёд с нормативным оборудованием + min_equipment
4. Моделирование — диалог: ввод рамок → ввод турникетов → расчёт
"""
import logging
from datetime import date, datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.analytics.calculator import (
    calculate_day,
    calculate_day_range,
    calculate_day_range_normative,
    calculate_hour,
    get_available_dates,
    get_ptv_data,
    get_ptv_weekly,
    get_ptv_hourly,
    HourResult,
)
from src.llm.deepseek import format_today, format_weekly, format_status, format_model
from src.db.session import SessionLocal
from src.db.models import ForecastLog, UserActivity, AuthorizedUser
from src.config import TRAFFIC_BY_WEEKDAY, FRAMES_TOTAL, TURNSTILES_TOTAL, BOT_PASSWORD

logger = logging.getLogger(__name__)


# ============================================================
# Логирование активности пользователей
# ============================================================

async def _log_user(update: Update, action: str):
    """Пишет в лог и БД информацию о пользователе."""
    user = update.effective_user
    if user is None:
        return

    chat_id = user.id
    username = user.username or "-"
    first = user.first_name or ""
    last = user.last_name or ""

    logger.info("[user=%d @%s %s %s] %s", chat_id, username, first, last, action)

    try:
        session = SessionLocal()
        try:
            session.add(UserActivity(
                telegram_id=chat_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                action=action,
            ))
            session.commit()
        finally:
            session.close()
    except Exception:
        logger.exception("Не удалось записать UserActivity в БД")


# ============================================================
# Персистентная авторизация (БД)
# ============================================================

def _is_authorized(telegram_id: int) -> bool:
    """Проверяет, авторизован ли пользователь (есть ли запись в БД)."""
    session = SessionLocal()
    try:
        return session.query(AuthorizedUser).filter(
            AuthorizedUser.telegram_id == telegram_id
        ).first() is not None
    finally:
        session.close()


def _set_authorized(telegram_id: int, username: str = None, first_name: str = None, last_name: str = None):
    """Сохраняет telegram_id в БД как авторизованного (с информацией о пользователе)."""
    session = SessionLocal()
    try:
        if not session.query(AuthorizedUser).filter(
            AuthorizedUser.telegram_id == telegram_id
        ).first():
            session.add(AuthorizedUser(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
            ))
            session.commit()
    except Exception:
        session.rollback()
        logger.exception("Не удалось записать AuthorizedUser в БД")
    finally:
        session.close()


# Дни недели по-русски
RUS_WEEKDAYS = {
    0: "понедельник", 1: "вторник", 2: "среда", 3: "четверг",
    4: "пятница", 5: "суббота", 6: "воскресенье",
}

# Контекст моделирования (храним в context.user_data)
MODEL_DATA_KEY = "model_data"

# Ключ аутентификации в user_data
AUTH_STATE_KEY = "authenticated"


# ============================================================
# Главное меню
# ============================================================

def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура главного меню v2.4."""
    keyboard = [
        [InlineKeyboardButton("📊 Текущий статус", callback_data="status")],
        [InlineKeyboardButton("📅 Прогноз на сегодня", callback_data="today")],
        [InlineKeyboardButton("📆 Прогноз на неделю", callback_data="weekly")],
        [InlineKeyboardButton("🔧 Моделирование", callback_data="model")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ============================================================
# Callback-обработчик
# ============================================================

CALLBACK_ACTIONS = {
    "today": "сегодня",
    "weekly": "неделя",
    "status": "статус",
    "model": "моделирование",
}


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на inline-кнопки."""
    query = update.callback_query
    await query.answer()

    action = CALLBACK_ACTIONS.get(query.data, query.data)
    await _log_user(update, action)

    if query.data == "today":
        await _send_today(query)
    elif query.data == "weekly":
        await _send_weekly(query)
    elif query.data == "status":
        await _send_status(query)
    elif query.data == "model":
        await _start_model_dialog(update, context)


# ============================================================
# Вспомогательные функции отправки
# ============================================================

TELEGRAM_MAX_LEN = 4000


def _split_html(text: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    """Разбивает HTML-текст на части по границам абзацев, сохраняя теги."""
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


async def _send_result(query, final_html: str):
    """Редактирует сообщение-заглушку результатом, затем отправляет меню."""
    try:
        await query.edit_message_text(
            final_html, parse_mode="HTML", reply_markup=None,
        )
    except Exception:
        try:
            await query.message.chat.send_message(
                final_html, parse_mode="HTML",
            )
        except Exception:
            logger.exception("Не удалось отправить результат")

    try:
        await query.message.chat.send_message(
            "Выберите действие:", reply_markup=main_menu_keyboard(),
        )
    except Exception:
        logger.exception("Не удалось отправить меню")


async def _send_multi_result(query, final_html: str):
    """Редактирует заглушку первой частью, остальные — новыми сообщениями, затем меню."""
    parts = _split_html(final_html)

    try:
        await query.edit_message_text(
            parts[0], parse_mode="HTML", reply_markup=None,
        )
    except Exception:
        try:
            await query.message.chat.send_message(
                parts[0], parse_mode="HTML",
            )
        except Exception:
            logger.exception("Не удалось отправить первую часть результата (weekly)")

    for part in parts[1:]:
        try:
            await query.message.chat.send_message(part, parse_mode="HTML")
        except Exception:
            logger.exception("Не удалось отправить продолжение результата (weekly)")

    try:
        await query.message.chat.send_message(
            "Выберите действие:", reply_markup=main_menu_keyboard(),
        )
    except Exception:
        logger.exception("Не удалось отправить меню")


# ============================================================
# 1. Прогноз на сегодня
# ============================================================

async def _send_today(query):
    """Формирует и отправляет прогноз на сегодня (с погодой, пробками, оборудованием)."""
    await query.edit_message_text("⏳ Формирую прогноз на сегодня...")

    try:
        now = datetime.now()
        today_weekday = now.weekday()
        current_hour = now.hour

        available = get_available_dates()
        if not available:
            await _send_result(query, "❌ Нет данных для прогноза. Загрузите Excel-файл.")
            return

        # Ищем дату с таким же днём недели
        target_date = None
        for d in available:
            if d.weekday() == today_weekday:
                target_date = d
                break

        if target_date is None:
            target_date = available[0]
            logger.info(
                "Нет данных на день недели %d, использую %s",
                today_weekday, target_date,
            )

        # Расчёт
        all_hours = calculate_day(target_date)
        if not all_hours:
            await _send_result(query, "❌ Нет данных для прогноза на сегодня.")
            return

        # Фильтруем оставшиеся часы (текущий час уже прошёл)
        remaining = [h for h in all_hours if h.hour > current_hour]

        if not remaining:
            await _send_result(
                query,
                f"📅 <b>ожидаемый прогноз на сегодня ({target_date.strftime('%d.%m')})</b>\n\n"
                f"✅ На оставшиеся часы данных нет — день завершён.",
            )
            return

        # Получаем оборудование из первого оставшегося часа
        first_hour = remaining[0]
        frames_working = first_hour.frames_working
        turnstiles_working = first_hour.turnstiles_working

        # Погода
        weather_summary = ""
        try:
            from src.parsers.weather import get_weather_forecast
            weather_data = await get_weather_forecast(date.today())
            if weather_data:
                weather_summary = weather_data.get("summary", "")
        except Exception as e:
            logger.warning("Погода недоступна: %s", e)

        # Пробки
        traffic_ball = TRAFFIC_BY_WEEKDAY.get(today_weekday, 0)

        # Форматируем через DeepSeek
        try:
            formatted = await format_today(
                remaining, target_date,
                weather_summary=weather_summary,
                traffic_ball=traffic_ball,
                frames_working=frames_working,
                turnstiles_working=turnstiles_working,
            )
            final_text = formatted
        except Exception as e:
            logger.warning("DeepSeek не отвечает, отправляю сгруппированный результат: %s", e)
            final_text = _build_today_fallback(
                remaining, target_date,
                weather_summary, traffic_ball,
                frames_working, turnstiles_working,
            )

        # Лог
        session = SessionLocal()
        try:
            log = ForecastLog(
                request_type="today",
                input_json={
                    "date": str(target_date),
                    "current_hour": current_hour,
                    "remaining_hours": len(remaining),
                    "traffic": traffic_ball,
                },
                response_text=final_text,
            )
            session.add(log)
            session.commit()
        finally:
            session.close()

        await _send_result(query, final_text)

    except Exception as e:
        logger.exception("Ошибка прогноза на сегодня")
        await _send_result(query, f"❌ Ошибка при формировании прогноза: {e}")


def _build_today_fallback(
    hours: list[HourResult],
    target_date: date,
    weather_summary: str = "",
    traffic_ball: int = 0,
    frames_working: int = 0,
    turnstiles_working: int = 0,
) -> str:
    """Fallback-форматирование прогноза на сегодня без DeepSeek (v2.2: разделение направлений)."""
    from src.llm.deepseek import _build_direction_html

    lines = [f"📅 <b>ожидаемый прогноз на сегодня ({target_date.strftime('%d.%m')})</b>"]

    if weather_summary:
        lines.append(f"🌤 {weather_summary}")
    if traffic_ball > 0:
        lines.append(f"🚗 Пробки: {traffic_ball} баллов")
    if frames_working > 0 or turnstiles_working > 0:
        lines.append(f"⚙️ Рамок: {frames_working} из {FRAMES_TOTAL}, турникетов: {turnstiles_working} из {TURNSTILES_TOTAL}")

    lines.append("")

    # Отправления (рамки)
    departing_lines = _build_direction_html(hours, "departing")
    if departing_lines:
        lines.extend(departing_lines)

    lines.append("")

    # Прибытия (турникеты)
    arriving_lines = _build_direction_html(hours, "arriving")
    if arriving_lines:
        lines.extend(arriving_lines)

    return "\n".join(lines)


# ============================================================
# 2. Прогноз на неделю (v2.1: 6 дней, норматив, min_equipment)
# ============================================================

async def _send_weekly(query):
    """Формирует и отправляет прогноз на 6 дней вперёд (без сегодня)."""
    await query.edit_message_text("⏳ Формирую прогноз на неделю...")

    try:
        today = date.today()
        available = get_available_dates()

        if not available:
            await _send_result(query, "❌ В базе нет данных для прогноза на неделю.")
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

            # Ищем любую дату в available с таким же днём недели
            source_date = None
            for d in available:
                if d.weekday() == future_weekday:
                    source_date = d
                    break

            if source_date is None:
                logger.warning("Нет данных для дня недели %d (%s)", future_weekday, future_date)
                continue

            # Рассчитываем с нормативным оборудованием
            hours = calculate_day_range_normative(source_date, source_date)
            # Но показываем под будущей датой
            results_by_date[future_date] = hours

        if not results_by_date:
            await _send_result(query, "❌ Нет данных для прогноза на неделю.")
            return

        # Форматируем через DeepSeek
        try:
            formatted = await format_weekly(results_by_date)
            final_text = f"📆 <b>ожидаемые прогнозы на неделю</b>\n\n{formatted}"
        except Exception as e:
            logger.warning("DeepSeek не отвечает, отправляю сырой результат: %s", e)
            final_text = _build_weekly_fallback(results_by_date)

        # Лог
        session = SessionLocal()
        try:
            log = ForecastLog(
                request_type="weekly",
                input_json={
                    "date_from": str(min(results_by_date.keys())),
                    "date_to": str(max(results_by_date.keys())),
                    "hours_count": sum(len(v) for v in results_by_date.values()),
                },
                response_text=final_text,
            )
            session.add(log)
            session.commit()
        finally:
            session.close()

        await _send_multi_result(query, final_text)

    except Exception as e:
        logger.exception("Ошибка прогноза на неделю")
        await _send_result(query, f"❌ Ошибка при формировании прогноза: {e}")


def _build_weekly_fallback(results_by_date: dict) -> str:
    """Fallback-форматирование недельного прогноза без DeepSeek (v2.2: разделение направлений)."""
    from src.llm.deepseek import _build_direction_html

    lines = ["📆 <b>ожидаемые прогнозы на неделю</b>\n"]

    for dt, hours in sorted(results_by_date.items()):
        weekday = RUS_WEEKDAYS.get(dt.weekday(), "?")
        lines.append(f"<b>{dt.strftime('%d.%m')} {weekday}</b>")

        # Отправления (рамки)
        departing_lines = _build_direction_html(hours, "departing")
        if departing_lines:
            lines.extend(departing_lines)

        # Прибытия (турникеты)
        arriving_lines = _build_direction_html(hours, "arriving")
        if arriving_lines:
            lines.extend(arriving_lines)

        lines.append("")

    return "\n".join(lines)


# ============================================================
# 3. Текущий статус (v2.1: переименования, ПТВ за прошлый час, кружки)
# ============================================================

async def _send_status(query):
    """Формирует и отправляет текущий статус вокзала."""
    await query.edit_message_text("⏳ Собираю текущий статус...")

    try:
        now = datetime.now()
        now_str = now.strftime("%d.%m.%Y %H:%M")
        current_hour = now.hour
        today = now.date()
        today_weekday = now.weekday()

        available = get_available_dates()
        if not available:
            await _send_result(query, "❌ Нет данных для статуса.")
            return

        # Ищем дату с таким же днём недели
        target_date = None
        for d in available:
            if d.weekday() == today_weekday:
                target_date = d
                break

        if target_date is None:
            target_date = available[0]

        # Берём данные за нужный час
        session = SessionLocal()
        try:
            from src.db.models import HourlyData
            row = (
                session.query(HourlyData)
                .filter(HourlyData.date == target_date, HourlyData.hour == current_hour)
                .first()
            )
        finally:
            session.close()

        if not row:
            all_hours = calculate_day(target_date)
            matching = [h for h in all_hours if h.hour >= current_hour]
            if matching:
                row_hour = matching[0]
            else:
                await _send_result(
                    query,
                    f"📊 <b>Текущий статус</b>\n\n"
                    f"Дата и время: {now_str}\n\n"
                    f"ℹ️ Нет данных на текущий час.",
                )
                return
        else:
            row_hour = calculate_hour(
                date_obj=target_date,
                hour=row.hour,
                passengers_arriving=row.passengers_arriving,
                trains_arriving=row.trains_arriving,
                turnstiles_working=row.turnstiles_working,
                passengers_departing=row.passengers_departing,
                trains_departing=row.trains_departing,
                frames_working=row.frames_working,
            )

        frames_working = row_hour.frames_working
        turnstiles_working = row_hour.turnstiles_working

        # ПТВ за прошедший час
        past_hour = (current_hour - 1) % 24
        ptv_hourly_rows = get_ptv_hourly(today_weekday, past_hour)

        ptv_sum = sum(r["passengers"] for r in ptv_hourly_rows) if ptv_hourly_rows else 0.0

        # Определяем статус ПТВ: сравниваем с другими днями недели
        ptv_status = _calc_ptv_hourly_status(today_weekday, past_hour, ptv_sum)

        # Форматируем через DeepSeek
        try:
            formatted = await format_status(
                current_hour=row_hour,
                now_str=now_str,
                frames_working=frames_working,
                turnstiles_working=turnstiles_working,
                ptv_hourly_data=ptv_hourly_rows,
                ptv_status=ptv_status,
                ptv_sum=ptv_sum,
            )
            final_text = formatted
        except Exception as e:
            logger.warning("DeepSeek не отвечает, отправляю сырой статус: %s", e)
            final_text = _build_status_fallback(
                row_hour, now_str, frames_working, turnstiles_working,
                ptv_hourly_rows, ptv_status, ptv_sum,
            )

        # Лог
        session = SessionLocal()
        try:
            log = ForecastLog(
                request_type="status",
                input_json={
                    "datetime": now_str,
                    "hour": current_hour,
                    "date": str(target_date),
                    "ptv_hour": past_hour,
                },
                response_text=final_text,
            )
            session.add(log)
            session.commit()
        finally:
            session.close()

        await _send_result(query, final_text)

    except Exception as e:
        logger.exception("Ошибка текущего статуса")
        await _send_result(query, f"❌ Ошибка при получении статуса: {e}")


def _calc_ptv_hourly_status(weekday: int, hour: int, current_sum: float) -> str:
    """
    Определяет статус ПТВ для конкретного часа:
    сравнивает сумму узла с другими днями недели в тот же час.
    Топ-2 дня → высокая загрузка, остальные → средняя загрузка.
    """
    # Собираем суммы по всем дням недели для этого часа
    hourly_sums = {}
    for wd in range(7):
        rows = get_ptv_hourly(wd, hour)
        s = sum(r["passengers"] for r in rows)
        hourly_sums[wd] = s

    if not hourly_sums or all(v == 0 for v in hourly_sums.values()):
        return "нет данных"

    sorted_days = sorted(hourly_sums.items(), key=lambda x: x[1], reverse=True)
    top_weekdays = {wd for wd, _ in sorted_days[:2]}

    if weekday in top_weekdays:
        return "высокая загрузка"
    else:
        return "средняя загрузка"


def _build_status_fallback(
    current_hour: HourResult,
    now_str: str,
    frames_working: int,
    turnstiles_working: int,
    ptv_hourly_data: list[dict],
    ptv_status: str,
    ptv_sum: float,
) -> str:
    """Fallback-форматирование статуса без DeepSeek."""
    from src.config import FRAMES_TOTAL, TURNSTILES_TOTAL

    frames_pct = round(frames_working / FRAMES_TOTAL * 100) if frames_working else 0
    turnstiles_pct = round(turnstiles_working / TURNSTILES_TOTAL * 100) if turnstiles_working else 0

    lines = [
        "📊 <b>Текущий статус</b>",
        f"Дата и время: {now_str}",
        "",
        "⚙️ <b>Оборудование</b>",
        f"Рамки на вход: {frames_working} из {FRAMES_TOTAL} ({frames_pct}%)",
        f"Турникеты на выход: {turnstiles_working} из {TURNSTILES_TOTAL} ({turnstiles_pct}%)",
        "",
        "👥 <b>Пассажиропоток дальнего следования</b>",
    ]

    # Отправление
    dep_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[current_hour.departing_zone]
    if current_hour.departing_total_minutes > 0:
        lines.append(
            f"Отправление: {current_hour.passengers_departing} пасс., "
            f"рамок {frames_working}, время прохода {current_hour.departing_total_minutes:.1f} мин {dep_icon}"
        )
    else:
        lines.append(f"Отправление: пассажиров нет 🟢")

    # Прибытие
    arr_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[current_hour.arriving_zone]
    if current_hour.arriving_total_minutes > 0:
        lines.append(
            f"Прибытие: {current_hour.passengers_arriving} пасс., "
            f"турникетов {turnstiles_working}, время прохода {current_hour.arriving_total_minutes:.1f} мин {arr_icon}"
        )
    else:
        lines.append(f"Прибытие: пассажиров нет 🟢")

    # ПТВ за прошедший час
    lines.append("")
    lines.append("🚇 <b>Пассажиропоток узла за прошедший час (отправления)</b>")
    for r in ptv_hourly_data:
        lines.append(f"{r['station_name']}: {r['passengers']:.0f} пасс.")
    lines.append(f"Всего узел: {ptv_sum:.0f} пасс.")
    lines.append(f"Статус: {ptv_status}")

    return "\n".join(lines)


# ============================================================
# 4. Моделирование (диалог) — v2.1: min-max время
# ============================================================

async def _start_model_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начинает диалог моделирования: запрашивает количество рамок."""
    query = update.callback_query
    await query.answer()

    context.user_data[MODEL_DATA_KEY] = {"step": "frames"}

    await query.edit_message_text(
        "🔧 <b>Моделирование пропускной способности</b>\n\n"
        "Введите количество работающих <b>рамок на вход</b> (всего 17):\n\n"
        "<i>Отправьте число в ответном сообщении</i>",
        parse_mode="HTML",
    )


async def model_handle_frames(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получили количество рамок — запрашиваем количество турникетов."""
    try:
        frames = int(update.message.text.strip())
        if frames < 0 or frames > 17:
            await update.message.reply_text(
                "⚠️ Введите число от 0 до 17 (всего рамок — 17):"
            )
            return
    except ValueError:
        await update.message.reply_text("⚠️ Введите, пожалуйста, число:")
        return

    context.user_data[MODEL_DATA_KEY] = {"step": "turnstiles", "frames": frames}

    await update.message.reply_text(
        f"✅ Рамок на вход: <b>{frames}</b>\n\n"
        f"Введите количество работающих <b>турникетов на выход</b> (всего 17):\n\n"
        f"<i>Отправьте число в ответном сообщении</i>",
        parse_mode="HTML",
    )


async def model_handle_turnstiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получили количество турникетов — выполняем расчёт."""
    try:
        turnstiles = int(update.message.text.strip())
        if turnstiles < 0 or turnstiles > 17:
            await update.message.reply_text(
                "⚠️ Введите число от 0 до 17 (всего турникетов — 17):"
            )
            return
    except ValueError:
        await update.message.reply_text("⚠️ Введите, пожалуйста, число:")
        return

    model_data = context.user_data.get(MODEL_DATA_KEY, {})
    frames = model_data.get("frames", 17)

    context.user_data.pop(MODEL_DATA_KEY, None)

    await update.message.reply_text("⏳ Выполняю моделирование...")

    try:
        now = datetime.now()
        today_weekday = now.weekday()
        current_hour = now.hour

        available = get_available_dates()
        if not available:
            await update.message.reply_text("❌ Нет данных для моделирования.")
            return

        target_date = None
        for d in available:
            if d.weekday() == today_weekday:
                target_date = d
                break

        if target_date is None:
            target_date = available[0]

        all_hours = calculate_day(
            target_date,
            custom_frames=frames,
            custom_turnstiles=turnstiles,
        )
        if not all_hours:
            await update.message.reply_text("❌ Нет данных для моделирования.")
            return

        remaining = [h for h in all_hours if h.hour > current_hour]
        if not remaining:
            await update.message.reply_text(
                f"🔧 <b>Моделирование пропускной способности</b>\n\n"
                f"Настроено: рамок {frames}, турникетов {turnstiles}\n\n"
                f"✅ На оставшиеся часы данных нет — день завершён.",
                parse_mode="HTML",
            )
            return

        try:
            formatted = await format_model(remaining, frames, turnstiles)
            final_text = formatted
        except Exception as e:
            logger.warning("DeepSeek не отвечает, отправляю сырой результат: %s", e)
            final_text = _build_model_fallback(remaining, frames, turnstiles)

        # Лог
        session = SessionLocal()
        try:
            log = ForecastLog(
                request_type="model",
                input_json={
                    "date": str(target_date),
                    "frames": frames,
                    "turnstiles": turnstiles,
                    "current_hour": current_hour,
                    "remaining_hours": len(remaining),
                },
                response_text=final_text,
            )
            session.add(log)
            session.commit()
        finally:
            session.close()

        await update.message.reply_text(final_text, parse_mode="HTML")

        await update.message.reply_text(
            "Выберите действие:", reply_markup=main_menu_keyboard(),
        )

    except Exception as e:
        logger.exception("Ошибка моделирования")
        await update.message.reply_text(f"❌ Ошибка при моделировании: {e}")


def _build_model_fallback(hours: list[HourResult], frames: int, turnstiles: int) -> str:
    """Fallback-форматирование моделирования без DeepSeek (v2.2: разделение направлений)."""
    from src.llm.deepseek import _build_direction_html

    lines = [
        "🔧 <b>Моделирование пропускной способности</b>",
        f"Настроено: рамок {frames}, турникетов {turnstiles}",
        "",
    ]

    # Отправления (рамки)
    departing_lines = _build_direction_html(hours, "departing")
    if departing_lines:
        lines.extend(departing_lines)

    lines.append("")

    # Прибытия (турникеты)
    arriving_lines = _build_direction_html(hours, "arriving")
    if arriving_lines:
        lines.extend(arriving_lines)

    return "\n".join(lines)


# ============================================================
# Команда /start
# ============================================================

PREAMBLE = (
    "👋 <b>Привет! Я аналитическая система управления Ленинградским вокзалом.</b>\n"
    "\n"
    "Вот что я умею:\n"
    "📊 <b>Текущий статус</b> — загрузка вокзала прямо сейчас: оборудование, пассажиропоток, ПТВ за прошедший час\n"
    "📅 <b>Прогноз на сегодня</b> — ожидаемая загрузка на оставшиеся часы с учётом погоды и пробок\n"
    "📆 <b>Прогноз на неделю</b> — загрузка на 6 дней вперёд с нормативным оборудованием\n"
    "🔧 <b>Моделирование</b> — расчёт времени прохода при заданном количестве рамок и турникетов\n"
    "\n"
    "⚠️ Расчёты выполнены на тестовых данных."
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик /start."""
    await _log_user(update, "старт")
    # Проверка пароля (персистентная: telegram_id в БД + fallback на user_data)
    user = update.effective_user
    if BOT_PASSWORD and not (_is_authorized(user.id) or context.user_data.get(AUTH_STATE_KEY)):
        await update.message.reply_text(
            "🔐 <b>Доступ ограничен.</b>\n\nВведите пароль для входа:",
            parse_mode="HTML",
        )
        return

    await update.message.reply_text(
        PREAMBLE,
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


# ============================================================
# Текстовые сообщения (не диалог)
# ============================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений — маршрутизация диалогов."""
    # Проверка пароля (персистентная: telegram_id в БД + fallback на user_data)
    user = update.effective_user
    if BOT_PASSWORD and not (_is_authorized(user.id) or context.user_data.get(AUTH_STATE_KEY)):
        if update.message.text.strip() == BOT_PASSWORD:
            context.user_data[AUTH_STATE_KEY] = True
            _set_authorized(
                user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
            )
            await update.message.reply_text(
                PREAMBLE,
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
        else:
            await update.message.reply_text(
                "❌ <b>Неверный пароль.</b>\n\nПопробуйте ещё раз:",
                parse_mode="HTML",
            )
        return

    # Обычная маршрутизация диалогов
    model_data = context.user_data.get(MODEL_DATA_KEY)

    if model_data and model_data.get("step") == "frames":
        await _log_user(update, "моделирование: ввод рамок")
        await model_handle_frames(update, context)
    elif model_data and model_data.get("step") == "turnstiles":
        await _log_user(update, "моделирование: ввод турникетов")
        await model_handle_turnstiles(update, context)
    else:
        text = update.message.text.strip().lower() if update.message.text else ""
        if text in ("привет", "прив", "здравствуй", "здравствуйте", "hello", "hi", "start"):
            await _log_user(update, "приветствие")
            await update.message.reply_text(
                PREAMBLE,
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
        else:
            await _log_user(update, f"сообщение: {text[:50]}")
            await update.message.reply_text(
                "Используйте кнопки меню для взаимодействия:",
                reply_markup=main_menu_keyboard(),
            )
