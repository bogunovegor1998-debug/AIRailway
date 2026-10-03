"""
Точка входа: запуск Telegram-бота и планировщика.
"""
import sys
import os
import time
import socket
import threading

# Настраиваем логирование ДО всего остального
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.logger import setup_logging
logger = setup_logging()

import logging
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from src.config import TELEGRAM_TOKEN
from src.db.session import init_db
from src.bot.handlers import cmd_start, handle_message, handle_callback
from src.bot.scheduler import setup_scheduler

main_logger = logging.getLogger(__name__)


def _watchdog_worker():
    """
    Фоновый поток: периодически проверяет доступность api.telegram.org.
    При 3 последовательных отказах DNS — жёстко убивает процесс через os._exit(1),
    чтобы внешний bat-файл (run_bot.bat) перезапустил бота.
    """
    CHECK_INTERVAL = 30  # секунд между проверками
    MAX_FAILURES = 3
    failures = 0

    # Даём боту время на первый запуск
    time.sleep(10)

    while True:
        try:
            socket.getaddrinfo("api.telegram.org", 443, proto=socket.IPPROTO_TCP)
            failures = 0  # сеть в порядке — сбрасываем счётчик
        except socket.gaierror:
            failures += 1
            main_logger.warning(
                "[watchdog] DNS-резолвинг api.telegram.org не удался (отказ %d/%d)",
                failures, MAX_FAILURES,
            )
            if failures >= MAX_FAILURES:
                main_logger.critical(
                    "[watchdog] %d последовательных отказа DNS. Принудительное завершение процесса.",
                    MAX_FAILURES,
                )
                # os._exit убивает процесс немедленно, без finally/atexit —
                # bat-файл перезапустит бота с чистым состоянием
                os._exit(1)

        time.sleep(CHECK_INTERVAL)


def main():
    main_logger.info("=== Запуск аналитической системы Ленинградского вокзала ===")

    # Инициализация БД
    main_logger.info("Инициализация базы данных...")
    try:
        init_db()
        main_logger.info("База данных готова.")
    except Exception as e:
        main_logger.critical("Не удалось инициализировать БД: %s", e)
        sys.exit(1)

    # Создаём приложение
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    main_logger.info("Обработчики бота зарегистрированы.")

    # Планировщик
    scheduler = setup_scheduler(app)
    scheduler.start()
    main_logger.info("Планировщик запущен.")

    # Запускаем watchdog-поток (daemon — не мешает выходу по Ctrl+C)
    watchdog = threading.Thread(target=_watchdog_worker, daemon=True, name="watchdog")
    watchdog.start()
    main_logger.info("Watchdog-поток запущен (проверка api.telegram.org каждые 30 сек, 3 отказа → перезапуск).")

    main_logger.info("Бот запущен. Ожидаем сообщения...")
    app.run_polling(drop_pending_updates=True)

    if scheduler.running:
        scheduler.shutdown(wait=False)
    main_logger.info("=== Система остановлена ===")


if __name__ == "__main__":
    main()
