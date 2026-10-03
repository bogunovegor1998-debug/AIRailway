"""
Модели БД для аналитической системы Ленинградского вокзала — v2.0.

Изменения относительно v1.0:
- Часы 0–23 (вместо 1–24)
- Только нормативные графики (trains_arriving/trains_departing)
- Добавлены рамки (frames_working) и турникеты (turnstiles_working)
- Раздельные потоки: прибытие (турникеты) и отправление (рамки)
- Новая таблица ptv_data для пассажиропотока станций узла
"""
from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, SmallInteger, String, Date, Float, Text, DateTime, JSON
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class HourlyData(Base):
    """
    Почасовые данные вокзала: пассажиры, поезда, рамки, турникеты.
    UPSERT по (date, hour) при загрузке из Excel.

    v2.0:
    - часы 0–23
    - только нормативные графики (нет trains_actual)
    - frames_working: работающие рамки для отправляющихся пассажиров
    - turnstiles_working: работающие турникеты для прибывающих пассажиров
    """
    __tablename__ = "hourly_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    hour = Column(SmallInteger, nullable=False)                # 0–23

    # Пассажиры
    passengers_arriving = Column(Integer, nullable=False)       # прибывшие пассажиры (через турникеты)
    passengers_departing = Column(Integer, nullable=False)      # отправленные пассажиры (через рамки)

    # Поезда (только норматив)
    trains_arriving = Column(Integer, nullable=False)           # прибывающие поезда (норматив)
    trains_departing = Column(Integer, nullable=False)          # отправляющиеся поезда (норматив)

    # Оборудование
    frames_working = Column(SmallInteger, nullable=False)       # работающие рамки (отправление)
    turnstiles_working = Column(SmallInteger, nullable=False)   # работающие турникеты (прибытие)

    source_file = Column(String(255))                            # имя исходного xlsx
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PTVData(Base):
    """
    Данные по пассажиропотоку станций транспортного узла (ПТВ).
    Суточные значения, тыс. пасс./сутки.
    """
    __tablename__ = "ptv_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    station_name = Column(String(100), nullable=False)          # название станции
    passengers_per_day = Column(Float, nullable=False)          # тыс. пасс./сутки


class PTVHourlyData(Base):
    """
    Почасовые данные пассажиропотока станций узла (ПТВ) — v2.1.
    Значения по часам и дням недели (из Данные по ПТВ_2.xlsx).
    """
    __tablename__ = "ptv_hourly_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    weekday = Column(SmallInteger, nullable=False)              # 1–7 (пн–вс)
    hour = Column(SmallInteger, nullable=False)                 # 0–23
    station_name = Column(String(100), nullable=False)          # название станции
    passengers = Column(Float, nullable=False)                  # отправлено пассажиров за час


class ForecastLog(Base):
    """
    Лог запросов к DeepSeek.
    """
    __tablename__ = "forecast_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_type = Column(String(20), nullable=False)   # today | weekly | status | model
    input_json = Column(JSON)                           # что передали в LLM
    response_text = Column(Text)                        # ответ DeepSeek
    generated_at = Column(DateTime(timezone=True), server_default=func.now())


class UserActivity(Base):
    """
    Лог активности пользователей: кто и когда нажимал кнопки.
    """
    __tablename__ = "user_activity"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, nullable=False, index=True)
    username = Column(String(100), nullable=True)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    action = Column(String(50), nullable=False)         # start, today, weekly, status, model, welcome
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AuthorizedUser(Base):
    """
    Пользователи, прошедшие авторизацию по паролю.
    После первого успешного ввода пароля telegram_id сохраняется сюда,
    чтобы при перезапуске бота не запрашивать пароль повторно.
    """
    __tablename__ = "authorized_users"

    telegram_id = Column(BigInteger, primary_key=True)
    username = Column(String(100), nullable=True)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
