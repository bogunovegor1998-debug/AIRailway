# План доработок v2 — новая модель данных и формат вывода

## Обзор

1. Добавляется колонка `trains_arriving_actual` (приезжающие поезда факт)
2. `trains_arriving` переименовывается в `trains_arriving_norm` (норматив)
3. В расчётах `пассажиров/поезд` используется **факт**, а не норматив
4. Если факт < норматив → причина "окно в графике или сбой в графике движения поездов"
5. Пиковые нагрузки выводятся для всех будущих дней (не только 3)
6. Формат пиковых нагрузок — как дайджест: дата жирным, под ней зоны

## Файлы к изменению

| Файл | Что |
|------|-----|
| [src/db/models.py](src/db/models.py) | `trains_arriving` → `trains_arriving_norm` + новое `trains_arriving_actual` |
| [scripts/load_excel.py](scripts/load_excel.py) | `ROW_NAMES`: норматив и факт; сортировка файлов по mtime |
| [src/analytics/calculator.py](src/analytics/calculator.py) | `HourResult` + поля; `calculate_hour` логика факт/норматив; `_get_data_*` |
| [src/bot/handlers.py](src/bot/handlers.py) | Убрать `[:3]` в пиковых нагрузках |
| [src/llm/deepseek.py](src/llm/deepseek.py) | `SYSTEM_PEAKS` формат; `_build_digest_input` + `format_peaks` |

## Порядок выполнения

1. Модель БД
2. Парсер Excel
3. Upsert
4. Загрузка нового Excel
5. `calculate_hour` + `HourResult`
6. `_get_data_for_date` / `_get_data_date_range`
7. Пиковые нагрузки — все дни
8. Формат `SYSTEM_PEAKS`
9. `_build_digest_input` + `format_peaks`
10. Проверка дней недели
