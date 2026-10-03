@echo off
REM ======================================================
REM Скрипт настройки виртуального окружения AIRailway
REM Запустите: setup.bat
REM ======================================================

echo === AIRailway: Настройка окружения ===
echo.

REM Ищем Python: сначала в PATH, затем в типовых местах установки
set PYTHON_EXE=

for /f "delims=" %%p in ('where python 2^>nul') do (
    if not defined PYTHON_EXE set PYTHON_EXE=%%p
)
if defined PYTHON_EXE goto :found

for %%p in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
) do (
    if exist %%p (
        set PYTHON_EXE=%%p
        goto :found
    )
)

echo [ОШИБКА] Python не найден!
echo Скачайте и установите Python 3.11+ с https://www.python.org/downloads/
echo Убедитесь что при установке отмечена галочка "Add Python to PATH"
pause
exit /b 1

:found
echo [OK] Python найден: %PYTHON_EXE%
echo.

REM Создаём виртуальное окружение
if exist .venv (
    echo [INFO] Виртуальное окружение уже существует
) else (
    echo [INFO] Создаём виртуальное окружение .venv ...
    %PYTHON_EXE% -m venv .venv
    echo [OK] Виртуальное окружение создано
)
echo.

REM Активируем и устанавливаем зависимости
echo [INFO] Устанавливаем зависимости из requirements.txt ...
.venv\Scripts\pip install --upgrade pip
.venv\Scripts\pip install -r requirements.txt
echo.

REM Проверяем наличие .env
if exist .env (
    echo [INFO] .env уже существует
) else (
    echo [ВНИМАНИЕ] Файл .env не найден!
    echo Создайте .env в корне проекта со следующими переменными:
    echo   TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, DATABASE_URL, DEEPSEEK_API_KEY
    echo   TURNSTILE_PASS_SECONDS, FRAME_PASS_SECONDS
    echo   FRAMES_TOTAL, TURNSTILES_TOTAL, ZONE_GREEN_MAX, ZONE_YELLOW_MAX
    echo   DAILY_DIGEST_HOUR, DAILY_DIGEST_MINUTE, STATION_NAME и другие
    echo   Полный список — в .env.example (скопируйте его в .env).
)

echo.
echo === Настройка завершена ===
echo.
echo Следующие шаги:
echo 1. Откройте .env и заполните TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, DATABASE_URL, DEEPSEEK_API_KEY
echo 2. Загрузите данные: .venv\Scripts\python -m scripts.load_excel
echo 3. Запустите бота: .venv\Scripts\python -m src.bot.main
echo.
pause
