# Развёртывание на VPS

Только про TeaSender. Ничего постороннего на сервере трогать не нужно.

## Предварительно

- VPS с Docker и Docker Compose.
- `API_ID`/`API_HASH` — с https://my.telegram.org.
- Токен бота — от @BotFather. **Не пересылайте его в переписках; если засветили — сделайте `/revoke` и выпустите новый.**
- Приватный канал-черновик, куда вы будете писать объявления. Добавьте туда свой аккаунт (и, если нужно, бота — только для чтения).

## Шаги

```bash
git clone <repo> teasender && cd teasender
cp .env.example .env
nano .env                      # заполнить API_ID, API_HASH, BOT_TOKEN, ADMIN_USER_IDS, DRAFTS_CHANNEL

docker compose up -d db        # PostgreSQL

# Логин в аккаунт делается интерактивно один раз — на своей машине или на
# сервере в интерактивном контейнере (код подтверждения придёт вам в Telegram):
pip install -e .
python -m teasender.tools.gen_key
python -m teasender.tools.login
python -m teasender.db.init
python -m teasender.tools.import_chats

docker compose up -d app       # запуск сервиса
docker compose logs -f app
```

## Первый запуск — безопасно

1. В `.env` держите `DRY_RUN=true`. Сервис спланирует публикации, но ничего не отправит.
2. В боте отметьте разрешённые чаты (по умолчанию все «не проверены» и не постятся) и задайте правила (`/rule`).
3. Понаблюдайте сутки: в «Статусе» видно, что запланировано.
4. Переключите `DRY_RUN=false`, перезапустите `app`. Начните с 3–5 чатов и 1 поста в день.

## Обновление схемы

```bash
alembic revision --autogenerate -m "change"
alembic upgrade head
```

## Резервные копии

Бэкапьте отдельно: `data/secret.key` (ключ) и дамп Postgres. Ключ и база —
раздельно; вместе они дают доступ к сессии аккаунта.
