"""
Аналитический калькулятор — v2.0.

Два раздельных потока:
- Прибытие: пассажиры через турникеты (12 сек/чел)
- Отправление: пассажиры через рамки (40 сек/чел)

Только нормативные графики. Причины: только "большое количество пассажиров".

v2.1:
- calculate_min_equipment() — минимальное оборудование для зелёной зоны
- get_ptv_hourly() — почасовые данные ПТВ
- calculate_day_range_normative() — расчёт с нормативным оборудованием (17/17)
"""
import logging
import math
from datetime import date
from typing import Optional

from src.db.session import SessionLocal
from src.db.models import HourlyData, PTVData, PTVHourlyData
from src.config import (
    TURNSTILE_PASS_SECONDS,
    FRAME_PASS_SECONDS,
    FRAMES_TOTAL,
    TURNSTILES_TOTAL,
    ZONE_GREEN_MAX,
    ZONE_YELLOW_MAX,
    PASSENGERS_PER_TRAIN_THRESHOLD,
)

logger = logging.getLogger(__name__)

# ===== Типы результатов =====

class HourResult:
    """
    Результат расчёта для одного часа — v2.0.
    Два независимых потока: прибытие (турникеты) и отправление (рамки).
    """
    __slots__ = (
        "date", "hour",
        # Прибытие (турникеты)
        "passengers_arriving",
        "trains_arriving",
        "turnstiles_working",
        "arriving_pass_per_train",
        "arriving_total_seconds",
        "arriving_total_minutes",
        "arriving_zone",           # "green" | "yellow" | "red"
        "arriving_reasons",        # список строк-причин
        # Отправление (рамки)
        "passengers_departing",
        "trains_departing",
        "frames_working",
        "departing_pass_per_train",
        "departing_total_seconds",
        "departing_total_minutes",
        "departing_zone",          # "green" | "yellow" | "red"
        "departing_reasons",       # список строк-причин
    )

    def __init__(self, date_obj, hour,
                 passengers_arriving=0, trains_arriving=0, turnstiles_working=0,
                 passengers_departing=0, trains_departing=0, frames_working=0):
        self.date = date_obj
        self.hour = hour

        # Прибытие
        self.passengers_arriving = passengers_arriving
        self.trains_arriving = trains_arriving
        self.turnstiles_working = turnstiles_working
        self.arriving_pass_per_train = 0.0
        self.arriving_total_seconds = 0.0
        self.arriving_total_minutes = 0.0
        self.arriving_zone = "green"
        self.arriving_reasons = []

        # Отправление
        self.passengers_departing = passengers_departing
        self.trains_departing = trains_departing
        self.frames_working = frames_working
        self.departing_pass_per_train = 0.0
        self.departing_total_seconds = 0.0
        self.departing_total_minutes = 0.0
        self.departing_zone = "green"
        self.departing_reasons = []

    @property
    def overall_zone(self) -> str:
        """Итоговая зона часа — худшая из двух потоков."""
        zones = {"green": 0, "yellow": 1, "red": 2}
        a = zones.get(self.arriving_zone, 0)
        d = zones.get(self.departing_zone, 0)
        if d > a:
            return self.departing_zone
        return self.arriving_zone

    @property
    def has_problems(self) -> bool:
        """Есть ли проблемы (жёлтая или красная зона) хоть в одном потоке."""
        return self.arriving_zone in ("yellow", "red") or self.departing_zone in ("yellow", "red")


# ===== Вспомогательные функции зон =====

def _zone_for_minutes(total_minutes: float) -> str:
    """Определяет зону по времени прохода в минутах."""
    if total_minutes >= ZONE_YELLOW_MAX:
        return "red"
    elif total_minutes >= ZONE_GREEN_MAX:
        return "yellow"
    else:
        return "green"


def _pass_per_train_reason(pass_per_train: float) -> list[str]:
    """Формирует список причин (только 'большое количество пассажиров')."""
    reasons = []
    if pass_per_train > PASSENGERS_PER_TRAIN_THRESHOLD:
        reasons.append("большое количество пассажиров")
    return reasons


# ===== Основной расчёт =====

def calculate_hour(
    date_obj: date,
    hour: int,
    passengers_arriving: int,
    trains_arriving: int,
    turnstiles_working: int,
    passengers_departing: int,
    trains_departing: int,
    frames_working: int,
    custom_turnstiles: Optional[int] = None,
    custom_frames: Optional[int] = None,
) -> HourResult:
    """
    Рассчитывает зону нагрузки для одного часа — v2.0.

    Два раздельных потока:
    - ПРИБЫТИЕ: passengers_arriving / trains_arriving → пасс/поезд
                → * TURNSTILE_PASS_SECONDS (12с) → / turnstiles_working
                → минуты → зона
    - ОТПРАВЛЕНИЕ: passengers_departing / trains_departing → пасс/поезд
                   → * FRAME_PASS_SECONDS (40с) → / frames_working
                   → минуты → зона

    custom_turnstiles / custom_frames — если переданы, используются вместо данных из БД
    (для инструмента моделирования).
    """
    result = HourResult(
        date_obj=date_obj,
        hour=hour,
        passengers_arriving=passengers_arriving,
        trains_arriving=trains_arriving,
        turnstiles_working=turnstiles_working,
        passengers_departing=passengers_departing,
        trains_departing=trains_departing,
        frames_working=frames_working,
    )

    effective_turnstiles = custom_turnstiles if custom_turnstiles is not None else turnstiles_working
    effective_frames = custom_frames if custom_frames is not None else frames_working

    # ===== РАСЧЁТ ПРИБЫТИЯ (турникеты) =====
    if trains_arriving > 0 and passengers_arriving > 0 and effective_turnstiles > 0:
        pass_per_train = passengers_arriving / trains_arriving
        result.arriving_pass_per_train = pass_per_train

        time_one_turnstile = pass_per_train * TURNSTILE_PASS_SECONDS
        total_seconds = time_one_turnstile / effective_turnstiles
        total_minutes = total_seconds / 60.0

        result.arriving_total_seconds = total_seconds
        result.arriving_total_minutes = total_minutes
        result.arriving_zone = _zone_for_minutes(total_minutes)
        result.arriving_reasons = _pass_per_train_reason(pass_per_train)

        zone_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
        logger.info(
            "🟢 ПРИБЫТИЕ [%s %02d:00] пасс=%d поезда=%d турникетов=%d "
            "пасс/поезд=%.1f время=%.1fмин → %s%s",
            date_obj, hour, passengers_arriving, trains_arriving,
            effective_turnstiles, pass_per_train, total_minutes,
            zone_icon.get(result.arriving_zone, "?"),
            f" ({'; '.join(result.arriving_reasons)})" if result.arriving_reasons else "",
        )
    else:
        logger.debug(
            "  ПРИБЫТИЕ [%s %02d:00] нет данных (поезда=%d, пасс=%d, турник=%d) → 🟢",
            date_obj, hour, trains_arriving, passengers_arriving, effective_turnstiles,
        )

    # ===== РАСЧЁТ ОТПРАВЛЕНИЯ (рамки) =====
    if trains_departing > 0 and passengers_departing > 0 and effective_frames > 0:
        pass_per_train = passengers_departing / trains_departing
        result.departing_pass_per_train = pass_per_train

        time_one_frame = pass_per_train * FRAME_PASS_SECONDS
        total_seconds = time_one_frame / effective_frames
        total_minutes = total_seconds / 60.0

        result.departing_total_seconds = total_seconds
        result.departing_total_minutes = total_minutes
        result.departing_zone = _zone_for_minutes(total_minutes)
        result.departing_reasons = _pass_per_train_reason(pass_per_train)

        zone_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
        logger.info(
            "🟢 ОТПРАВЛЕНИЕ [%s %02d:00] пасс=%d поезда=%d рамок=%d "
            "пасс/поезд=%.1f время=%.1fмин → %s%s",
            date_obj, hour, passengers_departing, trains_departing,
            effective_frames, pass_per_train, total_minutes,
            zone_icon.get(result.departing_zone, "?"),
            f" ({'; '.join(result.departing_reasons)})" if result.departing_reasons else "",
        )
    else:
        logger.debug(
            "  ОТПРАВЛЕНИЕ [%s %02d:00] нет данных (поезда=%d, пасс=%d, рамок=%d) → 🟢",
            date_obj, hour, trains_departing, passengers_departing, effective_frames,
        )

    return result


# ===== Работа с БД =====

def _get_data_for_date(date_obj: date):
    """Достаёт почасовые данные из БД для указанной даты."""
    session = SessionLocal()
    try:
        rows = (
            session.query(HourlyData)
            .filter(HourlyData.date == date_obj)
            .order_by(HourlyData.hour)
            .all()
        )
        return [
            {
                "hour": r.hour,
                "passengers_arriving": r.passengers_arriving,
                "passengers_departing": r.passengers_departing,
                "trains_arriving": r.trains_arriving,
                "trains_departing": r.trains_departing,
                "frames_working": r.frames_working,
                "turnstiles_working": r.turnstiles_working,
            }
            for r in rows
        ]
    finally:
        session.close()


def _get_data_date_range(start: date, end: date):
    """Достаёт почасовые данные для диапазона дат."""
    session = SessionLocal()
    try:
        rows = (
            session.query(HourlyData)
            .filter(HourlyData.date >= start, HourlyData.date <= end)
            .order_by(HourlyData.date, HourlyData.hour)
            .all()
        )
        return [
            {
                "date": r.date,
                "hour": r.hour,
                "passengers_arriving": r.passengers_arriving,
                "passengers_departing": r.passengers_departing,
                "trains_arriving": r.trains_arriving,
                "trains_departing": r.trains_departing,
                "frames_working": r.frames_working,
                "turnstiles_working": r.turnstiles_working,
            }
            for r in rows
        ]
    finally:
        session.close()


def get_available_dates() -> list[date]:
    """Возвращает список уникальных дат, по которым есть данные."""
    session = SessionLocal()
    try:
        rows = session.query(HourlyData.date).distinct().order_by(HourlyData.date).all()
        return [r[0] for r in rows]
    finally:
        session.close()


def get_ptv_data(for_date: Optional[date] = None) -> list[dict]:
    """
    Возвращает данные ПТВ.
    Если for_date указана — только за эту дату.
    Иначе — все доступные записи.
    """
    session = SessionLocal()
    try:
        q = session.query(PTVData)
        if for_date:
            q = q.filter(PTVData.date == for_date)
        rows = q.order_by(PTVData.date, PTVData.station_name).all()
        return [
            {
                "date": r.date,
                "station_name": r.station_name,
                "passengers_per_day": r.passengers_per_day,
            }
            for r in rows
        ]
    finally:
        session.close()


def get_ptv_weekly() -> list[dict]:
    """Возвращает все данные ПТВ для анализа (повышенный/средний)."""
    return get_ptv_data()


# ===== Публичные функции =====

def calculate_day(
    date_obj: date,
    custom_turnstiles: Optional[int] = None,
    custom_frames: Optional[int] = None,
) -> list[HourResult]:
    """
    Рассчитывает все часы для указанной даты.
    custom_turnstiles/custom_frames — для моделирования (все часы с этими значениями).
    Возвращает список HourResult.
    """
    rows = _get_data_for_date(date_obj)
    results = []
    for r in rows:
        hr = calculate_hour(
            date_obj=date_obj,
            hour=r["hour"],
            passengers_arriving=r["passengers_arriving"],
            trains_arriving=r["trains_arriving"],
            turnstiles_working=r["turnstiles_working"],
            passengers_departing=r["passengers_departing"],
            trains_departing=r["trains_departing"],
            frames_working=r["frames_working"],
            custom_turnstiles=custom_turnstiles,
            custom_frames=custom_frames,
        )
        results.append(hr)
    return results


def calculate_min_equipment(hours: list[HourResult]) -> dict:
    """
    Вычисляет минимально необходимое количество оборудования
    для прохода за ≤ 3 минуты (зелёная зона).

    Формула для каждого часа:
      min_equipment = ⌈ pass_per_train × PASS_SECONDS / (3 × 60) ⌉

    Для списка часов берётся максимум требований.

    Возвращает {"min_frames": int, "min_turnstiles": int}.
    """
    max_frames = 0
    max_turnstiles = 0

    for h in hours:
        # Прибытие (турникеты)
        if h.trains_arriving > 0 and h.passengers_arriving > 0:
            pass_per_train = h.passengers_arriving / h.trains_arriving
            needed = math.ceil(pass_per_train * TURNSTILE_PASS_SECONDS / (ZONE_GREEN_MAX * 60))
            if needed > max_turnstiles:
                max_turnstiles = needed

        # Отправление (рамки)
        if h.trains_departing > 0 and h.passengers_departing > 0:
            pass_per_train = h.passengers_departing / h.trains_departing
            needed = math.ceil(pass_per_train * FRAME_PASS_SECONDS / (ZONE_GREEN_MAX * 60))
            if needed > max_frames:
                max_frames = needed

    # Минимум — 4 рамки и 5 турникетов (не показываем 0 или 1)
    return {
        "min_frames": max(min(max_frames, FRAMES_TOTAL), 4),
        "min_turnstiles": max(min(max_turnstiles, TURNSTILES_TOTAL), 5),
    }


def get_ptv_hourly(weekday: int, hour: int) -> list[dict]:
    """
    Возвращает почасовые данные ПТВ для конкретного дня недели и часа.

    Параметры:
    - weekday: 0–6 (0=пн, ISO) — будет преобразовано в 1–7 + 0→7
    - hour: 0–23

    Возвращает список dict с ключами station_name, passengers.
    """
    # Преобразование: Python weekday (0=пн) → БД weekday (1=пн)
    db_weekday = weekday + 1

    session = SessionLocal()
    try:
        rows = (
            session.query(PTVHourlyData)
            .filter(
                PTVHourlyData.weekday == db_weekday,
                PTVHourlyData.hour == hour,
            )
            .order_by(PTVHourlyData.station_name)
            .all()
        )
        return [
            {
                "station_name": r.station_name,
                "passengers": r.passengers,
            }
            for r in rows
        ]
    finally:
        session.close()


def calculate_day_range_normative(
    start: date,
    end: date,
) -> list[HourResult]:
    """
    Рассчитывает все часы для диапазона дат с нормативным оборудованием
    (все 17 рамок, 17 турникетов). Используется для недельного прогноза.
    """
    return calculate_day_range(
        start, end,
        custom_frames=FRAMES_TOTAL,
        custom_turnstiles=TURNSTILES_TOTAL,
    )


def calculate_day_range(
    start: date,
    end: date,
    custom_turnstiles: Optional[int] = None,
    custom_frames: Optional[int] = None,
) -> list[HourResult]:
    """Рассчитывает все часы для диапазона дат."""
    rows = _get_data_date_range(start, end)
    results = []
    for r in rows:
        hr = calculate_hour(
            date_obj=r["date"],
            hour=r["hour"],
            passengers_arriving=r["passengers_arriving"],
            trains_arriving=r["trains_arriving"],
            turnstiles_working=r["turnstiles_working"],
            passengers_departing=r["passengers_departing"],
            trains_departing=r["trains_departing"],
            frames_working=r["frames_working"],
            custom_turnstiles=custom_turnstiles,
            custom_frames=custom_frames,
        )
        results.append(hr)
    return results
