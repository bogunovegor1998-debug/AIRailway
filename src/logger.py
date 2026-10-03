"""
Логирование приложения.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "app.log")


def setup_logging() -> logging.Logger:
    """Настраивает корневой логгер с ротацией файлов и выводом в консоль."""
    os.makedirs(LOG_DIR, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Формат
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Файловый handler (ротация 5 файлов по 5 МБ)
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Консольный handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Подавляем HTTP-логи от httpx и telegram (мусор в консоли)
    for noisy_logger in (
        "httpx", "httpcore", "telegram.request", "telegram.ext._updater",
        # DEBUG-шум get_updates (Entering/Exiting/No new updates)
        "telegram.ext._application",
        "telegram.vendor.ptb_urllib3.urllib3.connectionpool",
    ):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    return logging.getLogger(__name__)


logger = setup_logging()
