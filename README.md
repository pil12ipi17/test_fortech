# Backend-система управления задачами

Next-level реализация тестового задания на `FastAPI + PostgreSQL + Docker Compose`.

Проект теперь собран как интеграционный стенд из нескольких компонентов:
- `auth-service` — пользователи, роли, команды, JWT, refresh/logout, audit auth-действий
- `task-service` — задачи, lifecycle статусов, RBAC, фильтрация, идемпотентность, audit task-действий
- `frontend` — тонкий web-клиент для проверки API-сценариев
- `gateway (nginx)` — единая точка входа для frontend и backend
- `rabbitmq` — брокер сообщений и база для event-driven контура этапов 4-5
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
- `task-service` фиксирует доменные события в `outbox_events`
- отдельный `outbox-publisher` читает `pending` события из outbox и публикует их в `RabbitMQ`
- отдельные `notification-worker` и `audit-worker` читают `task.*` события из своих очередей и ведут независимую обработку с защитой от дублей

## Архитектура

The local stand consists of 12 containers:
- `auth-db`
- `task-db`
- `auth-service`
- `task-service`
- `rabbitmq`
- `outbox-publisher`
- `notification-worker`
- `notification-cron`
- `audit-worker`
- `audit-report-cron`
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

## Event-driven foundation (stage 4)

Сейчас в проекте уже подготовлен минимальный event-driven контур:
- `task-service` создаёт transactional outbox события:
  - `task.created`
  - `task.status_changed`
  - `task.deleted`
- события пишутся в таблицу `outbox_events` в той же транзакции, что и бизнес-изменения
- сервис `outbox-publisher` батчами читает `pending` записи из outbox и публикует их в exchange `tasks.events`
- `notification-worker` читает очередь `notifications.task-events` и фиксирует свою обработку в `worker_event_logs`
- `audit-worker` читает очередь `audit.task-events` и фиксирует свою обработку в `worker_event_logs`
- таблица `processed_events` защищает consumers от повторной обработки одного и того же `event_id`
- consumers используют bounded retry через header `x-retry-count`
- после превышения `TASK_WORKER_MAX_RETRY_ATTEMPTS` сообщение уходит в DLQ:
  - `notifications.task-events.dlq`
  - `audit.task-events.dlq`

Это ещё не полный этап 4, но уже закрывает ключевые фундаменты:
- RabbitMQ как часть стенда
- transactional outbox как защита от потери события после commit бизнес-данных
- outbox publisher
- отдельные consumers
- базовая идемпотентность consumers
- retry/DLQ для ошибок обработки

Следующий слой ещё впереди:
- более подробная наблюдаемость worker'ов


## Notifications and audit reporting (stage 5)

Stage 5 extends the RabbitMQ workers into a minimal notification and audit-reporting contour:
- `notification-worker` handles `task.created` and `task.status_changed` events.
- It builds a mock email subject/body and stores a `pending` delivery in `notification_deliveries`.
- `notification-cron` is a separate cron-like container that periodically dispatches pending/failed deliveries through `MockEmailSender`.
- `audit-worker` continues consuming task events and refreshes the CSV report after handled events.
- `audit-report-cron` is a separate cron-like container that periodically regenerates `reports/audit_report.csv`.
- Worker processing errors are stored in `worker_errors` and are included in the report as `errors_count`.
- `processed_events` still provides consumer idempotency, so repeated RabbitMQ delivery does not duplicate final worker effects.

The generated CSV report uses these columns:

```text
date,metric_name,metric_value,errors_count,notes
```

Main metrics:
- `task.created`
- `task.status_changed`
- `task.deleted`
- `auth.login`
- `worker.errors`

The report is generated at:

```text
reports/audit_report.csv
```

Manual notification dispatch inside Docker:

```bash
docker compose run --rm notification-cron python -m app.notification_cron --once
```

Manual report generation inside Docker:

```bash
docker compose run --rm audit-report-cron python -m app.audit_report_cron --once
```

Useful manual checks in `task_db`:

```sql
select event_id, event_type, recipient_email, subject, status, sent_at
from notification_deliveries
order by sent_at desc
limit 10;
```

```sql
select consumer_name, event_id, event_type, error_type, error_message, created_at
from worker_errors
order by created_at desc
limit 10;
```

```sql
select consumer_name, event_type, note, created_at
from worker_event_logs
order by created_at desc
limit 10;
```

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
