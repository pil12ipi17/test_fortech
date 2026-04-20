# Backend-система управления задачами

Next-level реализация тестового задания на `FastAPI + PostgreSQL + Docker Compose`.

Проект теперь собран как интеграционный стенд из нескольких компонентов:
- `auth-service` — пользователи, роли, команды, JWT, refresh/logout, audit auth-действий
- `task-service` — задачи, lifecycle статусов, RBAC, фильтрация, идемпотентность, audit task-действий
- `frontend` — тонкий web-клиент для проверки API-сценариев
- `gateway (nginx)` — единая точка входа для frontend и backend
- `rabbitmq` — брокер сообщений и база для event-driven контура этапа 4
- `auth-db` и `task-db` — отдельные PostgreSQL базы данных

## Что реализовано

### Auth-service
- регистрация и логин
- `access_token + refresh_token`
- `POST /auth/refresh`
- `POST /auth/logout`
- `GET /auth/me`
- роли `user`, `teamlead`, `admin`
- команды и membership пользователей
- bootstrap первого администратора
- аудит auth/admin-операций

### Task-service
- CRUD задач
- расширенная модель задачи:
  - `owner_id`
  - `assignee_id`
  - `team_id`
  - `priority`
  - `deadline`
- lifecycle статусов:
  - `todo`
  - `in_progress`
  - `review`
  - `done`
  - `cancelled`
- отдельный endpoint смены статуса
- история переходов статусов
- RBAC для `owner / assignee / teamlead / admin`
- фильтрация, пагинация и сортировка списка задач
- идемпотентность для создания задачи и смены статуса
- аудит task-операций

### Frontend и интеграция
- мини frontend для проверки API
- логин пользователя
- список задач с фильтрами
- создание задачи
- изменение статуса задачи
- отображение ошибок API в понятном виде
- refresh flow на стороне клиента
- `nginx` как reverse proxy и единая точка входа
- маршрутизация `/ -> frontend`, `/api/v1/* -> backend`
- базовые security headers в gateway
- `.env.example` для общих переменных окружения

### Event-driven foundation
- `RabbitMQ` добавлен в локальный стенд как инфраструктурная основа этапа 4
- конфигурация подключения к брокеру вынесена в env
- `task-service` уже знает настройки `RabbitMQ exchange`, чтобы дальше можно было вводить outbox и publisher без пересборки конфигурационного слоя

## Архитектура

Стенд состоит из семи контейнеров:
- `auth-db`
- `task-db`
- `auth-service`
- `task-service`
- `rabbitmq`
- `frontend`
- `gateway`

### Поток запросов
1. Пользователь открывает браузер и попадает в `gateway`
2. `gateway` отдаёт frontend по маршруту `/`
3. frontend отправляет API-запросы на `/api/v1/*`
4. `gateway` проксирует:
   - `/api/v1/auth/*`, `/api/v1/users*`, `/api/v1/teams*` -> `auth-service`
   - `/api/v1/tasks*` -> `task-service`
5. `task-service` использует `access_token` с `roles` и `team_ids` для локальной RBAC-проверки

## Структура репозитория

```text
.
├── docker-compose.yml
├── .env.example
├── gateway/
│   └── nginx.conf
├── frontend/
│   ├── Dockerfile
│   ├── nginx.conf
│   └── static/
├── requirements/
├── shared/
├── services/
│   ├── auth_service/
│   └── task_service/
└── tests/
```

## Запуск через Docker Compose

Подготовить `.env`:

```bash
cp .env.example .env
```

Запуск стенда:

```bash
docker compose up --build -d
```

### Ожидаемые URL

Единая точка входа:
- `http://localhost/` — frontend
- `http://localhost/health` — gateway health
- `http://localhost/api/v1/health/auth` — readiness auth-service через gateway
- `http://localhost/api/v1/health/task` — readiness task-service через gateway

RabbitMQ:
- `amqp://localhost:5672` — AMQP-подключение
- `http://localhost:15672` — management UI

Прямой доступ к БД:
- `auth-db` -> `localhost:5433`
- `task-db` -> `localhost:5434`

## Базы данных

### Auth DB
- host: `localhost`
- port: `5433`
- db: `auth_db`
- user: `auth_user`
- password: `auth_password`

### Task DB
- host: `localhost`
- port: `5434`
- db: `task_db`
- user: `task_user`
- password: `task_password`

### RabbitMQ
- host: `localhost`
- AMQP port: `5672`
- management UI: `15672`
- user: `task_user`
- password: `task_password`
- vhost: `task-system`

## Миграции

У каждого сервиса свой Alembic-контур:
- `services/auth_service/alembic.ini`
- `services/task_service/alembic.ini`

При старте контейнеров сервисы автоматически выполняют:

```bash
alembic upgrade head
```

## Основные API маршруты через gateway

### Auth-service
- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `GET /api/v1/users`
- `POST /api/v1/users`
- `PATCH /api/v1/users/{user_id}/roles`
- `GET /api/v1/teams`
- `POST /api/v1/teams`
- `POST /api/v1/teams/{team_id}/members`
- `DELETE /api/v1/teams/{team_id}/members/{user_id}`

### Task-service
- `POST /api/v1/tasks`
- `GET /api/v1/tasks`
- `GET /api/v1/tasks/{task_id}`
- `PATCH /api/v1/tasks/{task_id}`
- `PATCH /api/v1/tasks/{task_id}/status`
- `GET /api/v1/tasks/{task_id}/history`
- `DELETE /api/v1/tasks/{task_id}`

## RBAC

Используются роли:
- `user`
- `teamlead`
- `admin`

Базовые правила:
- `user` работает со своими задачами и задачами, где он исполнитель
- `teamlead` видит и изменяет задачи своей команды
- `admin` управляет пользователями, ролями, командами и имеет полный доступ к задачам

## Lifecycle задач

Поддерживаемые статусы:
- `todo`
- `in_progress`
- `review`
- `done`
- `cancelled`

Поддерживаемые переходы:
- `todo -> in_progress`
- `todo -> cancelled`
- `in_progress -> review`
- `in_progress -> cancelled`
- `review -> in_progress`
- `review -> done`
- `review -> cancelled`

## Ручная проверка

Сценарий ручной проверки сохранён в:
- `docs/manual-test-scenario-next-level.md`

Он покрывает:
- auth flow
- роли и команды
- refresh/logout
- задачи, RBAC и lifecycle
- list API
- идемпотентность
- audit

## Тесты

Запуск локально через виртуальное окружение:

```bash
.\.venv\Scripts\python.exe -m pytest
```

Текущее покрытие проверяет:
- auth сценарии
- task RBAC
- list API
- идемпотентность
- audit
- readiness
- единый формат ошибок
