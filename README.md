# Telegram-бот для учета заказов платков

Бот на `python-telegram-bot` с хранением заказов в `orders.json` и управлением через Telegram-кнопки.

## Что есть в проекте
- [main.py](./main.py) — основной код бота
- [requirements.txt](./requirements.txt) — Python зависимости
- [.env.example](./.env.example) — пример переменных окружения для локального запуска
- [orders.json](./orders.json) — локальное хранилище заказов
- [runtime.txt](./runtime.txt) — фиксация версии Python `3.11.11`
- [.python-version](./.python-version) — дополнительный pin Python `3.11.11` для детектора сборки
- [nixpacks.toml](./nixpacks.toml) — конфиг сборки/старта для Railway (Nixpacks)
- [railway.json](./railway.json) — Railway config-as-code (builder, start command, restart policy)
- [.gitignore](./.gitignore) — исключает секреты и локальные артефакты

## Локальный запуск (Windows PowerShell)
1. Перейдите в папку проекта:
   ```powershell
   cd C:\Users\ice20\Desktop\bot
   ```
2. Создайте и активируйте виртуальное окружение:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
3. Установите зависимости:
   ```powershell
   pip install -r requirements.txt
   ```
4. Создайте `.env`:
   ```powershell
   Copy-Item .env.example .env
   ```
5. Заполните `.env`:
   ```env
   BOT_TOKEN=ваш_токен_бота
   ADMIN_ID=ваш_числовой_telegram_id
   ```
6. Запустите бота:
   ```powershell
   python .\main.py
   ```

## Деплой на Railway (через GitHub)
1. Запушьте проект в GitHub.
2. В Railway создайте сервис из GitHub-репозитория (`Deploy from GitHub Repo`).
3. В `Variables` добавьте:
   - `BOT_TOKEN` — токен Telegram-бота
   - `ADMIN_ID` — числовой Telegram ID администратора
4. Убедитесь, что Railway видит `railway.json` и `nixpacks.toml` (start command уже задан: `python -u main.py`).
5. Дождитесь первого деплоя.

После привязки репозитория Railway автоматически запускает новый деплой при `git push` в подключенную ветку.

## Важно про переменные окружения
- На Railway `.env` не нужен.
- Бот читает `BOT_TOKEN` и `ADMIN_ID` из переменных окружения сервиса.
- Локальный `.env` используется только при наличии файла в проекте.

## Перезапуск сервиса в Railway
- Через UI: откройте нужный deployment и нажмите `Restart`.
- Через CLI (опционально):
  ```bash
  railway restart
  ```

## Логи в Railway
- В карточке deployment (Build/Deploy logs).
- В `Observability` -> Log Explorer.
- Через CLI:
  ```bash
  railway logs
  ```

## Стабильность long polling
В `main.py` запуск сделан через `run_polling(...)` с безопасными параметрами для прод-среды:
- `bootstrap_retries=-1` (повторные попытки подключения к Telegram API)
- `close_loop=False`
- логирование старта и ошибок обработчиков

## Примечания
- `orders.json` на Railway хранится в файловой системе контейнера и не является постоянным хранилищем между пересборками/перезапусками.
- Для постоянного хранения на проде лучше использовать БД или volume.



