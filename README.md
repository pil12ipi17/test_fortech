# Backend-система управления задачами

Проект представляет собой учебную backend-систему управления задачами на `FastAPI`, разделенную на несколько сервисов и инфраструктурных компонентов. Локально система запускается через `Docker Compose` и включает backend, frontend, gateway, PostgreSQL, RabbitMQ, Redis и фоновые worker/cron-процессы.

Основной стек:
- `FastAPI` для backend-сервисов
- `PostgreSQL` для постоянного хранения данных
- `RabbitMQ` для event-driven обработки событий задач
- `Redis` для кэширования и rate limiting
- `Nginx` как gateway и frontend-сервер
- `Docker Compose` для локального стенда
- `pytest` для автотестов

## Состав системы

Локальный стенд состоит из следующих контейнеров:
- `auth-db` - PostgreSQL база пользователей, ролей, команд и auth-аудита
- `task-db` - PostgreSQL база задач, outbox, worker-логов, уведомлений и audit-данных
- `rabbitmq` - брокер сообщений для event-driven контура
- `redis` - кэш и хранилище счетчиков rate limit
- `auth-service` - сервис пользователей, авторизации, ролей и команд
- `task-service` - сервис задач, RBAC, outbox, кэша и rate limiting write-операций
- `outbox-publisher` - фоновый процесс публикации outbox-событий в RabbitMQ
- `notification-worker` - consumer событий задач для создания уведомлений
- `notification-cron` - cron-like процесс отправки pending-уведомлений
- `audit-worker` - consumer событий задач для обновления audit-отчета
- `audit-report-cron` - cron-like процесс периодической генерации CSV-отчета
- `frontend` - легкий web-интерфейс для ручной проверки API
- `gateway` - Nginx reverse proxy, единая точка входа в приложение

## Архитектура запросов

Основной пользовательский поток:
1. Пользователь открывает `http://localhost/`.
2. Запрос попадает в `gateway`.
3. `gateway` отдает frontend или проксирует API-запросы в backend.
4. Auth-запросы идут в `auth-service`.
5. Запросы задач идут в `task-service`.
6. `auth-service` работает со своей PostgreSQL базой `auth-db`.
7. `task-service` работает со своей PostgreSQL базой `task-db`.
8. `task-service` использует Redis для кэша чтения задач и лимитов write-запросов.
9. После бизнес-изменений задач `task-service` пишет события в transactional outbox.
10. `outbox-publisher` публикует события из outbox в RabbitMQ.
11. `notification-worker` и `audit-worker` независимо обрабатывают события из RabbitMQ.

Маршрутизация через gateway:
- `/` -> `frontend`
- `/api/v1/auth/*` -> `auth-service`
- `/api/v1/users*` -> `auth-service`
- `/api/v1/teams*` -> `auth-service`
- `/api/v1/tasks*` -> `task-service`
- `/health` -> healthcheck gateway
- `/api/v1/health/auth` -> readiness auth-service
- `/api/v1/health/task` -> readiness task-service

## Auth-service

`auth-service` отвечает за пользователей, роли, команды и авторизацию.

Реализовано:
- регистрация пользователя
- login по email/password
- выдача `access_token` и `refresh_token`
- refresh access token
- logout через отзыв refresh token
- `GET /auth/me`
- роли `user`, `teamlead`, `admin`
- bootstrap первого администратора
- создание пользователей администратором
- обновление ролей пользователя
- создание команд
- добавление и удаление пользователей из команд
- RBAC для admin/teamlead/user сценариев
- audit auth/admin-действий
- rate limiting для `POST /auth/login` через Redis

Rate limit логина:
- ключ Redis: `rl:login:{ip}:{email}`
- лимит по умолчанию: `5` попыток за `60` секунд
- при превышении возвращается `429 Too Many Requests`
- ответ содержит заголовок `Retry-After`
- если Redis недоступен, login продолжает работать без rate limit

## Task-service

`task-service` отвечает за задачи, жизненный цикл задач, RBAC, audit, outbox, кэширование и rate limiting write-операций.

Реализовано:
- создание задачи
- получение списка задач
- получение задачи по id
- обновление задачи
- смена статуса задачи
- получение истории статусов задачи
- удаление задачи
- RBAC для `owner`, `assignee`, `teamlead`, `admin`
- фильтрация, пагинация и сортировка списка задач
- идемпотентность создания задачи через `Idempotency-Key`
- идемпотентность смены статуса через `Idempotency-Key`
- audit task-действий
- transactional outbox для событий задач
- Redis-кэш для `GET /tasks/{task_id}`
- инвалидация кэша после write-операций
- rate limiting для write-операций задач

Поддерживаемые статусы задач:
- `todo`
- `in_progress`
- `review`
- `done`
- `cancelled`

Поддерживаемые переходы статусов:
- `todo -> in_progress`
- `todo -> cancelled`
- `in_progress -> review`
- `in_progress -> cancelled`
- `review -> in_progress`
- `review -> done`
- `review -> cancelled`

## Event-driven контур RabbitMQ

Для задач реализован event-driven контур на базе RabbitMQ и transactional outbox.

Основной поток:
1. `task-service` выполняет бизнес-операцию с задачей.
2. В той же транзакции создается запись в `outbox_events`.
3. `outbox-publisher` периодически читает `pending` события из outbox.
4. Publisher публикует событие в RabbitMQ exchange `tasks.events`.
5. RabbitMQ доставляет событие в очереди consumers.
6. `notification-worker` и `audit-worker` обрабатывают событие независимо друг от друга.
7. Worker-ы фиксируют обработку в `processed_events` и `worker_event_logs`.

События задач:
- `task.created`
- `task.status_changed`
- `task.deleted`

RabbitMQ настройки:
- AMQP URL: `amqp://task_user:task_password@rabbitmq:5672/task-system`
- exchange: `tasks.events`
- management UI: `http://localhost:15672`
- user: `task_user`
- password: `task_password`
- vhost: `task-system`

Надежность обработки:
- transactional outbox защищает от потери события после commit бизнес-данных
- `processed_events` защищает consumers от повторной обработки одного и того же события
- worker-ы используют retry с backoff
- после превышения лимита retry сообщение уходит в DLQ
- ошибки worker-ов сохраняются в `worker_errors`

## Уведомления и audit-отчет

Stage 5 расширяет RabbitMQ worker foundation уведомлениями и отчетностью.

Уведомления:
- `notification-worker` читает события `task.created` и `task.status_changed`
- worker формирует mock email subject/body
- запись создается в таблице `notification_deliveries` со статусом `pending`
- `notification-cron` периодически выбирает pending/failed уведомления
- отправка выполняется через `MockEmailSender`
- успешные отправки получают статус `success`
- ошибки отправки фиксируются в `worker_errors`

Audit reporting:
- `audit-worker` обновляет CSV-отчет после обработки task events
- `audit-report-cron` периодически пересобирает отчет через APScheduler
- интервал задается через `TASK_AUDIT_REPORT_INTERVAL_SECONDS`
- дефолтный интервал: `3600` секунд
- ручной запуск доступен через `--once`

CSV-отчет создается здесь:

```text
reports/audit_report.csv
```

Основные метрики отчета:
- `task.created`
- `task.status_changed`
- `task.deleted`
- `auth.login`
- `worker.errors`

Ручной запуск отправки уведомлений:

```bash
docker compose run --rm notification-cron python -m services.notification_service.app.cron --once
```

Ручная генерация audit-отчета:

```bash
docker compose run --rm audit-report-cron python -m services.audit_service.app.report_cron --once
```

## Redis-кэширование и rate limiting

Redis добавлен как дополнительный инфраструктурный компонент. Он используется для временных быстрых данных, но не является источником правды. Основные данные остаются в PostgreSQL.

Реализовано:
- контейнер `redis` в `docker-compose.yml`
- healthcheck Redis через `redis-cli ping`
- volume `redis_data`
- Redis client для `auth-service`
- Redis client для `task-service`
- degraded mode: если Redis недоступен, API продолжает работать через PostgreSQL
- кэширование `GET /api/v1/tasks/{task_id}`
- инвалидация task cache после create/update/status/delete
- rate limiting `POST /api/v1/auth/login`
- rate limiting task write operations

Кэш задач:
- endpoint: `GET /api/v1/tasks/{task_id}`
- ключи: `tasks:item:*`
- ключ учитывает `user_id`, роли, команды и `task_id`
- TTL задается через `TASK_TASK_CACHE_TTL_SECONDS`
- дефолтный TTL: `90` секунд
- после успешной write-операции удаляются ключи `tasks:*`

Rate limiting:
- login key: `rl:login:{ip}:{email}`
- task write key: `rl:tasks:write:{user_id}:{ip}`
- login default: `5` запросов за `60` секунд
- task write default: `30` запросов за `60` секунд
- при превышении лимита API возвращает `429 Too Many Requests`
- ответ содержит `Retry-After`

## API endpoints

Auth-service через gateway:
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

Task-service через gateway:
- `POST /api/v1/tasks`
- `GET /api/v1/tasks`
- `GET /api/v1/tasks/{task_id}`
- `PATCH /api/v1/tasks/{task_id}`
- `PATCH /api/v1/tasks/{task_id}/status`
- `GET /api/v1/tasks/{task_id}/history`
- `DELETE /api/v1/tasks/{task_id}`

Health endpoints:
- `GET /health`
- `GET /api/v1/health/auth`
- `GET /api/v1/health/task`

## Запуск проекта

Подготовить `.env`:

```bash
cp .env.example .env
```

Запустить стенд:

```bash
docker compose up --build -d
```

Проверить контейнеры:

```bash
docker compose ps
```

Остановить стенд:

```bash
docker compose down
```

Остановить стенд с удалением volume-данных:

```bash
docker compose down -v
```

## Доступные URL

Frontend и gateway:
- `http://localhost/` - frontend
- `http://localhost/health` - gateway health
- `http://localhost/api/v1/health/auth` - readiness auth-service
- `http://localhost/api/v1/health/task` - readiness task-service

RabbitMQ:
- `amqp://localhost:5672` - AMQP
- `http://localhost:15672` - management UI
- user: `task_user`
- password: `task_password`

Redis:
- host внутри Docker: `redis`
- port внутри Docker: `6379`
- host с локальной машины: `localhost`
- port с локальной машины: `6379`

PostgreSQL:
- `auth-db` доступен на `localhost:5433`
- `task-db` доступен на `localhost:5434`

## Переменные окружения

Основные переменные:

```env
JWT_SHARED_SECRET=change-me-in-production
RABBITMQ_DEFAULT_USER=task_user
RABBITMQ_DEFAULT_PASS=task_password
RABBITMQ_DEFAULT_VHOST=task-system
TASK_RABBITMQ_URL=amqp://task_user:task_password@rabbitmq:5672/task-system
TASK_RABBITMQ_TASKS_EXCHANGE=tasks.events
TASK_OUTBOX_PUBLISH_BATCH_SIZE=50
TASK_OUTBOX_PUBLISH_POLL_INTERVAL_SECONDS=2
TASK_WORKER_MAX_RETRY_ATTEMPTS=3
TASK_WORKER_RETRY_BACKOFF_BASE_SECONDS=1
TASK_WORKER_RETRY_BACKOFF_MAX_SECONDS=10
TASK_AUTH_DATABASE_URL=postgresql+psycopg://auth_user:auth_password@auth-db:5432/auth_db
TASK_AUDIT_REPORT_PATH=/app/reports/audit_report.csv
TASK_AUDIT_REPORT_INTERVAL_SECONDS=3600
TASK_NOTIFICATION_DISPATCH_INTERVAL_SECONDS=60
TASK_NOTIFICATION_DISPATCH_BATCH_SIZE=50
AUTH_REDIS_URL=redis://redis:6379/0
AUTH_LOGIN_RATE_LIMIT_REQUESTS=5
AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS=60
TASK_REDIS_URL=redis://redis:6379/0
TASK_TASK_CACHE_TTL_SECONDS=90
TASK_TASK_WRITE_RATE_LIMIT_REQUESTS=30
TASK_TASK_WRITE_RATE_LIMIT_WINDOW_SECONDS=60
```

## Базы данных

Auth DB:
- host: `localhost`
- port: `5433`
- database: `auth_db`
- user: `auth_user`
- password: `auth_password`

Task DB:
- host: `localhost`
- port: `5434`
- database: `task_db`
- user: `task_user`
- password: `task_password`

При старте сервисы автоматически применяют Alembic migrations:

```bash
alembic upgrade head
```

## Структура репозитория

```text
.
|-- docker-compose.yml
|-- .env.example
|-- README.md
|-- gateway/
|   `-- nginx.conf
|-- frontend/
|   |-- Dockerfile
|   |-- nginx.conf
|   `-- static/
|-- requirements/
|   `-- base.txt
|-- shared/
|   |-- errors.py
|   `-- rate_limit.py
|-- services/
|   |-- auth_service/
|   `-- task_service/
|       `-- app/
|           |-- api/
|           |-- audit/
|           |-- cache/
|           |-- core/
|           |-- messaging/
|           |-- notifications/
|           `-- tasks/
|-- reports/
`-- tests/
```

## Ручная проверка

Проверить Redis:

```bash
docker compose exec redis redis-cli ping
```

Ожидаемый ответ:

```text
PONG
```

Проверить ключи task cache:

```bash
docker compose exec redis redis-cli --scan --pattern "tasks:*"
```

Проверить cache miss/cache hit:
1. Запустить стенд.
2. Залогиниться и получить access token.
3. Создать задачу или выбрать существующую видимую задачу.
4. Два раза вызвать `GET /api/v1/tasks/{task_id}` с одним и тем же токеном.
5. Проверить логи:

```bash
docker compose logs task-service | grep -E "cache_miss|cache_hit|cache_invalidate"
```

Проверить инвалидацию:
1. Вызвать `GET /api/v1/tasks/{task_id}`, чтобы задача попала в Redis.
2. Выполнить `PATCH /api/v1/tasks/{task_id}`.
3. Проверить, что ключи `tasks:*` удалились.

Проверить login rate limit:
1. Отправить больше 5 неверных login-запросов за 60 секунд.
2. Проверить, что следующие ответы возвращают `429 Too Many Requests`.
3. Проверить наличие заголовка `Retry-After`.

Проверить task write rate limit:
1. Для быстрой проверки временно уменьшить `TASK_TASK_WRITE_RATE_LIMIT_REQUESTS` в `.env`.
2. Перезапустить `task-service`.
3. Отправить больше write-запросов, чем разрешено лимитом.
4. Проверить `429 Too Many Requests` и `Retry-After`.

Проверить RabbitMQ UI:
1. Открыть `http://localhost:15672`.
2. Войти под `task_user / task_password`.
3. Проверить exchange `tasks.events`.
4. Проверить очереди `notifications.task-events`, `audit.task-events` и DLQ-очереди.

Проверить уведомления в `task_db`:

```sql
select event_id, event_type, recipient_email, subject, status, sent_at
from notification_deliveries
order by sent_at desc
limit 10;
```

Проверить ошибки worker-ов:

```sql
select consumer_name, event_id, event_type, error_type, error_message, created_at
from worker_errors
order by created_at desc
limit 10;
```

Проверить worker logs:

```sql
select consumer_name, event_type, note, created_at
from worker_event_logs
order by created_at desc
limit 10;
```

## Stage 7: event chain и ручная проверка

Stage 7 расширяет RabbitMQ-контур до полноценной цепочки событий. API по-прежнему не ждет фоновые процессы: `task-service` только фиксирует бизнес-изменение и пишет событие в `outbox_events`, а дальнейшая обработка идет асинхронно через RabbitMQ, worker-ы и cron-процессы.

Основная цепочка:
1. `task-service` создает или меняет задачу и пишет `task.created` или `task.status_changed` в `outbox_events`.
2. `outbox-publisher` публикует pending-события в RabbitMQ exchange `tasks.events`.
3. `enrichment-service` читает `task.created` и `task.status_changed`, добавляет metadata и пишет новое событие `task.enriched` в outbox.
4. `outbox-publisher` публикует `task.enriched` в RabbitMQ.
5. `notification-worker` читает `task.enriched` и создает pending-запись в `notification_deliveries`.
6. `notification-cron` отправляет pending-уведомление через `MockEmailSender` и после успешной отправки пишет `notification.sent` в outbox.
7. `audit-worker` слушает `task.*` и `notification.*`, фиксирует обработку в worker-таблицах и обновляет CSV-отчет.

Контракты событий используют общий envelope:
- `event_id` - уникальный id события
- `event_type` - тип события, например `task.created`, `task.enriched`, `notification.sent`
- `version` - версия контракта события
- `occurred_at` - время создания события
- `producer` - сервис, который создал событие
- `correlation_id` - общий id цепочки для трассировки
- `payload` - бизнес-данные события

Новые события stage 7:
- `task.enriched` - результат обработки исходного task-события в `enrichment-service`
- `notification.sent` - факт успешной отправки уведомления через `notification-cron`

Новые и обновленные компоненты:
- `enrichment-service` - consumer для `task.created` и `task.status_changed`
- `notification-worker` - теперь слушает `task.enriched`, а не сырые `task.*`
- `notification-cron` - после успешной отправки публикует `notification.sent` через outbox
- `audit-worker` - слушает `task.*` и `notification.*`
- `GET /api/v1/events/metrics` - admin-only endpoint с метриками event pipeline

Ручная проверка stage 7:
1. Запустить стенд:

```bash
docker compose up --build -d
```

2. Открыть приложение:

```text
http://localhost/
```

3. Залогиниться под пользователем с доступом к задачам и создать новую задачу через frontend или API.

4. Подождать несколько секунд, чтобы `outbox-publisher`, `enrichment-service`, `notification-worker`, `notification-cron` и `audit-worker` успели обработать цепочку.

5. Проверить в `task_db`, что появились события цепочки:

```sql
select event_type, status, producer, correlation_id, created_at, published_at
from outbox_events
where event_type in ('task.created', 'task.status_changed', 'task.enriched', 'notification.sent')
order by created_at desc
limit 20;
```

Ожидаемый результат: для одной цепочки должны быть видны исходное task-событие, затем `task.enriched`, затем `notification.sent`. У связанных событий должен совпадать `correlation_id`.

6. Проверить enrichment-результат:

```sql
select source_event_type, task_id, correlation_id, metadata_json, created_at
from task_enrichments
order by created_at desc
limit 10;
```

7. Проверить уведомления:

```sql
select event_type, task_id, recipient_email, status, correlation_id, sent_at
from notification_deliveries
order by sent_at desc nulls last
limit 10;
```

Ожидаемый результат: после работы `notification-cron` статус должен стать `success`, а `sent_at` должен быть заполнен.

8. Если не хочется ждать cron-интервал, можно запустить отправку уведомлений вручную:

```bash
docker compose run --rm notification-cron python -m services.notification_service.app.cron --once
```

9. Проверить worker trace:

```sql
select consumer_name, event_type, correlation_id, note, created_at
from worker_event_logs
order by created_at desc
limit 20;
```

10. Проверить idempotency consumers:

```sql
select consumer_name, event_type, correlation_id, processed_at
from processed_events
order by processed_at desc
limit 20;
```

11. Проверить ошибки и retry/DLQ-следы:

```sql
select consumer_name, event_type, correlation_id, error_type, error_message, retry_count, created_at
from worker_errors
order by created_at desc
limit 20;
```

12. Проверить метрики pipeline через API. Нужен access token admin-пользователя:

```bash
curl -H "Authorization: Bearer <ADMIN_ACCESS_TOKEN>" http://localhost/api/v1/events/metrics
```

Endpoint возвращает:
- `outbox.by_status`
- `outbox.by_event_type`
- `outbox.pending_lag_seconds`
- `workers.processed_by_consumer`
- `workers.logs_by_event_type`
- `workers.errors_by_consumer`
- `workers.retry_count`
- `workers.dlq_approx_count`

13. Проверить CSV audit report:

```bash
docker compose run --rm audit-report-cron python -m services.audit_service.app.report_cron --once
```

Файл создается или обновляется здесь:

```text
reports/audit_report.csv
```

В отчете должны быть метрики не только `task.created`, `task.status_changed`, `task.deleted`, но и stage 7 события `task.enriched` и `notification.sent`.
## Тесты

Запуск тестов:

```bash
.\.venv\Scripts\python.exe -m pytest
```

Текущее покрытие проверяет:
- auth flow
- роли и команды
- refresh/logout
- task RBAC
- фильтрацию, пагинацию и сортировку задач
- идемпотентность создания задачи
- идемпотентность смены статуса
- transactional outbox
- worker idempotency
- notification flow
- audit CSV report
- Redis cache invalidation
- login rate limit
- единый формат ошибок
- readiness endpoints

Актуальный локальный результат:

```text
10 passed
```
