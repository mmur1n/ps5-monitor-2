# Cloudflare timer for PS5 Monitor

Worker вызывает только `workflow_dispatch` существующего GitHub workflow каждые 5 минут. Проверки Sony, история, публикация панели и Telegram остаются в GitHub. Worker не получает Telegram-токен и не читает сообщения.

Нагрузка: 288 запусков в сутки, один запрос GitHub на запуск. Этого достаточно для Workers Free (100 000 запросов/сутки, 10 мс CPU на вызов). Платный Workers план не требуется. Первое применение нового Cron Trigger может занять до 15 минут. Это отдельный таймер; очередь выполнения GitHub всё ещё может задержать начало проверки.

## Размещение через Cloudflare Dashboard

1. Workers & Pages → Create application → Create Worker / Start with Hello World. Имя: `ps5-monitor-2-scheduler`. Используйте бесплатный план.
2. Edit code → замените код содержимым `worker.mjs` → Deploy.
3. Settings → Variables and Secrets → добавьте **Secret** `GITHUB_WORKFLOW_TOKEN`. Секрет содержит fine-grained GitHub PAT с доступом **только** к `mmur1n/ps5-monitor-2`, Repository permissions → **Actions: Read and write** (Metadata Read — обязательное). Contents, Administration, доступ ко всем репозиториям и Telegram-токен не нужны. Токен создаёт владелец аккаунта; его не следует вставлять в исходники или переписку.
4. Settings → Triggers / Cron Triggers → Add → `*/5 * * * *` (UTC).
5. В GitHub Actions убедитесь в появлении именно `workflow_dispatch` и в `triggerSource: cloudflare` в `monitor-data/state.json`. Только после подтверждения отключите собственный `schedule` GitHub, чтобы избежать дублирующих запусков.

Публичные GET `/` и `/health` показывают, настроен ли секрет. Это не подтверждение работы расписания. HTTP-запросы никогда не запускают workflow. Логи не содержат токенов, заголовков авторизации или ответа GitHub. Истёкший токен приводит к ошибке вызова; его нужно заменить в Secrets Worker.

## Размещение через Wrangler

После авторизации в своём Cloudflare аккаунте:

```sh
cd scheduler
npx wrangler deploy
npx wrangler secret put GITHUB_WORKFLOW_TOKEN
```

Значение вводится в скрытом приглашении, не в командной строке. `wrangler.toml` содержит только имя, путь к коду и расписание. Любые локальные секреты и состояние Wrangler исключены из Git.

Проверки без облачных запросов: `node --test scheduler/worker.test.mjs`.

Официальные источники: [Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/), [Workers Free](https://developers.cloudflare.com/workers/platform/pricing/), [GitHub workflow dispatch](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event).
