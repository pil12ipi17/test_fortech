# Stage 8: Observability, SLI/SLO and alerts

Этот документ фиксирует, что именно мы наблюдаем в проекте, какие SLO считаем целевыми и какие алерты должны помочь быстро найти проблему.

## Что входит в observability

В текущем этапе добавлены три уровня наблюдаемости:

- Logs: сервисы пишут структурные сообщения с `correlation_id` и, где есть активный span, с `trace_id`.
- Metrics: Prometheus собирает HTTP-метрики API, метрики публикации outbox и метрики обработки событий worker-ами.
- Traces: OpenTelemetry протягивает trace через `POST /tasks`, создание outbox-события, публикацию в RabbitMQ и обработку downstream worker-ом.

Основные инструменты локального стенда:

- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000`
- Jaeger UI: `http://localhost:16686`
- RabbitMQ Management UI: `http://localhost:15672`

## SLI и SLO

### SLO 1: API availability

Цель: API availability >= 99.5% за 30 дней.

SLI:

```promql
1 - (
  sum(rate(http_requests_total{status_code=~"5.."}[5m]))
  /
  clamp_min(sum(rate(http_requests_total[5m])), 0.001)
)
```

Что считается плохим событием: любой HTTP 5xx от backend API.

Error budget за 30 дней: 0.5% запросов могут завершиться 5xx без нарушения SLO.

### SLO 2: create-task latency

Цель: 95% запросов `POST /tasks` должны завершаться быстрее 3 секунд.

SLI:

```promql
histogram_quantile(
  0.95,
  sum(rate(http_request_duration_seconds_bucket{service="task-service", method="POST", endpoint="/tasks"}[5m])) by (le)
)
```

Почему это важно: создание задачи является основным write-flow проекта. Если этот flow медленный, пользователь сразу ощущает деградацию.

### Дополнительный SLI: event pipeline success

Цель: >= 99% событий должны успешно обрабатываться worker-ами без ухода в DLQ.

SLI:

```promql
sum(rate(worker_events_total{result="processed"}[5m]))
/
clamp_min(sum(rate(worker_events_total{result=~"processed|retry|dlq|error"}[5m])), 0.001)
```

Этот SLI помогает видеть проблемы асинхронной части: retries, worker errors и DLQ.

## Alerts

Prometheus alert rules находятся в `deploy/observability/alerts.yml`.

Добавленные алерты:

- `ApiAvailabilityBelowSlo`: API availability ниже 99.5% в течение 10 минут.
- `HighApi5xxRate`: больше 5% API-запросов завершаются 5xx в течение 5 минут.
- `CreateTaskP95LatencyAboveSlo`: p95 latency для `POST /tasks` выше 3 секунд в течение 10 минут.
- `HighApiP95Latency`: p95 latency любого API endpoint выше 1 секунды в течение 10 минут.
- `EventPipelineSuccessBelowSlo`: успешность event pipeline ниже 99% в течение 10 минут.
- `ConsumerErrorsOrDlqGrowing`: появились worker errors или сообщения в DLQ.
- `WorkerHandlersSaturated`: worker держит 10 активных обработчиков в течение 10 минут.

## Dashboard

Grafana dashboard находится в `deploy/observability/grafana/dashboards/task-management-overview.json`.

Он показывает:

- API RPS.
- API availability SLI.
- API p95 latency.
- p95 latency для `POST /tasks`.
- API 5xx rate.
- Outbox publish throughput.
- Worker throughput.
- Worker p95 processing latency.
- Worker errors and DLQ.
- Worker active handlers.
- Event pipeline success SLI.
- Outbox publish p95 latency.

## Trace flow

Основной trace для создания задачи проходит так:

1. `task-service` принимает `POST /tasks` и создает HTTP server span.
2. Внутри API создается span `task.create.persist` для сохранения задачи.
3. Создается span `task.outbox_event.create` для записи outbox-события.
4. В event envelope сохраняется `trace_context`.
5. `outbox-publisher` читает outbox, продолжает trace и создает span `rabbitmq.publish`.
6. Worker получает сообщение из RabbitMQ, извлекает `traceparent` из headers и создает span `rabbitmq.consume`.
7. Внутри worker создается span `worker.handler` для бизнес-обработки события.

Для ручной проверки можно отправить запрос с заголовком:

```http
X-Correlation-ID: demo-stage-8-trace-1
```

После этого в Jaeger можно искать trace по сервису `task-service`, `outbox-publisher`, `enrichment-service`, `notification-worker` или `audit-worker`, а в логах сверять тот же `correlation_id`.


## Fault injection results

### Scenario 1: task database unavailable

Action:

```bash
docker compose stop task-db
```

Checks:

```bash
curl http://localhost/api/v1/health/task
```

Observed result:

- `task-service` process stayed alive.
- Readiness endpoint returned `503 Service Unavailable` because the database dependency was down.
- Prometheus `up{job="task-service"}` stayed `1`, because `/metrics` was still reachable and the process itself did not crash.
- This difference is important: `up` shows scrape availability, while readiness shows whether the service can perform business work with its dependencies.

Root cause signal:

- Readiness failure points to the `task-db` dependency.
- If business endpoints require PostgreSQL, traces/logs should show database connection failures around the affected request.

Recovery:

```bash
docker compose start task-db
```

After recovery, `GET /api/v1/health/task` returned `{"status":"ok","service":"task-service","database":"ok"}`.

### Scenario 2: RabbitMQ unavailable during task creation

Action:

```bash
docker compose stop rabbitmq
```

Then a task was created through the API with:

```http
X-Correlation-ID: fault-rabbitmq-down-1
```

Observed result:

- `POST /api/v1/tasks` still returned `201 Created`.
- The task was saved in PostgreSQL and the domain event was saved in `outbox_events`.
- RabbitMQ consumers logged reconnect attempts while the broker was unavailable.
- Prometheus temporarily showed `up{job="event-workers", instance="outbox-publisher:9100"}=0` when the publisher process was unavailable before the resilience fix.
- After RabbitMQ was restored, the outbox publisher published the pending `task.created` event.
- The event pipeline continued and produced/processed `task.enriched`.

Recovery:

```bash
docker compose up -d rabbitmq outbox-publisher enrichment-service notification-worker audit-worker
```

Observed recovery logs:

```text
published_event event_type=task.created correlation_id=fault-rabbitmq-down-1
processed_event event_type=task.created consumer=enrichment-service correlation_id=fault-rabbitmq-down-1
processed_event event_type=task.created consumer=notification-worker correlation_id=fault-rabbitmq-down-1
processed_event event_type=task.created consumer=audit-worker correlation_id=fault-rabbitmq-down-1
published_event event_type=task.enriched correlation_id=fault-rabbitmq-down-1
processed_event event_type=task.enriched consumer=notification-worker correlation_id=fault-rabbitmq-down-1
processed_event event_type=task.enriched consumer=audit-worker correlation_id=fault-rabbitmq-down-1
```

Prometheus recovery check:

```promql
up{job="event-workers"}
```

All worker metrics targets returned to `1` after recovery.

Useful throughput check:

```promql
sum(rate(outbox_events_published_total[5m])) by (event_type, result)
```

This showed successful publication for `task.created` and `task.enriched` after RabbitMQ was restored.

Jaeger recovery check:

Search by the `task-service` or `outbox-publisher` service and inspect the trace with `correlation_id=fault-rabbitmq-down-1`. The trace should show the asynchronous chain from `task-service` to `outbox-publisher`, `enrichment-service`, `notification-worker`, and `audit-worker`.

### Fix discovered during fault injection

During the first DB outage test, `outbox-publisher` could exit when PostgreSQL closed an existing connection. The publisher loop was updated so that one failed iteration is logged and retried after the normal poll interval instead of terminating the process.

This makes temporary dependency failures visible in logs/metrics while keeping the background process alive for recovery.

## Ограничения

В labels Prometheus не добавляются `user_id`, `task_id`, `event_id`, email, token и другие высококардинальные или чувствительные значения. Это важно, чтобы метрики не раздувались и не утекали персональные данные.

`correlation_id` используется в logs/traces, но не как label в Prometheus metrics.