# Backend-система управления задачами

MVP-реализация тестового задания на `FastAPI + PostgreSQL + Docker Compose`.

## Что внутри

- `auth-service` для регистрации, логина, refresh/logout и получения текущего пользователя
- `task-service` для CRUD задач
- отдельная база данных для каждого сервиса
- JWT-авторизация с access и refresh token
- Swagger UI у каждого сервиса
- базовое логирование запросов
- базовые тесты на ключевые сценарии

## Запуск

```bash
docker compose up --build
```

После запуска:

- auth-service: `http://localhost:8001`
- auth-service docs: `http://localhost:8001/docs`
- task-service: `http://localhost:8002`
- task-service docs: `http://localhost:8002/docs`

## Основные эндпоинты

### Auth service

- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/refresh`
- `POST /auth/logout`
- `GET /auth/me`
- `GET /health`

### Task service

- `POST /tasks`
- `GET /tasks`
- `GET /tasks/{task_id}`
- `PATCH /tasks/{task_id}`
- `DELETE /tasks/{task_id}`
- `GET /health`

## Как сервисы взаимодействуют

- `auth-service` выдаёт пару `access_token + refresh_token`
- клиент передаёт `access_token` в `Authorization: Bearer <token>`
- `task-service` принимает только access token и извлекает `user_id` из claim `sub`
- `refresh_token` хранится и инвалидируется в `auth-service`
- пользователь видит и изменяет только свои задачи

## Локальный запуск тестов

```bash
python -m pip install -r requirements/dev.txt
pytest
```

## Документация

- архитектура: `docs/architecture.md`
- структура БД: `docs/database.md`
