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

## Ограничения

В labels Prometheus не добавляются `user_id`, `task_id`, `event_id`, email, token и другие высококардинальные или чувствительные значения. Это важно, чтобы метрики не раздувались и не утекали персональные данные.

`correlation_id` используется в logs/traces, но не как label в Prometheus metrics.