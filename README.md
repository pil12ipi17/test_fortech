# Backend-система управления задачами

Next-level реализация тестового задания на `FastAPI + PostgreSQL + Docker Compose`.

Проект построен как система из двух независимых сервисов:

- `auth-service` отвечает за пользователей, роли, команды, JWT, refresh/logout и audit auth-действий
- `task-service` отвечает за задачи, lifecycle статусов, RBAC, фильтрацию списка, идемпотентность и audit task-действий

У каждого сервиса своя база данных и свой набор Alembic-миграций.

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

### Общая инфраструктура

- Docker Compose для запуска всего проекта
- PostgreSQL для каждого сервиса
- Alembic для эволюции схемы БД
- единый формат ошибок
- `health` и `readiness` endpoints
- интеграционные тесты ключевых сценариев

## Архитектура

Система состоит из четырёх контейнеров:

- `auth-db`
- `auth-service`
- `task-db`
- `task-service`

### Как сервисы взаимодействуют

1. Клиент проходит регистрацию или логин в `auth-service`
2. `auth-service` выдаёт `access_token` и `refresh_token`
3. В `access_token` кладутся:
   - `sub`
   - `email`
   - `roles`
   - `team_ids`
4. Клиент передаёт `access_token` в `task-service`
5. `task-service` локально валидирует токен и применяет RBAC без синхронного запроса в `auth-service`

Такой подход сохраняет слабую связанность между сервисами и делает `task-service` автономным при обработке бизнес-запросов.

## Структура репозитория

```text
.
├── docker-compose.yml
├── README.md
├── requirements/
├── shared/
├── services/
│   ├── auth_service/
│   │   ├── alembic/
│   │   └── app/
│   └── task_service/
│       ├── alembic/
│       └── app/
└── tests/
```

## Запуск через Docker Compose

```bash
docker compose up --build -d
```

После запуска будут доступны:

- `auth-service`: `http://localhost:8001`
- `auth-service docs`: `http://localhost:8001/docs`
- `task-service`: `http://localhost:8002`
- `task-service docs`: `http://localhost:8002/docs`

Дополнительно:

- `auth-service health`: `http://localhost:8001/health`
- `auth-service readiness`: `http://localhost:8001/readiness`
- `task-service health`: `http://localhost:8002/health`
- `task-service readiness`: `http://localhost:8002/readiness`

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

## Миграции

У каждого сервиса свой Alembic-контур:

- `services/auth_service/alembic.ini`
- `services/task_service/alembic.ini`

При старте контейнеров сервисы автоматически выполняют:

```bash
alembic upgrade head
```

При необходимости миграции можно запустить вручную.

### Auth-service

```bash
cd services/auth_service
alembic upgrade head
```

### Task-service

```bash
cd services/task_service
alembic upgrade head
```

## Основные endpoint

### Auth-service

- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/refresh`
- `POST /auth/logout`
- `GET /auth/me`
- `GET /users`
- `POST /users`
- `PATCH /users/{user_id}/roles`
- `GET /teams`
- `POST /teams`
- `POST /teams/{team_id}/members`
- `DELETE /teams/{team_id}/members/{user_id}`

### Task-service

- `POST /tasks`
- `GET /tasks`
- `GET /tasks/{task_id}`
- `PATCH /tasks/{task_id}`
- `PATCH /tasks/{task_id}/status`
- `GET /tasks/{task_id}/history`
- `DELETE /tasks/{task_id}`

## RBAC

Используются роли:

- `user`
- `teamlead`
- `admin`

### Базовые правила

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

`done` и `cancelled` считаются терминальными статусами.

## Фильтрация списка задач

`GET /tasks` поддерживает:

- `status`
- `priority`
- `deadline_from`
- `deadline_to`
- `assignee_id`
- `owner_id`
- `team_id`
- `page`
- `page_size`
- `sort_by`
- `sort_order`

Ответ списка возвращает:

- `items`
- `total`
- `page`
- `page_size`
- `pages`

## Идемпотентность

Для операций:

- `POST /tasks`
- `PATCH /tasks/{task_id}/status`

поддерживается заголовок:

```text
Idempotency-Key: <unique-value>
```

Если запрос с тем же ключом и тем же payload повторяется, сервис возвращает уже сохранённый результат. Если ключ reused с другим payload, возвращается `409 Conflict`.

## Аудит

Аудит распределён по сервисам:

- `auth-service` пишет auth/admin-события в свою таблицу `audit_log`
- `task-service` пишет task-события в свою таблицу `audit_log`

Примеры событий:

- `auth.register`
- `auth.login`
- `auth.refresh`
- `auth.logout`
- `user.created`
- `user.roles_updated`
- `team.created`
- `team.member_added`
- `task.created`
- `task.updated`
- `task.status_changed`
- `task.deleted`

## Формат ошибок

Оба сервиса используют единый формат ответа об ошибке:

```json
{
  "error": {
    "code": "forbidden",
    "message": "You do not have enough permissions to perform this action",
    "details": null
  }
}
```

## Тесты

Локальный запуск:

```bash
python -m pip install -r requirements/dev.txt
python -m pytest
```

Текущие тесты покрывают:

- auth flow
- refresh/logout
- роли и команды
- RBAC
- lifecycle задач
- list API
- идемпотентность
- аудит
- единый error format

## Ветки next-level задач

Для поэтапной демонстрации работа разложена по веткам:

- `task/alembic-foundation`
- `task/auth-roles-and-teams`
- `task/task-model-and-lifecycle`
- `task/task-rbac`
- `task/task-list-api`
- `task/idempotency`
- `task/audit-log`
- `task/error-format-and-polish`

## Что полезно показать наставнику

Если нужно быстро провести демо, удобная последовательность такая:

1. Поднять контейнеры через `docker compose up --build -d`
2. Открыть Swagger:
   - `http://localhost:8001/docs`
   - `http://localhost:8002/docs`
3. Показать:
   - регистрацию и логин
   - создание команды
   - назначение ролей
   - создание задачи
   - смену статуса
   - фильтрацию списка
   - idempotency
   - audit в БД через DBeaver

## Дополнительные материалы

- рабочий план next-level: `docs/next-level-working-plan.md`
- подробная локальная документация по файлам: `docs/file-by-file-next-level/`