# Отчёт: фаза 2 — FIFO и жизненный цикл передач

Статус: **Завершена**, 29 октября 2026 года.

Основание: [план фазы 2](../phase2-retention-and-transfer-lifecycle.md), SPEC,
[предыдущая фаза](phase1-report.md). История исследования и прежнего блокера —
[phase2-history.md](phase2-history.md).

Пользователь подтвердил: гарантий штатного удаления Deluge/libtorrent достаточно.
Не требуется замораживать native rename/move или писать собственное удаление
данных. Известные shared/unsafe paths всё равно проверяются. Matching native
deletion alert завершает операцию; ошибка/timeout диагностируются. Manifest после
crash не используется для самостоятельного unlink. Это изменение гарантий
зафиксировано в SPEC §4.2, AGENTS, README и плане фазы.

## Реализованное поведение

- `PolicyService.lock` сериализует смену политики, конфигурацию, допуск leases,
  ticket activation и начало удаления. Успешное закрепление раньше cleanup
  исключает экземпляр из FIFO; после начала удаления pin/admission отклоняются.
  Внешнее ручное действие через Deluge leases плагина не блокируют.
- Временные торренты считаются по SQLite policy, default лимит 3. Выбирается
  только минимальный FIFO-ключ; защищённый старейший не заменяется более новым.
  Триггеры — reconcile/startup, успешная mutation, config, recovery confirmation
  и release/expiry/debounce. Никаких size/TTL/free-space triggers.
- Единый registry: playback/preload/file/zip/ticket, global cap 128 и per-kind
  caps, idle timeouts, byte-progress, однократный release, ticket → file/ZIP без
  разрыва защиты, tokenized playback debounce. Старые record_id retired при
  reconcile; cancellation callbacks не переносятся на replacement.
- Research HTTP GET использует playback lease и продлевает его на записанных
  байтах; disconnect/ошибка/disable освобождают lease. HEAD не создаёт lease и
  не обновляет debounce. Во время catalogue reconciliation GET получает 503,
  а не обходит identity/admission. Полноценные stream/preload/download/ZIP
  endpoints остаются фазами 3–4; здесь их контракт проверяется fake-передачами.
- Worker проверяет текущие native paths (payload, pads и partfile), symlinks,
  пересечения и file identities, сохраняя ограниченный manifest. Перед native
  вызовом повторно сверяются catalogue/policy/FIFO/protection и native path list.
  Это preflight, не блокировка чужих queued disk operations.
- Durable journal schema 5: prepared → submitting → submitted → completed/manual.
  Intent фиксируется до native mutation. Удаление только через Deluge Core с
  данными. Ожидание matching-handle alert вне policy lock, deadline 30 с.
  После delete_failed/timeout/неоднозначного native submission очередь остаётся
  на manual resolution, без удаления более новых кандидатов.
- Известные preflight ошибки — не более 3 попыток за цикл с интервалом не меньше
  5 с. Новый policy/catalogue/release event может начать новый цикл. Таймеры и
  leases отменяются при shutdown. Ошибка cleanup не меняет успешный ответ mutation.
- После restart prepared безопасно abort; submitting/submitted — read-only
  проверка отсутствия native записи и сохранённых файлов, иначе manual. Никакого
  replay unlink. Recovery-required фазы 1 отдельно запрещает новую автоочистку.
- Admin RPC `get_cleanup(1)` и `resolve_removal(1, operation_id, action)`:
  `verify` только подтверждает отсутствие, `acknowledge` явно закрывает manual
  operation без файловых изменений. Завершение старой операции использует UUID,
  не hash replacement. Подробная семантика/риски — в README.
- Новые product TorrServer add handlers не реализуются этой фазой; существующие
  native торренты не перемещаются. Separate instance destinations для будущего
  plugin-added add остаются контрактом фазы 3. Runtime fixtures используют
  раздельные directories и отдельно создают конфликт shared paths для проверки.

## Файлы

| Файл | Изменение |
| --- | --- |
| `deluge_stream/transfers.py` | Leases/refcount/debounce/limits/ticket activation |
| `deluge_stream/retention.py` | FIFO coordinator, retry, native completion, recovery/manual resolution |
| `deluge_stream/deletion.py` | Worker preflight и read-only recovery verification |
| `deluge_stream/repository.py` | Migration 5 и removal journal, сохранение evidence при reset/reconcile |
| `deluge_stream/policy_adapter.py` | Native full paths, delete alerts/submit, deregistration |
| `deluge_stream/lifecycle.py` | Общий lock/admission/config/policy/startup/shutdown integration и status |
| `deluge_stream/core.py` | Cleanup admin RPC и research GET lease |
| `tests/unit/test_transfers.py`, `tests/unit/test_retention_scheduler.py` | Clock-driven registry и bounded scheduler |
| `tests/persistence/test_deletion.py` | Actual SQLite journal/UUID/reopen и filesystem preflight |
| `tests/persistence/test_retention.py`, `tests/persistence/retention_scenario.py` | Actual SQLite/reactor с fake-native deterministic barriers |
| `tests/persistence/service_scenario.py` | Fake adapter full-path contract |
| `tests/runtime/retention.py`, `tests/runtime/retention_crash.py` | Installed egg/native deletion/SIGKILL stages |
| `tests/runtime/probe.py` | GET active count, disconnect release, HEAD debounce invariance |
| `tests/runtime/native_deletion_probe.py` | Исторический ordering research, отдельно от обязательной suite |
| `tools/runtime_check.py` | Новые native suites в make check/CI |
| `README.md`, `SPEC.md`, `AGENTS.md`, `Makefile`, `tasks/README.md`, план и отчёты | Контракт, команды, статус и результаты |

## Исходный Definition of Done

- [x] Все типы передач используют общий допуск и защиту экземпляра.
  Internal API, native/fake tests всех 5 типов; research GET подключён. Будущие
  HTTP preload/file/ZIP handlers повторно проверяются в фазах 3–4, как в плане.
- [x] FIFO удаляет старейшего только при превышении количества и соблюдает защищённую голову очереди.
  Native retention на обеих архитектурах и controlled completion barriers.
- [x] Смена постоянства и старт передачи согласованы с удалением; нет double release и утечек защиты.
  Lock-queue barriers в обоих порядках, registry tests, native re-add/release.
- [x] Ошибки add/cleanup не смешиваются; retry ограничен и останавливается при shutdown.
  Scheduler/SQLite tests, native failure/manual stop и 20-cycle resource regression.
- [x] Shared paths и небезопасные пути не приводят к удалению чужих файлов.
  В согласованных границах штатных гарантий: preflight checks и native sentinels,
  без обещания isolation от внешних queued rename/move/re-add.
- [x] Crash/частичное удаление/внешнее удаление корректно согласуются с журналом и Deluge.
  Native partial failure/SIGKILL prepared/submitting/submitted, actual SQLite,
  external native re-add. Неоднозначность — manual, никакого orphan unlink.
- [x] Проверены A09–A15/A22 и относящаяся часть A26; ограничения fake-передач указаны.
  Матрица ниже отделяет native, fake и fault injection; не заявлены будущие HTTP/UI.
- [x] Makefile и checks.yml включают новые обязательные проверки; make check проходит.
  Runtime list входит в общие make-цели и существующий checks.yml; оба локальных
  full check Passed. Hosted run не выполнен, remote не настроен.
- [x] README описывает фактическую политику и восстановление; отчёт и индекс фаз обновлены.
  Лимит 3, FIFO, guards, retries, native guarantees, RPC verify/acknowledge и crash.

## Проверки и среда

Debian 12 Linux Docker ARM64; Python 3.11.2, Deluge 2.2.0, libtorrent 2.0.8.0,
Twisted 24.11.0, SQLite 3.40.1. x86_64 — тот же image contract под эмуляцией.
Изолированные profiles и local fixtures, `--network none`; профиль пользователя
не используется. Команды из корня проекта:

```sh
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make test-persistence lint VENV=/opt/venv
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make build VENV=/opt/venv
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 /opt/venv/bin/python -I /work/tests/runtime/retention.py --egg /work/dist/Stream-0.0.1-py3.11.egg
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 /opt/venv/bin/python -I /work/tests/runtime/retention_crash.py --egg /work/dist/Stream-0.0.1-py3.11.egg
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check VENV=/opt/venv
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 /opt/venv/bin/python -m pytest -q tests/persistence/test_retention.py
docker run --rm --platform linux/amd64 --network none -v "$PWD:/work" deluge-stream-phase0:amd64 make check VENV=/opt/venv
```

Targeted persistence/lint Passed (80 persistence до расширения содержимого barrier
scenario). Targeted native retention Passed: v1/v2/hybrid payload/pad/partfile,
protected oldest, 5 kinds, ticket conversion, debounce, shared paths и partial
failure с foreign sentinel. Native crash suite Passed для всех трёх cut points:
prepared abort, submitting manual, submitted verified absent; recovery_required
сохранился, foreign sentinel не затронут, orphan unlink не выполнялся.

Во время integration выявлена готовность catalogue: первый HTTP GET мог захватить
пустой record_id до reconciliation и корректно отклонялся coordinator-ом, но с
обрывом соединения. Исправлено явным 503 до GET admission и ожиданием policy ready
в runtime harness. Первые lint/format не прошли из-за форматирования новых tests;
исправлено.

Общий ARM64 `make check` **Passed**: 152 unit, 80 persistence, lint/format/docs,
build, все native integration (включая новые retention/crash и external re-add),
ZIP64 и отдельные daemon/web smoke. Leased GET/HEAD/disconnect прошли на local seed.
Все 20-cycle checks handlers/listeners/RPC/fields/DB threads/timers/shutdown hooks
прошли. После добавления disk-full fault injection расширенный retention_scenario
дополнительно проверен через targeted pytest: **1 passed** (внутри — все barrier
сценарии). Первый x86_64 check остановился на форматировании этой последней
test-only добавки; форматирование исправлено. Повторный полный x86_64 `make check`
**Passed**: 152 unit + 80 persistence, lint/format/docs, build, все native suites,
три SIGKILL cuts, ZIP64, daemon/core-web smoke и 20-cycle resource checks.
Production code не менялся после успешного ARM64 check; последняя source-test
добавка disk-full проверена отдельно на ARM64 и общим x86_64 check.

## Сценарии ТЗ

| ID | Проверенная часть и границы |
| --- | --- |
| A09 | Native count limit 2 → 1, FIFO порядок, удаление только своего native storage, permanent excluded |
| A10 | Все 5 видов leases через coordinator на native торрентах; fake ordered completion, debounce и ticket conversion; полноценные HTTP preload/file/ZIP остаются фазами 3–4 |
| A11 | Существующие phase-1 repeat-add combinations плюс regression с включённым cleanup |
| A12 | Phase-1 двусторонний policy lifecycle и deterministic pin-before-cleanup |
| A13 | Fake-native FIFO lock barriers в обоих порядках, реальная SQLite; GET использует тот же API, native disk scheduling не объявляется замороженным |
| A14 | UUID retirement, stale release и journal completion; native внешнее remove/re-add проверяется в расширенной suite |
| A15 | Actual SQLite evidence/reopen/disable, fake delete_failed/timeout, native partial failure, SIGKILL после committed prepared/submitting/submitted |
| A22 | Shared payloads и symlink/traversal/overlap preflight, pads/partfile coverage, foreign sentinels и read-only recovery; не stronger native filesystem isolation |
| A26 | Никаких free-space/size/TTL triggers. Fault-injected SQLite disk-full не допускает native mutation без intent; настоящий заполненный volume не создавался |

## README / Makefile / CI

README объясняет default 3, FIFO, защищённую голову, временное превышение,
счётчики запросов, native guarantees и manual-resolution RPC. Makefile help
обновлён; обязательный runtime list расширен retention и retention_crash.
Существующий checks.yml вызывает те же make-цели, поэтому YAML не дублирует логику
и менять его не требуется. Hosted run не запускался; remote не настроен. Релиз,
тег, публикация и установка в пользовательский Deluge не выполнялись. Полный
bootstrap чистого checkout заново не проверялся; используется подготовленный
runtime image, но egg устанавливается в новые disposable profiles.

## Ограничения и следующий шаг

Native external rename/move/re-add не блокируются; действуют согласованные
гарантии Deluge. Crash recovery может потребовать ручного решения — журнал не
обещает автоматического удаления orphan files. Нет новых TorrServer/download/UI
handlers или live QoS. Нельзя считать число HTTP-запросов числом зрителей.
Предыдущий блокер и пройденные до интеграции проверки сохранены в history.
Регрессия ARM64/x86_64 завершена. Следующая — фаза 3, TorrServer handlers и live
streaming/QoS. Она не начиналась и требует отдельной команды пользователя.
После окончательных документационных правок выполнены:

```sh
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check-docs VENV=/opt/venv
git diff --check
```

Passed: 24 Markdown-файла, local targets существуют, whitespace errors нет.
Runtime без новых code changes повторять не требуется.
